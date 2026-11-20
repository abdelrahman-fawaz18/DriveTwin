"""Build from public pins and run an isolated Montreal mission qualification."""

import argparse
import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.name == "nt":
        parser.error("Run through scripts/linux.py")
    output = args.evidence_dir.resolve()
    if not output.is_relative_to(ROOT / "evidence/p0") or output.exists():
        parser.error("Select a new evidence directory under evidence/p0")
    output.mkdir(parents=True)
    token = uuid.uuid4().hex
    name = "evdt-p006-" + token
    record = {
        "passed": False,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "prepare" if args.prepare else "qualify",
        "commands": [],
    }
    inputs = [
        *ROOT.glob("simulation/montreal/*"),
        *ROOT.glob("data/public/montreal-core-v1/*"),
        ROOT / "scripts/platform/montreal_probe.py",
        ROOT / "scripts/fetch_map.py",
        Path(__file__).resolve(),
        ROOT / "containers/sumo.Dockerfile",
        ROOT / "containers/sumo-debian.sources",
        ROOT / "simulation/reference/pyproject.toml",
        ROOT / "simulation/reference/uv.lock",
        ROOT / "scripts/platform/sumo_probe.py",
        ROOT / "environments/dataflow-images.json",
        ROOT / ".dockerignore",
    ]

    def fingerprints():
        return {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in inputs
            if p.is_file()
        }

    before = fingerprints()

    def run(command, timeout=180):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        record["commands"].append({"argv": command, "exit": result.returncode})
        with (output / "commands.log").open("a") as log:
            log.write(result.stdout + result.stderr + "\n")
        if result.returncode:
            raise RuntimeError("Command failed; inspect commands.log and probe.json")
        return result.stdout

    try:
        receipt = json.loads((ROOT / "data/public/montreal-core-v1/source.json").read_text())
        if before[receipt["artifact"]] != receipt["artifact_sha256"]:
            raise RuntimeError("Reviewed map fingerprint mismatch")
        pins = json.loads((ROOT / "environments/dataflow-images.json").read_text())
        print("Building the pinned SUMO image (cached layers may be reused)", flush=True)
        run(
            [
                "docker",
                "build",
                "--target",
                "simulation",
                "--platform",
                "linux/amd64",
                "--build-arg",
                "PYTHON_IMAGE=" + pins["python"]["digest"],
                "--build-arg",
                "UV_IMAGE=" + pins["uv"]["digest"],
                "-f",
                "containers/sumo.Dockerfile",
                "-t",
                name,
                ".",
            ],
            timeout=900,
        )
        record["image_id"] = run(
            ["docker", "image", "inspect", name, "--format", "{{.Id}}"]
        ).strip()
        print(
            "Preparing mission" if args.prepare else "Running the Montreal mission twice",
            flush=True,
        )
        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--label",
            "com.evdt.scope=p0-montreal",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "512m",
            "--cpus",
            "2",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:size=64m,mode=1777",
            "-v",
            str(ROOT / "simulation/montreal") + ":/scenario:ro",
            "-v",
            str(ROOT / receipt["artifact"]) + ":/input/roads.osm.xml:ro",
            "-v",
            str(ROOT / "scripts/platform/montreal_probe.py") + ":/probe.py:ro",
            "-v",
            str(output) + ":/output",
            "--entrypoint",
            "python",
            name,
            "/probe.py",
        ]
        if args.prepare:
            command.append("--prepare")
        run(command, timeout=240)
        record["probe"] = json.loads((output / "probe.json").read_text())
        record["passed"] = record["probe"]["passed"] is True
    except Exception as exc:
        record["error"] = str(exc)
    finally:
        record["owned_container_remaining"] = run(
            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"]
        ).strip()
        record["source_sha256"] = before
        if record["owned_container_remaining"] or fingerprints() != before:
            record["passed"] = False
            record["error"] = "Owned container remains or source changed during qualification"
        (output / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"Evidence: {output.relative_to(ROOT)} passed: {record['passed']}", flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
