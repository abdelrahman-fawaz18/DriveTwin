"""Coordinate the local command services and Windows browser checks."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from qualify_command_authority import seal_competing_proposals
from qualify_connected_speed_limit import (
    ASSET,
    ROOT,
    digest,
    read,
    request,
    require,
    running,
    wait_ready,
    write,
)

CASES = ("confirmed", "unknown", "sealed")


def service_case(compose, env, tokens, run, control, case):
    compose("up", "-d", "--pull", "never", "api", "command-api", "replay")

    def address(service):
        return "http://" + compose("port", service, "8000").decode().strip()

    base, commands, replay = (address(service) for service in ("api", "command-api", "replay"))
    wait_ready(base)
    wait_ready(commands)
    if case == "sealed":
        seal_competing_proposals(commands, tokens, run, "operator_wins")
    ready = {
        "base": base,
        "replay": replay,
        "run": run,
        "asset": ASSET,
        "reader": tokens["reader"],
        "operator": tokens["operator"],
        "replay_credential": tokens["replay"],
    }
    write(control / "ready.tmp", ready)
    (control / "ready.tmp").replace(control / "ready.json")
    print(f"Operator browser fixture ready for {case}", flush=True)
    deadline = time.monotonic() + 165
    acted = False
    while time.monotonic() < deadline:
        if (control / "stop").exists():
            require((control / "browser-passed").is_file(), "Browser acceptance did not pass")
            return {"operator_case": case, "fixture_action_completed": acted}
        if not acted and (control / "act").exists():
            if case == "confirmed":
                compose("run", "--rm", "--no-deps", "-T", "command-simulator", timeout=75)
                command = read(Path(env["COMMAND_EVIDENCE_DIR"]) / "drive/commanded/command.json")
                compose("run", "--rm", "--no-deps", "-T", "command-observer", command["command_id"])
            elif case == "unknown":
                code, _ = request(
                    commands,
                    "/internal/v1/claim",
                    tokens["executor"],
                    json.dumps(running(4)).encode(),
                )
                require(code == 200, "Browser conflict case could not reserve the command")
            else:
                raise RuntimeError("Unexpected action in sealed browser case")
            acted = True
            (control / "acted").touch()
        time.sleep(0.15)
    raise RuntimeError("Operator browser fixture deadline exceeded")


def main():
    sys.path.insert(0, str(ROOT / "scripts/release"))
    from dashboard_service_guard import ReviewGuard
    from smoke_policy import verify_browser_tools, verify_chromium_binary

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if os.name != "nt" or not evidence.is_relative_to(ROOT / "evidence/p3") or evidence.exists():
        parser.error("Run from Windows with a new evidence/p3 directory")
    evidence.mkdir(parents=True)
    paths = [
        *ROOT.glob("apps/dashboard/src/**/*"),
        *ROOT.glob("apps/dashboard/*.json"),
        ROOT / "apps/dashboard/index.html",
        ROOT / "apps/dashboard/replay.html",
        ROOT / "apps/dashboard/vite.config.mjs",
        ROOT / "qualification/browser/dashboard.config.mjs",
        ROOT / "qualification/browser/dashboard/operator.spec.mjs",
        ROOT / "qualification/browser/package-lock.json",
        ROOT / "scripts/release/dashboard_service_guard.py",
        ROOT / "scripts/release/qualify_restarts.py",
        ROOT / "scripts/release_checks/smoke_policy.py",
        ROOT / "scripts/monitoring/host_memory_policy.py",
        ROOT / "benchmarks/monitoring-host.json",
    ]
    sources = {p.relative_to(ROOT).as_posix(): digest(p) for p in paths if p.is_file()}
    record = {"passed": False, "cases": {}, "frontend_sources": sources}
    controls = ROOT / ".runtime/p3-operator" / uuid.uuid4().hex
    controls.mkdir(parents=True)
    try:
        record["browser_tools"] = {
            "playwright": verify_browser_tools(),
            "chromium": verify_chromium_binary(),
        }
        package = ROOT / "qualification/browser"
        node = shutil.which("node.exe")
        require(
            node and (package / "node_modules/playwright/cli.js").is_file(),
            "Prepare the qualified Windows browser tools",
        )
        with (evidence / "build.log").open("wb") as log:
            subprocess.run(
                ["npm.cmd", "run", "build"],
                cwd=ROOT / "apps/dashboard",
                stdout=log,
                stderr=log,
                check=True,
                timeout=120,
            )
        dist = ROOT / "apps/dashboard/dist"
        bundle = {
            p.relative_to(dist).as_posix(): digest(p)
            for p in sorted(dist.rglob("*"))
            if p.is_file()
        }
        record["bundle_sha256"] = bundle
        for case in CASES:
            control = controls / case
            output = evidence / case
            control.mkdir()
            output.mkdir()
            case_report = record["cases"][case] = {"passed": False}
            guard = ReviewGuard(output, linux_failure=output / "services/linux-guard.json")
            with (output / "services.log").open("wb") as log:
                invocation = uuid.uuid4().hex
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        "scripts/linux.py",
                        "--run-id",
                        invocation,
                        "--timeout",
                        "230",
                        "--",
                        "python3",
                        "scripts/commands/qualify_connected_speed_limit.py",
                        "--operator-browser",
                        case,
                        "--operator-control",
                        control.as_posix().replace("C:/", "/mnt/c/"),
                        "--evidence-dir",
                        (output / "services").relative_to(ROOT).as_posix(),
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=log,
                )
                try:
                    end = time.monotonic() + 65
                    while not (control / "ready.json").is_file():
                        guard.check()
                        require(
                            worker.poll() is None and time.monotonic() < end,
                            "Operator fixture failed to start",
                        )
                        time.sleep(0.2)
                    ready = read(control / "ready.json")
                    print(f"Testing {case} through the actual dashboard in Chromium", flush=True)
                    env = {
                        **os.environ,
                        "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".runtime/playwright-browsers"),
                        "EVDT_DASHBOARD_CONTROL": str(control),
                        "EVDT_BASE_URL": ready["base"],
                        "EVDT_BROWSER_OUTPUT": str(output),
                        "EVDT_OPERATOR_CASE": case,
                    }
                    with (output / "browser.log").open("wb") as browser_log:
                        result = guard.run(
                            [
                                node,
                                str(package / "node_modules/playwright/cli.js"),
                                "test",
                                "--config",
                                "dashboard.config.mjs",
                                "operator.spec.mjs",
                                "--project=chromium-automated",
                            ],
                            cwd=package,
                            env=env,
                            stdout=browser_log,
                            stderr=browser_log,
                            timeout=120,
                        )
                    require(result.returncode == 0, f"{case} browser acceptance failed")
                    guard.finish_measurement()
                    (control / "browser-passed").touch()
                finally:
                    (control / "stop").touch()
                    try:
                        try:
                            worker.wait(timeout=55)
                        except subprocess.TimeoutExpired:
                            subprocess.run(
                                [sys.executable, "scripts/linux.py", "--cancel", invocation],
                                cwd=ROOT,
                                timeout=20,
                                check=True,
                                capture_output=True,
                            )
                            worker.wait(timeout=60)
                    finally:
                        guard.close()
                service_report = output / "services/qualification.json"
                checks = read(service_report)
                require(
                    worker.returncode == 0 and checks["passed"] and checks["cleanup_passed"],
                    "Operator fixture cleanup or checks failed",
                )
                require(
                    case == "sealed" or checks["fixture_action_completed"],
                    "Browser missed its real service action",
                )
                case_report.update(
                    passed=True,
                    host_guards_passed=True,
                    service_sha256=digest(service_report),
                    playwright_sha256=digest(output / "results.json"),
                )
        require(
            all(digest(ROOT / p) == sha for p, sha in sources.items()),
            "Frontend sources changed during checks",
        )
        require(
            all(digest(dist / p) == sha for p, sha in bundle.items()),
            "Dashboard build changed during checks",
        )
        record["passed"] = True
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        record["error"] = str(error)
    finally:
        write(evidence / "qualification.json", record)
    print(f"Operator browser qualification passed: {record['passed']}", flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
