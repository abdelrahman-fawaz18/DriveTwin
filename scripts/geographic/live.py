"""Run a prepared real-road journey with a visible local dashboard on Windows."""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / p)
    for p in (
        "scripts/geographic",
        "scripts/route_aware_energy",
        "scripts/release_checks",
        "scripts/monitoring",
        "scripts/commands",
        "scripts/release",
        "src",
    )
]

from live_inputs import check, read, write  # noqa: E402
from qualify import finish_host_guard, source  # noqa: E402
from smoke_policy import WindowsSmokeGuard, preflight  # noqa: E402


def worker(output):
    from live_session import run

    receipt = read(output / "launch.json")
    if not 0 <= time.time() - receipt["created"] <= 45 or source() != receipt["source"]:
        raise ValueError("A fresh, source-compatible host admission is required")
    (output / "launch.json").unlink()
    profile = read(output / "profile.json")
    check(profile)

    def stop(_signum, _frame):
        (output / "stop").touch()

    signal.signal(signal.SIGTERM, stop)
    result = run(output, profile, preview_seconds=receipt["preview_seconds"])
    return (
        0
        if result["status"] in ("arrived", "preview_complete") and result["cleanup_verified"]
        else 1
    )


def launch(profile, *, preview_seconds=None, no_browser=False, output_directory=None):
    from qualify_restarts import open_review_page
    from windows_resources import Sampler

    check(profile)
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Commit the reviewed source before this live session")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    output = output_directory or ROOT / "evidence/energy-v2" / ("live-journey-" + stamp)
    output.mkdir()
    write(output / "profile.json", profile)
    before = source()
    report = {"status": "incomplete", "passed": False, "source": before, "automatic_retry": False}
    invocation = uuid4().hex
    child = sampler = guard = awake = None
    sleep_dir = ROOT / ".runtime/live-journey-sleep" / stamp
    report["sleep_directory"] = sleep_dir.relative_to(ROOT).as_posix()

    def stage(name, message):
        temporary = output / "stage.tmp"
        write(temporary, {"stage": name, "message": message, "updated": time.time()})
        temporary.replace(output / "stage.json")
        if (output / "stop").exists():
            raise KeyboardInterrupt

    print(
        f"JOURNEY: {profile['title']}\nApproximately {profile['expected_working_minutes']:.1f} working minutes; {profile['expected_working_minutes'] / profile.get('time_scale', 1):.1f} real-life driving minutes at {profile.get('time_scale', 1):g}x.\nRESULT FOLDER: {output}",
        flush=True,
    )
    print(
        "Keep power connected and the lid open. Ctrl+C ONCE stops this session and preserves the partial recording. Allow up to three minutes for cleanup; keep this terminal open.",
        flush=True,
    )
    try:
        stage("checking", "Checking Ubuntu, storage, and available memory. Allow about 45 seconds.")
        print(
            "Checking Ubuntu, storage, and the unchanged 2 GiB Windows reserve (about 45 seconds)...",
            flush=True,
        )
        report["preflight"] = preflight(output)
        if not report["preflight"]["passed"]:
            report["status"] = "blocked_by_host_preflight"
            raise RuntimeError("Host preflight blocked this attempt; no bus started")
        stage("building", "Preparing the dashboard and checking runtime resources.")
        guard = WindowsSmokeGuard(
            output / "linux-guard.json", live_clock=True, allow_gradual_wall_drift=True
        )
        sampler = Sampler(output / "windows-resources.jsonl", on_sample=guard.observe)
        sampler.start()
        deadline = time.monotonic() + 6
        while not guard.first_sample_validated:
            if sampler.error or guard.failure or time.monotonic() > deadline:
                raise RuntimeError("Runtime resource guard did not admit this session")
            time.sleep(0.1)
        with (output / "awake.log").open("xb") as log:
            awake = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/windows_awake.py",
                    "hold",
                    str(sleep_dir),
                    "--lease-seconds",
                    "120",
                    "--max-seconds",
                    str(profile["maximum_real_life_s"] + 900),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        deadline = time.monotonic() + 10
        while not (sleep_dir / "state.json").exists():
            if awake.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Temporary sleep prevention failed")
            time.sleep(0.2)
        if read(sleep_dir / "state.json")["status"] != "holding":
            raise RuntimeError("Temporary sleep prevention was not confirmed")
        print("Building the current dashboard. Build output is saved in build.log.", flush=True)
        with (output / "build.log").open("xb") as log:
            subprocess.run(
                ["npm.cmd", "--prefix", "apps/dashboard", "run", "build"],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                check=True,
                timeout=120,
            )
        if source() != before or sampler.error or guard.failure:
            raise RuntimeError("Source changed or resource guard stopped setup")
        stage("starting", "Starting the selected services. Allow 1–3 minutes.")
        write(
            output / "launch.json",
            {"created": time.time(), "source": before, "preview_seconds": preview_seconds},
        )
        command = [
            sys.executable,
            "scripts/linux.py",
            "--run-id",
            invocation,
            "--timeout",
            str(profile["maximum_real_life_s"] + 600),
            "--",
            ".runtime/energy-v2/venv/bin/python",
            "scripts/geographic/live.py",
            "--worker",
            "--output",
            output.relative_to(ROOT).as_posix(),
        ]
        opened = arrived = False
        print(
            "Starting the bus and selected services. The dashboard is available when ready (usually 1–3 minutes).",
            flush=True,
        )
        with (output / "worker.log").open("xb") as log:
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            report["worker_started"] = True
            while child.poll() is None:
                (sleep_dir / "lease").touch()
                if (output / "stop").exists():
                    raise KeyboardInterrupt
                if sampler.error or guard.failure:
                    raise RuntimeError(str(sampler.error or guard.failure))
                if (output / "ready.json").exists() and not opened:
                    ready = read(output / "ready.json")
                    if not no_browser:
                        open_review_page(
                            {
                                **ready,
                                "monitoring_token": ready.get(
                                    "charging_operator_token", ready["monitoring_token"]
                                ),
                            }
                        )
                        print(
                            "Monitoring key stays on the clipboard. Use Map, Measurements, History, and Energy Forecast while the bus moves. Forecasts refresh periodically; they may take a few seconds to appear.",
                            flush=True,
                        )
                    else:
                        print("Live services ready. Private credentials: ready.json", flush=True)
                    opened = True
                    stage(
                        "running", "The bus is running. Open the dashboard to view live readings."
                    )
                if opened and (output / "arrived.json").exists() and not arrived:
                    if not no_browser and ready.get("replay_token"):
                        open_review_page(ready, replay=True)
                    print(
                        "Drive finished. Five minutes remain to browse recorded history, then the recording is saved and services stop."
                        if not preview_seconds
                        else "Short preview finished; saving and cleaning up.",
                        flush=True,
                    )
                    arrived = True
                    stage(
                        "history", "The trip has finished. Five minutes remain to browse history."
                    )
                time.sleep(0.5)
        result = read(output / "session-result.json")
        report.update(
            session=result,
            status=result["status"],
            passed=(
                child.returncode == 0
                and result["cleanup_verified"]
                and result.get("backup_saved") is True
                and source() == before
            ),
        )
    except (Exception, KeyboardInterrupt) as exc:
        report.update(error=repr(exc))
        if isinstance(exc, KeyboardInterrupt):
            report["status"] = "stopped_incomplete"
        print("Stopping this session and saving its recording. Please wait...", flush=True)
    finally:
        try:
            temporary = output / "stage.tmp"
            write(
                temporary,
                {
                    "stage": "stopping",
                    "message": "Saving the recording and removing this run's services.",
                    "updated": time.time(),
                },
            )
            temporary.replace(output / "stage.json")
        except OSError as exc:
            report["stage_update_error"] = repr(exc)
        try:
            if child and child.poll() is None:
                (output / "stop").touch()
                try:
                    child.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    subprocess.run(
                        [sys.executable, "scripts/linux.py", "--cancel", invocation],
                        cwd=ROOT,
                        timeout=20,
                        check=True,
                    )
                    child.wait(timeout=30)
            if (output / "session-result.json").exists():
                report["session"] = read(output / "session-result.json")
        except Exception as exc:
            report.update(passed=False, cleanup_error=repr(exc))
        finally:
            if awake:
                try:
                    if sleep_dir.exists():
                        (sleep_dir / "stop").touch()
                    awake.wait(timeout=10)
                    report["sleep_released"] = (
                        read(sleep_dir / "state.json")["status"] == "released"
                        if (sleep_dir / "state.json").exists()
                        else awake.returncode is not None
                    )
                except Exception as exc:
                    report.update(passed=False, sleep_release_error=repr(exc))
            finish_host_guard(report, sampler, guard)
            if not report["passed"] and report["status"] in (
                "arrived",
                "preview_complete",
                "incomplete",
            ):
                report["status"] = "failed_or_incomplete"
            write(output / "operator-result.json", report)
            if report.get("error"):
                print("Reason:", report["error"], flush=True)
            print(
                f"RESULT: {report['status']}\nCleanup verified: {report.get('session', {}).get('cleanup_verified', False)}\nRecording and logs: {output}\nNo retry was launched.",
                flush=True,
            )
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=".private/operator/longest-live.json")
    parser.add_argument(
        "--check", action="store_true", help="Check input fingerprints without starting services"
    )
    parser.add_argument(
        "--preview-seconds", type=int, help="Bounded setup check; does not complete the journey"
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.worker:
        output = (ROOT / args.output).resolve(strict=True)
        if not output.is_relative_to(ROOT / "evidence/energy-v2"):
            parser.error("Worker output must stay in project evidence")
        return worker(output)
    profile = read(ROOT / args.profile)
    check(profile)
    if args.check:
        print("Prepared journey input fingerprints: PASSED. No services started.")
        return 0
    if os.name != "nt":
        parser.error("Start the live session from Windows PowerShell")
    if args.preview_seconds is not None and not 30 <= args.preview_seconds <= 1800:
        parser.error("Preview duration must be 30–1800 working seconds")
    output = (ROOT / args.output).resolve() if args.output else None
    if output and (output.parent != ROOT / "evidence/energy-v2" or output.exists()):
        parser.error("Output must be a new folder directly inside evidence/energy-v2")
    import msvcrt

    with (ROOT / ".runtime/p6-monitoring.lock").open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            return launch(
                profile,
                preview_seconds=args.preview_seconds,
                no_browser=args.no_browser,
                output_directory=output,
            )
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
