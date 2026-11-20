"""Run one bounded release check from a guarded Windows terminal."""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / p)
    for p in ("scripts/release_checks", "scripts/monitoring", "scripts/commands", "src")
]

from demo_session import DASHBOARD_REVIEW, DEMO_BROWSER, review_profile  # noqa: E402
from extension_session import recording_path  # noqa: E402
from history_session import HistoryResourceClient, restore_identity  # noqa: E402
from qualify_connected_speed_limit import digest, read, require, write  # noqa: E402
from restart_cases import (  # noqa: E402
    CASES,
    DEMO_CASES,
    EXPERIMENT_CASE,
    EXTENSION_CASE,
    HISTORY_CASE,
    OUTAGE_CASE,
    RETENTION_CASE,
    SCHEMA_CASE,
    execute,
)
from smoke_policy import (  # noqa: E402
    INPUT_PATHS,
    LinuxSmokeGuard,
    WindowsSmokeGuard,
    consume_receipt,
    inventory,
    launch_receipt,
    preflight,
    verify_browser_tools,
    verify_chromium_binary,
)

SCRIPT = "scripts/release/qualify_restarts.py"
TIMEOUT = 260
LINUX_SAMPLE_PERIOD_S = 0.75


def profile(case):
    if case in DEMO_CASES:
        return "P7-02"
    if case in (HISTORY_CASE, RETENTION_CASE, OUTAGE_CASE):
        return "P6-01"
    if case == EXTENSION_CASE:
        return "P6-03"
    if case == EXPERIMENT_CASE:
        return "P6-05"
    return "P6-04" if case == SCHEMA_CASE else "P6-02"


def source():
    profile = "qualification/release/simulation.json"
    return {
        "profile_sha256": digest(ROOT / profile),
        "inventory": inventory(
            (
                *INPUT_PATHS,
                SCRIPT,
                "scripts/dashboard_review.py",
                "scripts/windows_awake.py",
                "qualification/dashboard",
                "scripts/release/restart_cases.py",
                "scripts/release/gate_restart_source.py",
                "scripts/release/schema_upgrade_probe.py",
                "scripts/release/demo_session.py",
                "scripts/release/extension_session.py",
                "scripts/release/recorded_adapter.py",
                "scripts/release/retention_preview.py",
                "scripts/release/verify_retention_preview.py",
                "scripts/release/history_session.py",
                "scripts/release/outage_session.py",
                "scripts/release/prepare_simulation.py",
                "scripts/commands",
                "ops/compose/compose.commands.yaml",
                "ops/compose/compose.live-speed.yaml",
                profile,
            )
        ),
    }


def project_name(output):
    import hashlib

    return "evdt-e2c-" + hashlib.sha256(output.name.encode()).hexdigest()[:12]


def open_review_page(ready, *, replay=False):
    name, port = ("replay", 18766) if replay else ("monitoring", 18765)
    require(ready[f"{name}_url"] == f"http://127.0.0.1:{port}", "Unexpected review address")
    if replay:
        os.startfile(ready["replay_url"] + "/#access=" + ready["replay_token"])
        print("Replay opened for this recording.", flush=True)
        return
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
        ],
        input=ready[f"{name}_token"],
        text=True,
        check=True,
        timeout=10,
    )
    label = "Dashboard key"
    print(f"\n{name.upper()} LOGIN · temporary key for this review", flush=True)
    print(f"Address: {ready[f'{name}_url']}", flush=True)
    print(f"{label}: {ready[f'{name}_token']}", flush=True)
    print(
        "If the clipboard changes, copy the key above into the matching browser field.\n",
        flush=True,
    )
    os.startfile(ready[f"{name}_url"])
    action = "Connect to monitoring"
    print(
        f"Credential copied to your clipboard. In the browser: click {label}, press Ctrl+V, then {action}.",
        flush=True,
    )


def linux_command(*args, timeout):
    return [sys.executable, "scripts/linux.py", "--timeout", str(timeout), "--", *args]


def owned_cleanup(output, case):
    project = project_name(output)
    if case == HISTORY_CASE:
        control = output / "restore"
        if not (control / "restore-control.json").is_file():
            return
        project, _ = restore_identity(control)
    label = "label=com.docker.compose.project=" + project
    for kind in ("container", "volume", "network"):
        listing = ["docker", kind, "ls", "-q", "--filter", label]
        options = []
        if kind == "container":
            listing.append("-a")
            # Docker removes anonymous volumes attached to these exact owned containers.
            options = ["-f", "-v"]
        ids = subprocess.check_output(listing, cwd=ROOT, timeout=10, text=True).splitlines()
        if not ids:
            continue
        subprocess.run(
            ["docker", kind, "rm", *options, *ids],
            cwd=ROOT,
            timeout=15,
            capture_output=True,
            check=True,
        )
        require(
            not subprocess.check_output(listing, cwd=ROOT, timeout=10).strip(),
            f"Owned {kind} cleanup incomplete",
        )


@contextmanager
def monitor(
    project,
    output,
    *,
    history=False,
    allow_replacement=False,
    restartable_services=(),
    finite_services=(),
):
    from linux_resources import DockerReadClient, sample

    stopped = threading.Event()
    ready = threading.Event()
    failures = []
    if history:
        restartable_services = ("db",)

    def collect():
        guard = LinuxSmokeGuard(restartable_services=restartable_services)
        engine = None
        try:
            engine = (
                HistoryResourceClient(project, output) if history else DockerReadClient(project)
            )
            with (output / "linux-resources.jsonl").open("x", encoding="utf-8") as report:
                sequence = 0
                while not stopped.is_set():
                    tick = time.monotonic()
                    row = {
                        "sequence": sequence,
                        **sample(
                            engine,
                            allow_replacement=allow_replacement,
                            restartable_services=restartable_services,
                            finite_services=finite_services,
                        ),
                    }
                    report.write(json.dumps(row, allow_nan=False) + "\n")
                    report.flush()
                    guard.observe(row)
                    sequence += 1
                    ready.set()
                    # Leave scheduling headroom below the unchanged 1.5-second coverage limit.
                    stopped.wait(max(0, tick + LINUX_SAMPLE_PERIOD_S - time.monotonic()))
        except Exception as exc:
            failures.append(str(exc))
            write(output / "linux-guard.json", {"reason": str(exc)})
            ready.set()
        finally:
            if engine:
                engine.close()

    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    try:
        require(ready.wait(6) and not failures, "Linux resource sampler failed to start")
        yield
    finally:
        stopped.set()
        thread.join(timeout=8)
        require(
            not thread.is_alive() and not failures, f"Linux resource sampler failed: {failures}"
        )


def worker(output, case):
    consume_receipt(output, output, profile(case))
    expected = read(output / "expected-source.json")
    require(expected == source(), "Windows and Ubuntu source fingerprints differ")

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    result = {"passed": False, "case": case, "source": expected}
    try:
        result["checks"] = execute(
            case, output, project_name(output), monitor, expected["inventory"]["sha256"]
        )
        result["source_after"] = source()
        result["passed"] = result["checks"]["passed"] and result["source_after"] == expected
    except (Exception, KeyboardInterrupt) as exc:
        result["error"] = repr(exc)
    finally:
        try:
            owned_cleanup(output, case)
            result["cleanup_passed"] = True
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            result.update(passed=False, cleanup_passed=False, cleanup_error=repr(exc))
        write(output / "worker.json", result)
    return 0 if result["passed"] else 1


def guarded_launch(output, case, *, recording=None):
    from windows_resources import Sampler

    expected = source()
    output.mkdir()
    if recording:
        write(output / "experiment-input.json", recording.relative_to(ROOT).as_posix())
    summary = {"passed": False, "case": case, "source": expected}
    sampler = None
    try:
        if case in (DEMO_BROWSER, DASHBOARD_REVIEW):
            summary["browser_tools"] = {
                "playwright": verify_browser_tools(),
                "chromium": verify_chromium_binary(),
            }
        summary["preflight"] = preflight(output)
        require(summary["preflight"]["passed"], "Host launch precheck held the test")
        guard = WindowsSmokeGuard(output / "linux-guard.json", live_clock=True)
        sampler = Sampler(output / "windows-resources.jsonl", on_sample=guard.observe)
        sampler.start()
        deadline = time.monotonic() + 6
        while not guard.first_sample_validated:
            require(
                not (sampler.error or guard.failure) and time.monotonic() < deadline,
                "No valid initial runtime resource sample",
            )
            time.sleep(0.1)
        write(output / "expected-source.json", expected)
        launch_receipt(output, output, profile(case))
        invocation = uuid4().hex
        relative = output.relative_to(ROOT).as_posix()
        timeout = (
            review_profile()["worker_timeout_seconds"]
            if case == DASHBOARD_REVIEW
            else {HISTORY_CASE: 1800, OUTAGE_CASE: 750}.get(case, TIMEOUT)
        )
        child_args = linux_command(
            "python3", SCRIPT, "--case", case, "--output", relative, "--worker", timeout=timeout
        )
        child_args[2:2] = ["--run-id", invocation]
        with (output / "worker.log").open("xb") as log:
            child = subprocess.Popen(child_args, cwd=ROOT, stdout=log, stderr=log)
            browser = None
            try:
                announced = False
                recording_announced = False
                while child.poll() is None:
                    require(
                        not (sampler.error or guard.failure), str(sampler.error or guard.failure)
                    )
                    if case == "demo-view" and not announced and (output / "ready.json").is_file():
                        print(
                            f"Demo ready. See the local demo guide and {relative}/ready.json.",
                            flush=True,
                        )
                        announced = True
                    if (
                        case in (DEMO_BROWSER, DASHBOARD_REVIEW)
                        and (output / "ready.json").is_file()
                    ):
                        if browser is None:
                            print("Demo ready. Starting the automated browser.", flush=True)
                            if case == DASHBOARD_REVIEW:
                                open_review_page(read(output / "ready.json"))
                                print(
                                    "Navigate freely. A separate automated browser captures all nine tabs. One bus runs for about five minutes, then recordings remain available for two minutes.",
                                    flush=True,
                                )
                            with (output / "browser.log").open("xb") as browser_log:
                                browser = subprocess.Popen(
                                    ["node.exe", "release-demo.mjs", str(output)],
                                    cwd=ROOT / "qualification/browser",
                                    env={
                                        **os.environ,
                                        "PLAYWRIGHT_BROWSERS_PATH": str(
                                            ROOT / ".runtime/playwright-browsers"
                                        ),
                                    },
                                    stdout=browser_log,
                                    stderr=browser_log,
                                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                                )
                        require(browser.poll() in (None, 0), "Browser failed. See browser.log.")
                    if (
                        case == DASHBOARD_REVIEW
                        and not recording_announced
                        and (output / "review-recording").is_file()
                    ):
                        print(
                            "Live drive and automated checks finished. Two minutes remain for recorded history and replay. Opening replay with its separate credential copied to the clipboard.",
                            flush=True,
                        )
                        open_review_page(read(output / "ready.json"), replay=True)
                        recording_announced = True
                    time.sleep(0.25)
                ended = time.monotonic()
                summary["runtime_ended_monotonic_s"] = ended
                require(
                    ended - guard.previous <= 1.5,
                    "Required memory samples did not cover the end of the fixture",
                )
                if guard.low_start is not None:
                    tail_s = max(0.0, ended - guard.low_start)
                    summary["runtime_final_below_floor_upper_s"] = tail_s
                    require(
                        tail_s <= guard.policy["warning_duration_s"],
                        "Windows available RAM remained below 2 GiB through fixture completion",
                    )
                summary["worker"] = read(output / "worker.json")
                if case in (DEMO_BROWSER, DASHBOARD_REVIEW) and summary["worker"]["passed"]:
                    require(browser and browser.wait(timeout=10) == 0, "Browser runner failed")
                    require(
                        summary["worker"]["checks"]["browser"]["chromium"]
                        == summary["browser_tools"]["chromium"],
                        "Browser version differed from the pinned Chromium",
                    )
                summary["passed"] = (
                    child.returncode == 0
                    and summary["worker"]["passed"]
                    and source() == expected
                    and not (sampler.error or guard.failure)
                )
            finally:
                try:
                    if browser and browser.poll() is None:
                        subprocess.run(
                            ["taskkill", "/PID", str(browser.pid), "/T", "/F"],
                            timeout=10,
                            capture_output=True,
                        )
                        browser.wait(timeout=10)
                finally:
                    if child.poll() is None:
                        subprocess.run(
                            [sys.executable, "scripts/linux.py", "--cancel", invocation],
                            cwd=ROOT,
                            timeout=15,
                        )
                        child.wait(timeout=60)
                    if not summary["passed"]:
                        cleanup = subprocess.run(
                            linux_command(
                                "python3",
                                SCRIPT,
                                "--case",
                                case,
                                "--output",
                                relative,
                                "--cleanup",
                                timeout=75,
                            ),
                            cwd=ROOT,
                            timeout=85,
                            stdout=log,
                            stderr=log,
                        )
                        summary["fallback_cleanup_passed"] = cleanup.returncode == 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as exc:
        summary["error"] = repr(exc)
    finally:
        if sampler:
            sampler.close()
        write(output / "qualification.json", summary)
    print(f"{profile(case)} {case} passed: {summary['passed']}")
    return 0 if summary["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--recording", type=Path, help="Qualified two-bus source for recorded-experiment"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--worker", action="store_true")
    mode.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    prefixes = {
        "P6-01": "history-",
        "P6-03": "extension-",
        "P6-04": "compatibility-",
        "P6-05": "experiment-",
        "P7-02": "demo-",
    }
    prefix = prefixes.get(profile(args.case), "restart-")
    if args.case == RETENTION_CASE:
        prefix = "retention-"
    if args.case == OUTAGE_CASE:
        prefix = "outage-"
    if output.parent != ROOT / "evidence/p6" or not output.name.startswith(prefix):
        parser.error(f"Use a {prefix}* directory directly under evidence/p6")
    if args.worker or args.cleanup:
        if os.name == "nt" or not (output / "expected-source.json").is_file():
            parser.error("Start the guarded controller from Windows")
        if args.cleanup:
            owned_cleanup(output, args.case)
            return 0
        return worker(output, args.case)
    if os.name != "nt" or output.exists():
        parser.error("Start on Windows with a new evidence folder")
    recording = args.recording
    if (args.case == EXPERIMENT_CASE) != (recording is not None):
        parser.error("Provide --recording only for the recorded-experiment case")
    if recording is not None:
        try:
            recording = recording_path(recording)
            if not (recording / "qualification.json").is_file():
                parser.error("The selected recording has no qualification report")
        except RuntimeError as exc:
            parser.error(str(exc))
    import msvcrt

    path = ROOT / ".runtime/p6-monitoring.lock"
    if path.resolve() != path:
        parser.error("The runtime lock path must stay inside the project")
    path.parent.mkdir(exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            return guarded_launch(output, args.case, recording=recording)
        finally:
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
