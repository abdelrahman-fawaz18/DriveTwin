"""Prepare and run one guarded, locally viewable dashboard review on Windows."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts/release")]

from qualify_restarts import (  # noqa: E402
    DASHBOARD_REVIEW,
    guarded_launch,
    preflight,
    project_name,
    read,
    require,
    review_profile,
    source,
    verify_browser_tools,
    verify_chromium_binary,
    write,
)


def check_tools():
    require(os.name == "nt", "Run this entry point in Windows PowerShell")
    require(shutil.which("node.exe") is not None, "Node.js is missing from PATH")
    require(
        not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, timeout=15).strip(),
        "Commit the reviewed changes before starting this source-bound review",
    )
    return {
        "profile": review_profile(),
        "source": source(),
        "playwright": verify_browser_tools(),
        "chromium": verify_chromium_binary(),
    }


def build_dashboard(folder):
    invocation = uuid4().hex
    command = [
        sys.executable,
        "scripts/linux.py",
        "--run-id",
        invocation,
        "--timeout",
        "900",
        "--",
        "python3",
        "scripts/release/prepare_simulation.py",
        "--dashboard",
    ]
    with (folder / "build.log").open("xb") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
        try:
            require(child.wait(timeout=930) == 0, "Dashboard build failed; inspect build.log")
        finally:
            if child.poll() is None:
                subprocess.run(
                    [sys.executable, "scripts/linux.py", "--cancel", invocation],
                    cwd=ROOT,
                    timeout=20,
                    check=True,
                )
                child.wait(timeout=60)


def cleanup_verified(output):
    label = "label=com.docker.compose.project=" + project_name(output)
    code = (
        "import json,subprocess; "
        f"label={label!r}; "
        "print(json.dumps({kind:subprocess.check_output(['docker',kind,'ls','-q',"
        "'--filter',label]+(['-a'] if kind=='container' else [])).decode().splitlines() "
        "for kind in ('container','volume','network')}))"
    )
    rows = json.loads(
        subprocess.check_output(
            [sys.executable, "scripts/linux.py", "--timeout", "30", "--", "python3", "-c", code],
            cwd=ROOT,
            timeout=45,
        )
    )
    return {"passed": not any(rows.values()), "remaining": rows}


def run_review(tools):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    output = ROOT / "evidence/p6" / ("demo-dashboard-" + stamp)
    preparation = ROOT / ".runtime/dashboard-review" / stamp
    preparation.mkdir(parents=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {"passed": False, "preparation": str(preparation.relative_to(ROOT)), "tools": tools}
    awake = None
    sleep_dir = preparation / "sleep"
    print(f"RESULT FOLDER: {output}", flush=True)
    print(
        "One attempt only. Keep power connected and the lid open. Allow about 10-20 minutes; the build can take longer on a cold cache.",
        flush=True,
    )
    print(
        "If still running after 30 minutes, press Ctrl+C ONCE and wait for cleanup. Do not close this terminal.",
        flush=True,
    )
    try:
        print(
            "Checking Ubuntu, storage, and the unchanged 2 GiB Windows memory reserve (about 45 seconds).",
            flush=True,
        )
        summary["build_preflight"] = preflight(preparation)
        require(
            summary["build_preflight"]["passed"], "Host precheck held the review; no bus started"
        )
        with (preparation / "sleep.log").open("xb") as log:
            awake = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/windows_awake.py",
                    "hold",
                    str(sleep_dir),
                    "--lease-seconds",
                    "2100",
                    "--max-seconds",
                    "2100",
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        end = time.monotonic() + 10
        while not (sleep_dir / "state.json").is_file() and time.monotonic() < end:
            require(awake.poll() is None, "Temporary sleep prevention failed")
            time.sleep(0.2)
        require(
            read(sleep_dir / "state.json")["status"] == "holding",
            "Sleep prevention was not confirmed",
        )
        print(
            "Building the current dashboard service (usually 1-5 minutes). Build output is saved privately.",
            flush=True,
        )
        build_dashboard(preparation)
        require(source() == tools["source"], "Source changed during preparation")
        print(
            "Starting the guarded review. Your browser will open when the services are ready.",
            flush=True,
        )
        summary["exit_code"] = guarded_launch(output, DASHBOARD_REVIEW)
        summary["qualification_passed"] = read(output / "qualification.json")["passed"]
        summary["source_unchanged"] = source() == tools["source"]
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as exc:
        summary["error"] = repr(exc)
    finally:
        print(
            "Checking owned-resource cleanup and releasing temporary sleep prevention. Please wait.",
            flush=True,
        )
        try:
            summary["cleanup"] = cleanup_verified(output)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            summary["cleanup"] = {"passed": False, "error": repr(exc)}
        summary["sleep_request_released"] = awake is None
        if awake:
            try:
                (sleep_dir / "stop").touch()
                awake.wait(timeout=10)
                summary["sleep_request_released"] = (
                    read(sleep_dir / "state.json")["status"] == "released"
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                summary["sleep_release_error"] = repr(exc)
        summary["passed"] = all(
            (
                summary.get("qualification_passed"),
                summary.get("source_unchanged"),
                summary["cleanup"]["passed"],
                summary["sleep_request_released"],
            )
        )
        output.mkdir(exist_ok=True)
        write(output / "operator-result.json", summary)
        print(
            "FINAL RESULT: "
            + (
                "DASHBOARD REVIEW PASSED; EVIDENCE REVIEW PENDING"
                if summary["passed"]
                else "FAILED OR INCOMPLETE; REVIEW REQUIRED"
            ),
            flush=True,
        )
        print(f"Cleanup verified: {summary['cleanup']['passed']}", flush=True)
        print(f"Sleep request released: {summary['sleep_request_released']}", flush=True)
        print(f"RESULT FOLDER: {output}", flush=True)
        if (output / "screenshots.html").is_file():
            print(f"SCREENSHOT GALLERY: {output / 'screenshots.html'}", flush=True)
        print(
            "No retry was launched. Review the result folder before starting another run.",
            flush=True,
        )
    return 0 if summary["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check committed inputs and browser tools; start no services",
    )
    args = parser.parse_args()
    tools = check_tools()
    print(
        "Committed source, pinned browser tools, and dashboard review profile: PASSED", flush=True
    )
    if args.check_only:
        print("CHECK ONLY: no build, bus, browser, or sleep request started.")
        return 0
    import msvcrt

    lock_path = ROOT / ".runtime/p6-monitoring.lock"
    require(lock_path.resolve() == lock_path, "Runtime lock must stay inside the project")
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            return run_review(tools)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
