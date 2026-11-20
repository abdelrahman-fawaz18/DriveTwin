"""Windows browser controller for the real-service P1-C1 dashboard checkpoint."""

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

ROOT = Path(__file__).resolve().parents[2]


def fingerprints():
    paths = [
        *ROOT.glob("apps/dashboard/src/**/*"),
        *ROOT.glob("apps/dashboard/tests/*"),
        *ROOT.glob("apps/dashboard/*.json"),
        ROOT / "apps/dashboard/index.html",
        ROOT / "apps/dashboard/replay.html",
        ROOT / "apps/dashboard/vite.config.mjs",
        *ROOT.glob("qualification/browser/dashboard/*"),
        ROOT / "qualification/browser/dashboard.config.mjs",
        ROOT / "qualification/browser/package.json",
        ROOT / "qualification/browser/package-lock.json",
        Path(__file__),
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if os.name != "nt" or not evidence.is_relative_to(ROOT / "evidence/p1") or evidence.exists():
        parser.error("Run on Windows with a new directory under evidence/p1")
    evidence.mkdir(parents=True)
    control = ROOT / ".runtime/dashboard" / uuid.uuid4().hex
    control.mkdir(parents=True)
    before = fingerprints()
    record = {"passed": False, "checkpoint": "P1-D1", "source_sha256": before}
    worker = None
    with (control / "worker.log").open("w", encoding="utf-8") as log:
        try:
            print("Starting the owned live-source dashboard fixture", flush=True)
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/linux.py",
                    "--timeout",
                    "2400",
                    "--",
                    "python3",
                    "scripts/monitoring/qualify_uplink.py",
                    "--evidence-dir",
                    (evidence.relative_to(ROOT) / "services").as_posix(),
                    "--dashboard-control",
                    control.as_posix().replace("C:/", "/mnt/c/"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
            )
            deadline = time.monotonic() + 900
            while not (control / "ready.json").is_file():
                if worker.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        "Dashboard fixture did not become ready; see ignored worker log"
                    )
                time.sleep(0.5)
            fixture = json.loads((control / "ready.json").read_text())
            package = ROOT / "qualification/browser"
            node = shutil.which("node.exe")
            if not node or not (package / "node_modules/playwright/cli.js").is_file():
                raise RuntimeError("Prepare the previously qualified Windows browser tools first")
            env = {
                **os.environ,
                "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".runtime/playwright-browsers"),
                "EVDT_DASHBOARD_CONTROL": str(control),
                "EVDT_BASE_URL": fixture["base_url"],
                "EVDT_BROWSER_OUTPUT": str(evidence),
            }
            print("Testing the actual dashboard in visible Edge and automated Chromium", flush=True)
            result = subprocess.run(
                [
                    node,
                    str(package / "node_modules/playwright/cli.js"),
                    "test",
                    "--config",
                    "dashboard.config.mjs",
                    "monitoring.spec.mjs",
                ],
                cwd=package,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=330,
            )
            (control / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            results = [
                json.loads((control / (project + "-result.json")).read_text())
                for project in ("windows-edge-visible", "chromium-automated")
                if (control / (project + "-result.json")).is_file()
            ]
            browser_passed = (
                result.returncode == 0 and len(results) == 2 and all(r["passed"] for r in results)
            )
            (control / "browser-result.json").write_text(
                json.dumps({"passed": browser_passed, "projects": results})
            )
            (control / "browser-done").touch()
            if not browser_passed:
                raise RuntimeError("Base dashboard browser checks failed")
            deadline = time.monotonic() + 150
            while not (control / "scenarios-ready").is_file():
                if worker.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Controlled scenario producer did not become ready")
                time.sleep(0.25)
            print("Testing persisted quality and clock transitions in both browsers", flush=True)
            scenario_output = evidence / "scenarios"
            scenario_output.mkdir()
            scenario_result = subprocess.run(
                [
                    node,
                    str(package / "node_modules/playwright/cli.js"),
                    "test",
                    "--config",
                    "dashboard.config.mjs",
                    "scenarios.spec.mjs",
                ],
                cwd=package,
                env={**env, "EVDT_BROWSER_OUTPUT": str(scenario_output)},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=330,
            )
            (control / "scenario-browser.log").write_text(
                scenario_result.stdout + scenario_result.stderr, encoding="utf-8"
            )
            scenario_projects = [
                json.loads(p.read_text()) for p in control.glob("*-scenarios-result.json")
            ]
            scenarios_passed = (
                scenario_result.returncode == 0
                and len(scenario_projects) == 2
                and all(p["passed"] for p in scenario_projects)
            )
            (control / "scenario-browser-result.json").write_text(
                json.dumps({"passed": scenarios_passed, "projects": scenario_projects})
            )
            if not scenarios_passed:
                raise RuntimeError("Controlled quality/clock browser checks failed")
            print("Testing isolated replay and frozen recordings in both browsers", flush=True)
            replay_output = evidence / "replay"
            replay_output.mkdir()
            replay_result = subprocess.run(
                [
                    node,
                    str(package / "node_modules/playwright/cli.js"),
                    "test",
                    "--config",
                    "dashboard.config.mjs",
                    "replay.spec.mjs",
                ],
                cwd=package,
                env={**env, "EVDT_BROWSER_OUTPUT": str(replay_output)},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=330,
            )
            (control / "replay-browser.log").write_text(
                replay_result.stdout + replay_result.stderr, encoding="utf-8"
            )
            replay_projects = [
                json.loads(p.read_text()) for p in control.glob("*-replay-result.json")
            ]
            replay_passed = (
                replay_result.returncode == 0
                and len(replay_projects) == 2
                and all(p["passed"] for p in replay_projects)
            )
            record["replay_browsers"] = replay_projects
            if not replay_passed:
                raise RuntimeError("Isolated replay browser checks failed")
            (control / "scenarios-done").touch()
            record["scenario_browsers"] = scenario_projects
            worker.wait(timeout=400)
            services = json.loads((evidence / "services/qualification.json").read_text())
            record.update(
                passed=browser_passed
                and scenarios_passed
                and replay_passed
                and worker.returncode == 0
                and services["passed"],
                browsers=results,
                services_passed=services["passed"],
            )
        except Exception as exc:
            record["error"] = str(exc)
        finally:
            # Always release a waiting worker so its own Compose cleanup can run.
            if worker and worker.poll() is None:
                (control / "browser-result.json").write_text(json.dumps({"passed": False}))
                (control / "start-source").touch()
                (control / "browser-done").touch()
                (control / "scenario-browser-result.json").write_text(json.dumps({"passed": False}))
                (control / "scenarios-done").touch()
                try:
                    worker.wait(timeout=160)
                except subprocess.TimeoutExpired:
                    record["cleanup_unverified"] = True
                    worker.terminate()
                    worker.wait(timeout=15)
            if worker and worker.returncode and (control / "ready.json").is_file():
                # A launcher timeout may stop Python before its Compose finally block.
                fixture_id = uuid.UUID(
                    json.loads((control / "ready.json").read_text())["run_id"]
                ).hex
                fixture_env = ROOT / ".runtime/p1-uplink" / fixture_id / "compose.env"
                cleanup = subprocess.run(
                    [
                        sys.executable,
                        "scripts/linux.py",
                        "--",
                        "docker",
                        "compose",
                        "--project-directory",
                        ".",
                        "--env-file",
                        str(fixture_env.relative_to(ROOT)).replace("\\", "/"),
                        "-f",
                        "ops/compose/compose.monitoring.yaml",
                        "-p",
                        "evdt-p1-" + fixture_id[:12],
                        "--profile",
                        "*",
                        "down",
                        "--volumes",
                        "--remove-orphans",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                (control / "fallback-cleanup.log").write_text(
                    cleanup.stdout + cleanup.stderr, encoding="utf-8"
                )
                record["fallback_cleanup_exit_code"] = cleanup.returncode
            if fingerprints() != before:
                record.update(passed=False, error="Source changed during browser qualification")
            (evidence / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
            print(
                f"Dashboard passed: {record['passed']}; report: {evidence.relative_to(ROOT)}",
                flush=True,
            )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
