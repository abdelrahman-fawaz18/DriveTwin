"""Verify the Ubuntu engine's normal service lifecycle and volume persistence.

Run from Windows with --report PATH. Only unique test-labelled resources are
created/removed. Service restart is refused if any containers are running.
This qualifies an engine prerequisite, not the complete P0-02 application gate.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/linux.py"
IMAGE_LOCK = ROOT / "environments/reference-container-images.json"
PROJECT_LABEL = "com.evdt.project"
RUN_LABEL = "com.evdt.run"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    output = args.report.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("Save evidence inside the project.")
    token = uuid.uuid4().hex
    volume = "evdt_p0_engine_" + token[:16]
    record = {
        "client_date": "2026-11-03", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Ubuntu systemd Docker Engine prerequisite; full P0-02 still pending",
        "run_id": token, "commands": [], "cycles": [], "passed": False,
    }

    def run(command: list[str], *, root=False, timeout=60, check=True):
        if root:
            invocation = ["wsl.exe", "--distribution", "Ubuntu-24.04", "--user", "root", "--exec", *command]
        else:
            invocation = [sys.executable, str(LAUNCHER), "--", *command]
        started = time.monotonic()
        result = subprocess.run(invocation, cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=timeout)
        record["commands"].append({
            "command": command, "ubuntu_root": root, "exit_code": result.returncode,
            "seconds": round(time.monotonic()-started, 3),
            "stdout": result.stdout.strip(), "stderr": result.stderr.strip(),
        })
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed: {command!r}: {result.stderr.strip()}")
        return result

    def docker(*arguments, **kwargs):
        return run(["docker", *arguments], **kwargs)

    def assert_target():
        info = json.loads(docker("info", "--format", "{{json .}}").stdout)
        if not info["OperatingSystem"].startswith("Ubuntu") or info["DockerRootDir"] != "/var/lib/docker":
            raise RuntimeError("Refusing to test a different engine target.")
        return {key: info[key] for key in ["ID", "ServerVersion", "OperatingSystem", "DockerRootDir", "Driver"]}

    created = False
    try:
        identity = assert_target()
        record["engine"] = identity
        record["service_enabled"] = run(["systemctl", "is-enabled", "docker.service"]).stdout.strip()
        if IMAGE_LOCK.exists():
            image = json.loads(IMAGE_LOCK.read_text())
            docker("pull", "--platform", "linux/amd64", image["busybox"]["digest"], timeout=120)
        else:
            tag = "busybox:1.37.0"
            docker("pull", "--platform", "linux/amd64", tag, timeout=120)
            inspection = json.loads(docker("image", "inspect", tag).stdout)[0]
            digest = next(item for item in inspection["RepoDigests"] if item.startswith("busybox@sha256:"))
            image = {"busybox": {
                "discovery_tag": tag, "digest": digest, "image_id": inspection["Id"],
                "platform": "linux/amd64", "source": "https://hub.docker.com/_/busybox",
            }}
            IMAGE_LOCK.write_text(json.dumps(image, indent=2) + "\n")
        image_ref = image["busybox"]["digest"]
        record["image"] = image["busybox"]
        docker("volume", "create", "--label", PROJECT_LABEL+"=ev-dt",
               "--label", RUN_LABEL+"="+token, volume)
        created = True
        base = ["run", "--rm", "--read-only", "--network", "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--label", PROJECT_LABEL+"=ev-dt",
                "--label", RUN_LABEL+"="+token]
        for cycle in (1, 2):
            expected = {"fixture": "engine-persistence", "run_id": token, "cycle": cycle}
            payload = json.dumps(expected, sort_keys=True)
            docker(*base, "--name", f"evdt-engine-{token[:12]}-write-{cycle}",
                   "--mount", f"type=volume,src={volume},dst=/evidence", image_ref,
                   "sh", "-c", 'printf "%s" "$1" > /evidence/check.json; sync', "sh", payload)
            # No running container, including unrelated work, may be stopped by this test.
            if docker("ps", "-q").stdout.strip():
                raise RuntimeError("Running containers found; refusing engine restart.")
            stop_start = time.monotonic()
            run(["systemctl", "stop", "docker.service", "docker.socket"], root=True)
            stopped = run(["systemctl", "is-active", "docker.service"], check=False)
            if stopped.returncode == 0:
                raise RuntimeError("Engine did not stop.")
            run(["systemctl", "start", "docker.service"], root=True)
            deadline = time.monotonic() + 30
            while True:
                probe = docker("version", "--format", "{{.Server.Version}}", check=False, timeout=10)
                if probe.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Engine not ready after normal service restart.")
                time.sleep(.5)
            after = assert_target()
            if after["ID"] != identity["ID"]:
                raise RuntimeError("Engine identity changed across restart.")
            result = docker(*base, "--name", f"evdt-engine-{token[:12]}-read-{cycle}",
                            "--mount", f"type=volume,src={volume},dst=/evidence,readonly",
                            image_ref, "cat", "/evidence/check.json")
            if json.loads(result.stdout) != expected:
                raise RuntimeError("Volume data did not survive normal engine restart.")
            record["cycles"].append({"cycle": cycle, "passed": True, "expected_data": expected,
                                      "stop_start_seconds": round(time.monotonic()-stop_start, 3)})
        record["passed"] = True
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        record["error"] = str(error)
    finally:
        if created:
            try:
                current = assert_target()
                if current["ID"] != record["engine"]["ID"]:
                    raise RuntimeError("Engine changed before cleanup.")
                meta = json.loads(docker("volume", "inspect", volume).stdout)[0]
                if meta["Name"] != volume or meta["Labels"].get(PROJECT_LABEL) != "ev-dt" or meta["Labels"].get(RUN_LABEL) != token:
                    raise RuntimeError("Volume identity/ownership check failed; cleanup refused.")
                docker("volume", "rm", volume)
                record["test_volume_removed"] = True
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                record["cleanup_error"] = str(error)
                record["passed"] = False
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": record["passed"], "cycles": record["cycles"],
                          "error": record.get("error"), "cleanup_error": record.get("cleanup_error"),
                          "report": str(output)}, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
