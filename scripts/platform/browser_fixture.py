"""Hold an owned data-flow fixture ready while Windows runs real browser checks."""

import argparse
import hashlib
import json
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--dataflow-report",
        type=Path,
        default=Path("evidence/p0/2026-11-03-dataflow-requalification-p004.json"),
    )
    args = parser.parse_args()
    token = uuid.UUID(args.run_id).hex
    if token != args.run_id or not 1024 <= args.port <= 65535:
        parser.error("Use a canonical hex run ID and a nonprivileged port")
    runtime = ROOT / ".runtime/p0-browser" / token
    runtime.mkdir(exist_ok=True, parents=True)
    project = "evdt-p0-browser-" + token[:12]
    record = {
        "passed": False,
        "project": project,
        "run_id": str(uuid.UUID(token)),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "commands": [],
    }
    log = (runtime / "linux-commands.log").open("w")

    def run(argv, *, check=True, timeout=120):
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        record["commands"].append({"argv": argv, "exit_code": result.returncode})
        log.write(json.dumps(argv) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        if check and result.returncode:
            raise RuntimeError(f"Command failed: {argv!r}: {result.stderr[-1200:]}")
        return result

    def docker(*argv, **kwargs):
        return run(["docker", *argv], **kwargs)

    compose_base = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "--env-file",
        str(runtime / "compose.env"),
        "-f",
        str(ROOT / "ops/compose/compose.dataflow.yaml"),
        "-f",
        str(ROOT / "ops/compose/compose.browser-fixture.yaml"),
        "-p",
        project,
    ]

    def compose(*argv, **kwargs):
        return run([*compose_base, *argv], **kwargs)

    started = False
    try:
        prerequisite = (ROOT / args.dataflow_report).resolve()
        if not prerequisite.is_relative_to(ROOT / "evidence/p0"):
            raise RuntimeError("Prerequisite must be project-local evidence")
        qualified = json.loads(prerequisite.read_text())
        record["dataflow_prerequisite"] = str(prerequisite.relative_to(ROOT))
        if not qualified["passed"]:
            raise RuntimeError("Data-flow prerequisite is not passed")
        for name, digest in qualified["source_sha256"].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"Previously qualified source changed: {name}")
        info = json.loads(docker("info", "--format", "{{json .}}").stdout)
        if (
            not info["OperatingSystem"].startswith("Ubuntu")
            or info["DockerRootDir"] != "/var/lib/docker"
        ):
            raise RuntimeError("Unexpected engine")
        record["engine"] = {k: info[k] for k in ("ID", "OperatingSystem", "ServerVersion")}
        images = json.loads((ROOT / "environments/dataflow-images.json").read_text())
        image = "evdt-p0-browser:" + token
        docker(
            "build",
            "--target",
            "runtime",
            "--build-arg",
            "UV_IMAGE=" + images["uv"]["digest"],
            "--build-arg",
            "PYTHON_IMAGE=" + images["python"]["digest"],
            "-f",
            "containers/dataflow.Dockerfile",
            "-t",
            image,
            ".",
            timeout=300,
        )
        record["image_id"] = json.loads(docker("image", "inspect", image).stdout)[0]["Id"]
        password = runtime / "db-password"
        password.write_text(secrets.token_urlsafe(32) + "\n")
        password.chmod(0o600)
        env = {
            "APP_IMAGE": image,
            "POSTGRES_IMAGE": images["postgres"]["digest"],
            "MOSQUITTO_IMAGE": images["mosquitto"]["digest"],
            "EVDT_RUN_ID": record["run_id"],
            "API_PORT": str(args.port),
            "PASSWORD_PATH": str(password),
        }
        (runtime / "compose.env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        compose("config", "--quiet")
        started = True
        compose("up", "-d", "--wait", "--wait-timeout", "90")
        deadline = time.monotonic() + 20
        while '"subscription_ready"' not in compose("logs", "--no-color", "ingest").stdout:
            if time.monotonic() > deadline:
                raise RuntimeError("Ingest subscription not ready")
            time.sleep(0.2)
        record["seed_probe"] = json.loads(
            compose(
                "run", "--rm", "--no-deps", "probe", "python", "-m", "evdt.probe", "--batch", "1"
            ).stdout
        )
        ready = {
            "ready": True,
            "run_id": record["run_id"],
            "project": project,
            "port": args.port,
            "expected_unique_observations": 3,
        }
        try:
            with urlopen(f"http://127.0.0.1:{args.port}/health/ready", timeout=3) as response:
                ready["linux_host_http"] = {"passed": True, "body": json.load(response)}
        except OSError as exc:
            ready["linux_host_http"] = {"passed": False, "error": repr(exc)}
        ready["api_port_mapping"] = compose("port", "api", "8000", check=False).stdout.strip()
        ready["linux_listener"] = run(
            ["ss", "-ltn", "sport", "=", f":{args.port}"], check=False
        ).stdout
        record["readiness"] = ready
        tmp = runtime / "ready.json.tmp"
        tmp.write_text(json.dumps(ready) + "\n")
        tmp.replace(runtime / "ready.json")
        # A foreground process keeps WSL active through the Windows browser checks.
        deadline = time.monotonic() + 300
        while not (runtime / "stop").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Browser fixture hold expired")
            time.sleep(0.2)
        record["passed"] = True
    except BaseException as exc:
        record["error"] = repr(exc)
    finally:
        if started:
            try:
                record["service_logs"] = compose(
                    "logs", "--no-color", "--tail", "100", check=False
                ).stdout
                current = json.loads(docker("info", "--format", "{{json .}}").stdout)
                if current["ID"] != record["engine"]["ID"]:
                    raise RuntimeError("Engine identity changed; refusing cleanup")
                for name in (project + "_database", project + "_broker"):
                    inspected = docker("volume", "inspect", name, check=False)
                    if inspected.returncode == 0:
                        labels = json.loads(inspected.stdout)[0]["Labels"]
                        if (
                            labels.get("com.docker.compose.project") != project
                            or labels.get("com.evdt.scope") != "p0-dataflow"
                        ):
                            raise RuntimeError("Volume ownership mismatch")
                compose("down", "--volumes", "--timeout", "10", timeout=60)
                record["cleanup"] = {
                    "containers_remaining": docker(
                        "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project
                    ).stdout.strip(),
                    "volumes_remaining": docker(
                        "volume",
                        "ls",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + project,
                    ).stdout.strip(),
                    "networks_remaining": docker(
                        "network",
                        "ls",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + project,
                    ).stdout.strip(),
                }
                if any(record["cleanup"].values()):
                    raise RuntimeError("Owned resources remain")
            except BaseException as exc:
                record["cleanup_error"] = repr(exc)
                record["passed"] = False
        log.close()
        (runtime / "fixture-result.json").write_text(json.dumps(record, indent=2) + "\n")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
