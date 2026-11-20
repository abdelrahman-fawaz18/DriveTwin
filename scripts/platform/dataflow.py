"""Build/check/qualify the isolated P0 pipeline inside Ubuntu, through the launcher.

This owns only a unique Compose project. No engine stop, system prune, or removal
of unrelated resources is performed. Engine cold startup is a separate check.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "environments/dataflow-images.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["lock", "format", "qualify"])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--port", type=int, default=18760)
    args = parser.parse_args()
    if os.name == "nt":
        parser.error("Use python scripts/linux.py -- python3 scripts/platform/dataflow.py ...")
    report_path = args.report.resolve() if args.report else None
    if report_path and (
        not report_path.is_relative_to(ROOT / "evidence/p0") or report_path.exists()
    ):
        parser.error("Use a new report path under evidence/p0; preserve previous evidence.")
    if args.action == "qualify" and report_path is None:
        parser.error("Qualification requires --report")
    token = uuid.uuid4().hex
    project = "evdt-p0-dataflow-" + token[:12]
    runtime = ROOT / ".runtime/p0-dataflow" / token
    runtime.mkdir(parents=True)
    images = json.loads(LOCK.read_text())
    record = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "P0 telemetry/store/query subset, not full P0-02 or operational guarantees",
        "project": project,
        "run_id": str(uuid.UUID(token)),
        "commands": [],
        "passed": False,
        "images": images,
    }
    log = (runtime / "commands.log").open("w")

    def run(argv, *, timeout=120, check=True):
        started = time.monotonic()
        result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        log.write(json.dumps(argv) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        record["commands"].append(
            {
                "argv": argv,
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        if check and result.returncode:
            raise RuntimeError(
                f"Failed: {argv!r}\n{result.stdout[-2500:]}\n{result.stderr[-2500:]}"
            )
        return result.stdout.strip()

    def docker(*argv, **kwargs):
        return run(["docker", *argv], **kwargs)

    build_args = [
        "--build-arg",
        "UV_IMAGE=" + images["uv"]["digest"],
        "--build-arg",
        "PYTHON_IMAGE=" + images["python"]["digest"],
    ]
    compose_base = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "--env-file",
        str(runtime / "compose.env"),
        "-f",
        str(ROOT / "ops/compose/compose.dataflow.yaml"),
        "-p",
        project,
    ]

    def compose(*argv, **kwargs):
        return run([*compose_base, *argv], **kwargs)

    started_fixture = False
    try:
        info = json.loads(docker("info", "--format", "{{json .}}"))
        if (
            not info["OperatingSystem"].startswith("Ubuntu")
            or info["DockerRootDir"] != "/var/lib/docker"
        ):
            raise RuntimeError("Unexpected Docker engine")
        record["engine"] = {k: info[k] for k in ("ID", "OperatingSystem", "ServerVersion")}
        tools = (
            "evdt-p0-tools:"
            + hashlib.sha256(json.dumps(images, sort_keys=True).encode()).hexdigest()[:16]
        )
        print("Building pinned Python/uv tooling image", flush=True)
        docker(
            "build",
            "--target",
            "tools",
            *build_args,
            "-f",
            "containers/dataflow.Dockerfile",
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
            "UV_PROJECT_ENVIRONMENT=/tmp/evdt-venv",
            "-v",
            str(ROOT) + ":/work",
            "-w",
            "/work",
            tools,
            "uv",
        ]
        if args.action == "lock":
            print(docker(*mounted, "lock", "--python", "/usr/local/bin/python", timeout=300))
            return 0
        if args.action == "format":
            docker(
                *mounted,
                "run",
                "--locked",
                "ruff",
                "check",
                "--fix",
                "src",
                "tests/unit",
                "scripts/platform/dataflow.py",
                "scripts/platform/lock_dataflow_images.py",
                timeout=180,
            )
            docker(
                *mounted,
                "run",
                "--locked",
                "ruff",
                "format",
                "src",
                "tests/unit",
                "scripts/platform/dataflow.py",
                "scripts/platform/lock_dataflow_images.py",
                timeout=180,
            )
            return 0
        app_image = "evdt-p0-dataflow:" + token
        checks_image = "evdt-p0-checks:" + token
        print("Building locked application and development checks", flush=True)
        for target, tag in (("runtime", app_image), ("checks", checks_image)):
            docker(
                "build",
                "--target",
                target,
                *build_args,
                "-f",
                "containers/dataflow.Dockerfile",
                "-t",
                tag,
                ".",
                timeout=300,
            )
        record["unit_tests"] = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:size=64m",
            checks_image,
            timeout=60,
        )
        record["lint"] = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            checks_image,
            "ruff",
            "check",
            "--no-cache",
            "src",
            "tests/unit",
            "scripts/platform/dataflow.py",
            "scripts/platform/lock_dataflow_images.py",
            timeout=60,
        )
        record["format"] = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            checks_image,
            "ruff",
            "format",
            "--check",
            "--no-cache",
            "src",
            "tests/unit",
            "scripts/platform/dataflow.py",
            "scripts/platform/lock_dataflow_images.py",
        )
        record["versions"] = docker(
            "run",
            "--rm",
            "--network",
            "none",
            checks_image,
            "python",
            "-c",
            "import sys,json,importlib.metadata as m; print(json.dumps({'python':sys.version,"
            "'packages':{n:m.version(n) for n in ['fastapi','uvicorn','paho-mqtt','psycopg',"
            "'jsonschema','pytest','ruff']}}))",
        )
        password = runtime / "db-password"
        password.write_text(secrets.token_urlsafe(32) + "\n")
        password.chmod(0o600)
        env = {
            "APP_IMAGE": app_image,
            "POSTGRES_IMAGE": images["postgres"]["digest"],
            "MOSQUITTO_IMAGE": images["mosquitto"]["digest"],
            "EVDT_RUN_ID": record["run_id"],
            "API_PORT": str(args.port),
            "PASSWORD_PATH": str(password),
        }
        (runtime / "compose.env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        # Validate configuration without printing secrets or the rendered environment.
        compose("config", "--quiet")
        record["cycles"] = []
        for cycle in (1, 2):
            print(f"Starting application cycle {cycle} (engine already running)", flush=True)
            started = time.monotonic()
            started_fixture = True
            compose("up", "-d", "--wait", "--wait-timeout", "90", timeout=120)
            deadline = time.monotonic() + 20
            while '"subscription_ready"' not in compose("logs", "--no-color", "ingest"):
                if time.monotonic() > deadline:
                    raise RuntimeError("Ingestion subscription was not ready")
                time.sleep(0.2)
            result = {"cycle": cycle, "startup_seconds": round(time.monotonic() - started, 3)}
            if cycle == 2:
                result["persisted_after_recreate"] = json.loads(
                    compose(
                        "run",
                        "--rm",
                        "--no-deps",
                        "probe",
                        "python",
                        "-m",
                        "evdt.probe",
                        "--batch",
                        "1",
                        "--verify-only",
                        timeout=60,
                    )
                )
            result["probe"] = json.loads(
                compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "probe",
                    "python",
                    "-m",
                    "evdt.probe",
                    "--batch",
                    str(cycle),
                    timeout=60,
                )
            )
            record["cycles"].append(result)
            ids = compose("ps", "-q").split()
            record[f"cycle_{cycle}_stats"] = docker(
                "stats", "--no-stream", "--format", "{{json .}}", *ids
            )
            record[f"cycle_{cycle}_logs"] = compose("logs", "--no-color", "--tail", "100")
            if cycle == 1:
                compose("down", "--timeout", "15", timeout=60)  # preserve named volumes
        record["passed"] = True
    except BaseException as exc:
        record["error"] = repr(exc)
        print(record["error"], flush=True)
    finally:
        if started_fixture:
            try:
                if not record["passed"]:
                    record["failure_logs"] = compose(
                        "logs", "--no-color", "--tail", "100", check=False
                    )
                # Compose ownership plus explicit label/engine checks before deleting volumes.
                current = json.loads(docker("info", "--format", "{{json .}}"))
                if current["ID"] != record["engine"]["ID"]:
                    raise RuntimeError("Engine identity changed; refusing cleanup")
                for name in (project + "_database", project + "_broker"):
                    inspected = docker("volume", "inspect", name, check=False)
                    if inspected:
                        labels = json.loads(inspected)[0]["Labels"]
                        if (
                            labels.get("com.docker.compose.project") != project
                            or labels.get("com.evdt.scope") != "p0-dataflow"
                        ):
                            raise RuntimeError("Volume ownership mismatch; refusing cleanup")
                compose("down", "--volumes", "--timeout", "15", timeout=60)
                record["cleanup"] = {
                    "project_containers": docker(
                        "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project
                    ),
                    "project_volumes": docker(
                        "volume",
                        "ls",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + project,
                    ),
                }
                if any(record["cleanup"].values()):
                    raise RuntimeError("Owned resources remain")
            except BaseException as exc:
                record["cleanup_error"] = repr(exc)
                record["passed"] = False
        log.close()
        if report_path:
            record["source_sha256"] = {
                str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(
                    [
                        *ROOT.glob("src/**/*.py"),
                        *ROOT.glob("contracts/**/*.json"),
                        *ROOT.glob("catalog/*.json"),
                        *ROOT.glob("migrations/*.sql"),
                        *ROOT.glob("tests/unit/*.py"),
                        ROOT / "scripts/privacy_guard.py",
                        ROOT / "ops/compose/compose.dataflow.yaml",
                        ROOT / "containers/dataflow.Dockerfile",
                        ROOT / "containers/mosquitto.conf",
                        ROOT / "uv.lock",
                        ROOT / "pyproject.toml",
                        ROOT / ".dockerignore",
                        LOCK,
                        ROOT / "scripts/platform/lock_dataflow_images.py",
                        Path(__file__).resolve(),
                    ]
                )
            }
            report_path.write_text(json.dumps(record, indent=2) + "\n")
            print("Report:", report_path.relative_to(ROOT), "passed:", record["passed"], flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
