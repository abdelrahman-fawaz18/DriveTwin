"""Run inside Ubuntu via scripts/linux.py. Qualify a pinned, isolated SUMO installation."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "environments/dataflow-images.json"
DOCKERFILE = "containers/sumo.Dockerfile"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("lock", "format", "qualify"))
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    if os.name == "nt":
        parser.error("Use python scripts/linux.py -- python3 scripts/platform/sumo.py ...")
    evidence = args.evidence_dir.resolve() if args.evidence_dir else None
    if args.action == "qualify" and evidence is None:
        parser.error("Qualification requires a new --evidence-dir under evidence/p0")
    if evidence and (not evidence.is_relative_to(ROOT / "evidence/p0") or evidence.exists()):
        parser.error("Evidence must be a new directory under evidence/p0")
    token = uuid.uuid4().hex
    runtime = ROOT / ".runtime/p0-sumo" / token
    output = runtime / "output"
    output.mkdir(parents=True)
    images = json.loads(IMAGES.read_text())
    container = "evdt-p0-sumo-" + token
    image = "evdt-p0-sumo:" + token
    record = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "scope": "P0-02 headless SUMO/TraCI subset",
        "run_id": token,
        "images": images,
        "commands": [],
        "container": container,
    }
    log = (runtime / "commands.log").open("w")

    def run(argv, *, timeout=120, check=True):
        start = time.monotonic()
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        log.write(json.dumps(argv) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        record["commands"].append(
            {
                "argv": argv,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - start, 3),
            }
        )
        if check and result.returncode:
            raise RuntimeError(
                f"Command failed: {argv!r}\n{result.stdout[-1600:]}\n{result.stderr[-1600:]}"
            )
        return result

    def docker(*argv, **kwargs):
        return run(["docker", *argv], **kwargs)

    try:
        info = json.loads(docker("info", "--format", "{{json .}}").stdout)
        if (
            not info["OperatingSystem"].startswith("Ubuntu")
            or info["DockerRootDir"] != "/var/lib/docker"
        ):
            raise RuntimeError("Unexpected Docker Engine target")
        record["engine"] = {k: info[k] for k in ("ID", "ServerVersion", "OperatingSystem")}
        build_args = [
            "--build-arg",
            "UV_IMAGE=" + images["uv"]["digest"],
            "--build-arg",
            "PYTHON_IMAGE=" + images["python"]["digest"],
        ]
        tools = (
            "evdt-p0-sumo-tools:"
            + hashlib.sha256(json.dumps(images, sort_keys=True).encode()).hexdigest()[:16]
        )
        print("Building pinned SUMO tools environment", flush=True)
        docker(
            "build",
            "--target",
            "tools",
            *build_args,
            "-f",
            DOCKERFILE,
            "-t",
            tools,
            ".",
            timeout=300,
        )
        mounted = [
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "UV_CACHE_DIR=/tmp/uv-cache",
            "-e",
            "UV_PROJECT_ENVIRONMENT=/tmp/venv",
            "-v",
            str(ROOT) + ":/work",
            "-w",
            "/work",
            tools,
            "uv",
        ]
        if args.action == "lock":
            docker(
                *mounted,
                "lock",
                "--project",
                "simulation/reference",
                "--python",
                "/usr/local/bin/python",
                timeout=300,
            )
            return 0
        if args.action == "format":
            for command in (["check", "--fix"], ["format"]):
                docker(
                    *mounted,
                    "run",
                    "--locked",
                    "--project",
                    "/work",
                    "ruff",
                    *command,
                    "scripts/platform/sumo.py",
                    "scripts/platform/sumo_probe.py",
                    timeout=180,
                )
            return 0
        record["code_checks"] = {}
        for label, command in (
            ("lint", ["check", "--no-cache"]),
            ("format", ["format", "--check", "--no-cache"]),
        ):
            record["code_checks"][label] = docker(
                *mounted,
                "run",
                "--locked",
                "--project",
                "/work",
                "ruff",
                *command,
                "scripts/platform/sumo.py",
                "scripts/platform/sumo_probe.py",
                timeout=180,
            ).stdout.strip()
        print("Building SUMO 1.28.0 and matching Python libraries", flush=True)
        docker(
            "build",
            "--target",
            "simulation",
            *build_args,
            "-f",
            DOCKERFILE,
            "-t",
            image,
            ".",
            timeout=600,
        )
        record["image_id"] = json.loads(docker("image", "inspect", image).stdout)[0]["Id"]
        print("Running the road scenario twice through TraCI", flush=True)
        probe = docker(
            "run",
            "--rm",
            "--name",
            container,
            "--label",
            "com.evdt.scope=p0-sumo",
            "--label",
            "com.evdt.run=" + token,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:size=64m",
            "--mount",
            f"type=bind,source={output},target=/output",
            image,
            timeout=120,
            check=False,
        )
        if (output / "probe.json").exists():
            record["probe"] = json.loads((output / "probe.json").read_text())
        if probe.returncode or not record.get("probe", {}).get("passed"):
            raise RuntimeError(
                "SUMO verification failed; inspect probe.json and container/command logs"
            )
        record["passed"] = True
    except BaseException as exc:
        record["error"] = repr(exc)
        print(record["error"], flush=True)
    finally:
        if args.action == "qualify":
            try:
                current = json.loads(docker("info", "--format", "{{json .}}").stdout)
                if current["ID"] != record.get("engine", {}).get("ID"):
                    raise RuntimeError("Engine identity changed; refusing cleanup")
                inspected = docker("container", "inspect", container, check=False)
                if inspected.returncode == 0:
                    data = json.loads(inspected.stdout)[0]
                    if (
                        data["Config"]["Labels"].get("com.evdt.run") != token
                        or data["Config"]["Labels"].get("com.evdt.scope") != "p0-sumo"
                    ):
                        raise RuntimeError("Container ownership mismatch; refusing removal")
                    docker("rm", "--force", container)
                remaining = docker(
                    "ps", "-aq", "--filter", "label=com.evdt.run=" + token
                ).stdout.strip()
                if remaining:
                    raise RuntimeError("Test container remains")
                record["cleanup"] = {
                    "owned_containers_remaining": remaining,
                    "created_named_volumes": False,
                }
            except BaseException as exc:
                record["cleanup_error"] = repr(exc)
                record["passed"] = False
        log.close()
        if evidence:
            evidence.mkdir()
            shutil.copytree(output, evidence / "artifacts")
            shutil.copyfile(runtime / "commands.log", evidence / "commands.log")
            paths = [
                *ROOT.glob("simulation/reference/*"),
                ROOT / DOCKERFILE,
                ROOT / "containers/sumo-debian.sources",
                ROOT / "pyproject.toml",
                ROOT / "uv.lock",
                Path(__file__).resolve(),
                ROOT / "scripts/platform/sumo_probe.py",
                IMAGES,
                ROOT / ".dockerignore",
            ]
            record["source_sha256"] = {
                str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in sorted(paths)
                if p.is_file()
            }
            (evidence / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
            print("Evidence:", evidence.relative_to(ROOT), "passed:", record["passed"], flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
