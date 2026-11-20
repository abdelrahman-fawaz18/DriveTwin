"""Windows-host P0-03 qualification, coordinating a foreground Ubuntu fixture."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "qualification/browser"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18763)
    parser.add_argument(
        "--dataflow-report",
        type=Path,
        default=Path("evidence/p0/2026-11-03-dataflow-requalification-p004.json"),
    )
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if sys.platform != "win32":
        parser.error("Run from Windows; the Ubuntu fixture uses the project launcher")
    if not evidence.is_relative_to(ROOT / "evidence/p0") or evidence.exists():
        parser.error("Use a new evidence directory under evidence/p0")
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a nonprivileged port")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", args.port))
    token = uuid.uuid4().hex
    runtime = ROOT / ".runtime/p0-browser" / token
    runtime.mkdir(parents=True)
    browser_output = runtime / "browser-output"
    browser_output.mkdir()
    temporary = runtime / "tmp"
    temporary.mkdir()
    record = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "gate": "P0-03",
        "windows_platform": sys.platform,
        "python": sys.version,
        "run_id": str(uuid.UUID(token)),
        "commands": [],
        "base_url": f"http://127.0.0.1:{args.port}",
    }
    log = (runtime / "windows-commands.log").open("w", encoding="utf-8")
    env = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".runtime/playwright-browsers"),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "EVDT_BROWSER_OUTPUT": str(browser_output),
        "EVDT_BASE_URL": record["base_url"],
        "EVDT_EXPECTED_RUN": record["run_id"],
    }
    worker = None
    worker_log = (runtime / "linux-worker.log").open("w", encoding="utf-8")

    def run(argv, *, cwd=ROOT, timeout=180, check=True):
        start = time.monotonic()
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        record["commands"].append(
            {
                "argv": [str(a) for a in argv],
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - start, 3),
            }
        )
        log.write(json.dumps([str(a) for a in argv]) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        if check and result.returncode:
            raise RuntimeError(
                f"Command failed: {argv!r}: {result.stdout[-1200:]} {result.stderr[-1200:]}"
            )
        return result

    try:
        node, npm = shutil.which("node.exe"), shutil.which("npm.cmd")
        if not node or not npm:
            raise RuntimeError("Windows Node.js/npm missing")
        npm_cli = Path(npm).parent / "node_modules/npm/bin/npm-cli.js"
        if not npm_cli.is_file():
            raise RuntimeError("Cannot locate the installed npm JavaScript entry point")
        record["node"] = run([node, "--version"]).stdout.strip()
        if not record["node"].startswith("v24."):
            raise RuntimeError("Qualify Node.js 24 before using a different major")
        record["npm"] = run([node, str(npm_cli), "--version"]).stdout.strip()
        print("Installing locked browser-test dependencies", flush=True)
        run(
            [
                node,
                str(npm_cli),
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--cache",
                str(ROOT / ".runtime/npm-cache"),
            ],
            cwd=PACKAGE,
        )
        cli = PACKAGE / "node_modules/playwright/cli.js"
        print("Preparing the pinned Chromium browser", flush=True)
        run([node, str(cli), "install", "chromium", "--no-shell"], cwd=PACKAGE, timeout=300)
        record["browser_registry"] = json.loads(
            (PACKAGE / "node_modules/playwright-core/browsers.json").read_text()
        )
        print("Starting the owned Ubuntu data-flow fixture", flush=True)
        worker = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts/linux.py"),
                "--timeout",
                "900",
                "--",
                "python3",
                "scripts/platform/browser_fixture.py",
                "--run-id",
                token,
                "--port",
                str(args.port),
                "--dataflow-report",
                str(args.dataflow_report).replace("\\", "/"),
            ],
            cwd=ROOT,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 180
        while not (runtime / "ready.json").exists():
            if worker.poll() is not None:
                raise RuntimeError(
                    "Ubuntu fixture exited before readiness; inspect fixture-result.json"
                )
            if time.monotonic() > deadline:
                raise RuntimeError("Ubuntu fixture readiness timed out")
            time.sleep(0.2)
        ready = json.loads((runtime / "ready.json").read_text())
        record["fixture_readiness"] = ready
        if not ready["linux_host_http"]["passed"]:
            raise RuntimeError("Ubuntu host cannot reach the published API port")
        if not ready["ready"] or ready["run_id"] != record["run_id"]:
            raise RuntimeError("Fixture identity mismatch")
        # Windows-native HTTP confirms the host route before either browser runs.
        deadline = time.monotonic() + 30
        record["windows_http_attempt_errors"] = []
        while True:
            try:
                with urlopen(
                    record["base_url"] + "/v1/observations?asset_id=bus-001", timeout=3
                ) as response:
                    host_readings = json.load(response)
                if host_readings["run_id"] != record["run_id"] or len(host_readings["items"]) != 3:
                    raise RuntimeError("Wrong service/run at the Windows loopback address")
                record["windows_http_readings"] = host_readings
                break
            except OSError as exc:
                record["windows_http_attempt_errors"].append(repr(exc))
                if time.monotonic() > deadline:
                    raise RuntimeError("Windows cannot reach the ready Ubuntu fixture")
                time.sleep(0.3)
        print("Running visible Windows Edge and automated Chromium sequentially", flush=True)
        result = run([node, str(cli), "test"], cwd=PACKAGE, timeout=180, check=False)
        if (browser_output / "results.json").exists():
            record["test_summary"] = json.loads((browser_output / "results.json").read_text())[
                "stats"
            ]
        if result.returncode or record.get("test_summary", {}).get("expected") != 2:
            raise RuntimeError("Browser checks did not both pass; inspect results and traces")
        if record["test_summary"].get("unexpected") or record["test_summary"].get("flaky"):
            raise RuntimeError("Unexpected or flaky browser result")
        record["passed"] = True
    except BaseException as exc:
        record["error"] = repr(exc)
        print(record["error"], flush=True)
    finally:
        (runtime / "stop").write_text("stop\n")
        if worker:
            try:
                worker.wait(timeout=90)
                if worker.returncode:
                    raise RuntimeError(f"Linux fixture returned {worker.returncode}")
                record["fixture"] = json.loads((runtime / "fixture-result.json").read_text())
                if not record["fixture"]["passed"] or any(record["fixture"]["cleanup"].values()):
                    raise RuntimeError("Fixture/cleanup failed")
            except BaseException as exc:
                record["cleanup_error"] = repr(exc)
                record["passed"] = False
        worker_log.close()
        log.close()
        evidence.mkdir()
        shutil.copytree(browser_output, evidence / "browser")
        for name in (
            "windows-commands.log",
            "linux-worker.log",
            "linux-commands.log",
            "fixture-result.json",
        ):
            if (runtime / name).exists():
                shutil.copyfile(runtime / name, evidence / name)
        paths = [
            *PACKAGE.glob("*.mjs"),
            *PACKAGE.glob("*.json"),
            *PACKAGE.glob("*.html"),
            *PACKAGE.glob("tests/*.mjs"),
            ROOT / "ops/compose/compose.browser-fixture.yaml",
            ROOT / "scripts/platform/browser_app.py",
            ROOT / "scripts/platform/browser_fixture.py",
            Path(__file__).resolve(),
        ]
        record["source_sha256"] = {
            str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)
        }
        (evidence / "qualification.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        print("Evidence:", evidence.relative_to(ROOT), "passed:", record["passed"], flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
