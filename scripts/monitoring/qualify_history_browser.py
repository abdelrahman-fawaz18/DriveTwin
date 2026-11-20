"""Windows-driven real history downloads with independent original-packet audits."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from history_download_audit import audit

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ("windows-edge-visible", "chromium-automated")


def fingerprints():
    paths = [
        Path(__file__),
        ROOT / "scripts/monitoring/history_download_audit.py",
        ROOT / "scripts/monitoring/history_browser_bridge.py",
        ROOT / "scripts/monitoring/qualify_history_restore.py",
        ROOT / "qualification/browser/dashboard/history-volume.spec.mjs",
        ROOT / "qualification/browser/dashboard.config.mjs",
        ROOT / "qualification/browser/package.json",
        ROOT / "qualification/browser/package-lock.json",
        *ROOT.glob("apps/dashboard/src/**/*"),
        ROOT / "benchmarks/history.json",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    checkpoint, output = args.checkpoint.resolve(), args.evidence_dir.resolve()
    if (
        os.name != "nt"
        or not checkpoint.is_relative_to(ROOT / ".private/backups")
        or not output.is_relative_to(ROOT / "evidence/p1")
        or output.exists()
    ):
        parser.error(
            "Run on Windows with a canonical private checkpoint and new evidence directory"
        )
    output.mkdir(parents=True)
    control = ROOT / ".runtime/dashboard" / uuid.uuid4().hex
    control.mkdir(parents=True)
    before = fingerprints()
    record = {"passed": False, "W2_browser_export_passed": False, "source_sha256": before}
    worker = None
    with (output / "worker.log").open("w", encoding="utf-8") as log:
        try:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/linux.py",
                    "--timeout",
                    "1800",
                    "--",
                    "python3",
                    "scripts/monitoring/qualify_history_restore.py",
                    "--checkpoint",
                    checkpoint.relative_to(ROOT).as_posix(),
                    "--evidence-dir",
                    (output.relative_to(ROOT) / "services").as_posix(),
                    "--browser-control",
                    control.as_posix().replace("C:/", "/mnt/c/"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
            )
            deadline = time.monotonic() + 900
            while not (control / "ready.json").exists():
                if worker.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Restored history browser fixture did not become ready")
                time.sleep(0.25)
            fixture = json.loads((control / "ready.json").read_bytes())
            node = shutil.which("node.exe")
            package = ROOT / "qualification/browser"
            if not node or not (package / "node_modules/playwright/cli.js").is_file():
                raise RuntimeError("Previously qualified Windows browser tools are unavailable")
            env = {
                **os.environ,
                "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".runtime/playwright-browsers"),
                "EVDT_DASHBOARD_CONTROL": str(control),
                "EVDT_BASE_URL": fixture["base_url"],
                "EVDT_BROWSER_OUTPUT": str(output),
            }
            env.pop("EVDT_MEASUREMENT_TRACE", None)
            print(
                "Testing real five-signal history and JSON/CSV downloads in both browsers",
                flush=True,
            )
            completed = subprocess.run(
                [
                    node,
                    str(package / "node_modules/playwright/cli.js"),
                    "test",
                    "--config",
                    "dashboard.config.mjs",
                    "history-volume.spec.mjs",
                ],
                cwd=package,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=800,
            )
            (output / "browser.log").write_text(
                completed.stdout + completed.stderr, encoding="utf-8"
            )
            reports = [
                json.loads((control / (project + "-history-result.json")).read_bytes())
                for project in PROJECTS
            ]
            record["browser_reports"] = reports
            if completed.returncode or not all(r["passed"] for r in reports):
                raise RuntimeError("History browser collection failed")
            print(
                "Independently checking every downloaded row against the original source packets",
                flush=True,
            )
            audits = [
                audit(checkpoint, output / "downloads", fixture, project) for project in PROJECTS
            ]
            record["download_audits"] = audits
            (output / "download-audits.json").write_text(json.dumps(audits, indent=2) + "\n")
            (control / "browser-result.json").write_text(
                json.dumps({"passed": True, "projects": list(PROJECTS), "audits": audits})
            )
            (control / "browser-done").touch()
            worker.wait(timeout=180)
            services = json.loads((output / "services/qualification.json").read_bytes())
            if worker.returncode or not services["passed"]:
                raise RuntimeError("History backend qualification or owned cleanup failed")
            record.update(
                passed=True,
                W2_browser_export_passed=fixture["full_W2_volume"],
                rows_per_download=fixture["expected_rows"],
                asset_ids=fixture["assets"],
                services_passed=True,
            )
        except Exception as exc:
            record.update(passed=False, W2_browser_export_passed=False, error=str(exc))
        finally:
            if worker is not None and worker.poll() is None:
                (control / "browser-result.json").write_text(json.dumps({"passed": False}))
                (control / "browser-done").touch()
                try:
                    worker.wait(timeout=150)
                except subprocess.TimeoutExpired:
                    worker.terminate()
                    worker.wait(timeout=15)
                    record["cleanup_unverified"] = True
            if worker is not None and worker.returncode:
                stamp = output / "services/restore-control.json"
                if stamp.exists():
                    identity = json.loads(stamp.read_bytes())
                    identifier = uuid.UUID(identity["runtime_id"]).hex
                    if (
                        identifier != identity["runtime_id"]
                        or identity["project"] != "evdt-history-restore-" + identifier[:12]
                    ):
                        raise RuntimeError("Refusing unbound history-restore fallback cleanup")
                    compose = ROOT / ".runtime/history-restore" / identifier / "compose.json"
                    if compose.is_file():
                        cleanup = subprocess.run(
                            [
                                sys.executable,
                                "scripts/linux.py",
                                "--",
                                "docker",
                                "compose",
                                "-f",
                                compose.relative_to(ROOT).as_posix(),
                                "-p",
                                identity["project"],
                                "down",
                                "--volumes",
                                "--remove-orphans",
                            ],
                            cwd=ROOT,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=150,
                        )
                        (output / "fallback-cleanup.log").write_text(
                            cleanup.stdout + cleanup.stderr
                        )
                        record["fallback_cleanup_exit_code"] = cleanup.returncode
            if before != fingerprints():
                record.update(
                    passed=False,
                    W2_browser_export_passed=False,
                    error="History browser source changed",
                )
            record["phase_1_complete"] = False
            (output / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
            print(
                f"History browser passed: {record['passed']}; evidence: {output.relative_to(ROOT)}",
                flush=True,
            )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
