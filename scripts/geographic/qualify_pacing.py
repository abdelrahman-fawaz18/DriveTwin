"""Six bounded, sequential one-bus pacing checks through the guarded live runner."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/launcher"), str(ROOT / "scripts/geographic")]
from live_inputs import read, write  # noqa: E402

from catalog import load, select  # noqa: E402


def inspect(folder, speed, samples):
    result = read(folder / "operator-result.json")
    if not result["passed"]:
        raise ValueError("Guarded live session failed or was incomplete; review its result")
    if not result["session"]["cleanup_verified"] or not result["session"]["backup_saved"]:
        raise ValueError("Cleanup and recording backup must both pass")
    accounting = read(folder / "recording-check.json")
    if not accounting["passed"] or accounting["events"] != samples:
        raise ValueError("The bounded recording did not contain every requested reading")
    source = read(folder / "source-report.json")
    pacing = source["plant_summary"]["pacing"]
    if pacing["time_scale"] != speed:
        raise ValueError("Source pace differs from the selected pace")
    views = [
        json.loads(line) for line in (folder / "forecast-pacing.jsonl").read_text().splitlines()
    ]
    fences = {v["current_fence"] for v in views if v["current_fence"] is not None}
    if len(fences) < 2:
        raise ValueError("At least two fresh forecast generations must be observed")
    controller = read(folder / "controller/controller-result.json")
    if controller["failed_jobs"] or not controller["cleanup_verified"]:
        raise ValueError("Forecast worker failure or unverified cleanup")
    return {
        "passed": True,
        "time_scale": speed,
        "working_s": samples,
        "real_life_driving_s": pacing["elapsed_s"],
        "achieved_speed": samples / pacing["elapsed_s"],
        "current_forecast_generations": len(fences),
        "completed_forecasts": controller["completed_jobs"],
        "unavailable_reasons": sorted({v["reason"] for v in views if v["reason"]}),
        "recording": accounting,
        "cleanup_verified": True,
        "backup_saved": True,
    }


def main():
    if sys.platform != "win32":
        raise ValueError("Run this entry point on Windows")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    output = ROOT / "evidence/energy-v2" / ("pacing-" + stamp)
    output.mkdir()
    report = {"passed": False, "automatic_retry": False, "attempts": []}
    trips = load()
    trip = next(t for t in trips if "Lachine" in t["title"])
    baseline = None
    print(
        "Six sequential one-bus checks, with the existing 2 GiB reserve. Allow 20–35 real-life minutes.",
        flush=True,
    )
    print(
        "Stops after any failed or blocked check. No automatic retry. RESULTS:", output, flush=True
    )
    try:
        for speed in (1, 0.5, 2, 5, 10, 20):
            # Leave time to observe the second forecast at the 60-working-second cadence.
            samples = max(80, int(90 * speed))
            profile = select(trips, trip["id"], ["energy", "replay"], speed)
            profile_file = output / f"profile-{speed:g}.json"
            write(profile_file, profile)
            identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
            folder = ROOT / "evidence/energy-v2" / ("live-journey-" + identity)
            entry = {"speed": speed, "output": folder.relative_to(ROOT).as_posix(), "passed": False}
            report["attempts"].append(entry)
            write(output / "result.json", report)
            print(
                f"Checking {speed:g}x: {samples} working seconds, approximately {samples / speed:g} real-life driving seconds.",
                flush=True,
            )
            child = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/geographic/live.py",
                    "--profile",
                    str(profile_file),
                    "--preview-seconds",
                    str(samples),
                    "--output",
                    str(folder),
                    "--no-browser",
                ],
                cwd=ROOT,
            )
            try:
                code = child.wait()
            except KeyboardInterrupt:
                # The console sends Ctrl+C to both processes. Give the guarded runner
                # time to preserve its partial recording and remove owned resources.
                print(
                    "Waiting for the interrupted session's cleanup. Keep this terminal open.",
                    flush=True,
                )
                child.wait(timeout=210)
                raise
            if code:
                raise ValueError(f"{speed:g}x stopped. Inspect {folder / 'operator-result.json'}")
            entry.update(inspect(folder, speed, samples))
            first = [
                event["body"]["samples"]
                for event in read(folder / "source-report.json")["events"][:60]
            ]
            if baseline is not None and first != baseline:
                entry["passed"] = False
                raise ValueError(
                    "Changing the pacing changed the first sixty physical observations"
                )
            baseline = first
            entry["physical_readings_match"] = True
            write(output / "result.json", report)
        report["passed"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = repr(exc)
        print("STOPPED:", exc, flush=True)
    finally:
        write(output / "result.json", report)
        print("PACING CHECKS PASSED:", report["passed"], "RESULTS:", output, flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
