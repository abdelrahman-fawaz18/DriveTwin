"""Guard one route-aware verification session from Windows; never auto-retry."""

import argparse
import hashlib
import json
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
        "scripts/release_checks",
        "scripts/monitoring",
        "scripts/commands",
        "scripts/release",
        "src",
    )
]

from smoke_policy import WindowsSmokeGuard, preflight  # noqa: E402

CASES = (
    "worker-database",
    "connected",
    "study-development",
    "study-validation",
    "study-reserved",
    "map-journey",
    "map-journey-source-distance",
    "charging-plant",
    "charging-plant-outage",
    "charging-plant-recovery",
)


def source():
    from smoke_policy import INPUT_PATHS, inventory

    return inventory(
        (
            *INPUT_PATHS,
            "scripts/route_aware_energy",
            "scripts/geographic",
            "scripts/geographic/prepare_osm_network.py",
            "scripts/geographic/extract_osm.py",
            "scripts/dev.py",
            "scripts/release/qualify_restarts.py",
            "qualification/energy/route-aware",
            "containers/energy-route-worker.Dockerfile",
            "environments/energy-geospatial-requirements.txt",
        )
    )


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def finish_host_guard(report, sampler, guard):
    if sampler is not None:
        sampler.close()
        if sampler.error:
            report.update(passed=False, sampler_error=str(sampler.error))
    if guard is not None:
        report["runtime_host_guard"] = {
            "first_sample_validated": guard.first_sample_validated,
            "failure": guard.failure,
        }
        if guard.failure or not guard.first_sample_validated:
            report["passed"] = False


def terrain_input(name):
    if name is None:
        return None
    path = (ROOT / name).resolve(strict=True)
    if (
        path.parent != ROOT / "data/downloads/energy-v2"
        or path.suffix != ".json"
        or path.stat().st_size > 65_536
    ):
        raise ValueError("Use a bounded terrain manifest in the project terrain cache")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def worker(case, output, *, fresh_b4=False, expanded_routes=False, terrain=None):
    expected = json.loads((output / "source.json").read_bytes())
    if source() != expected:
        raise ValueError("Windows and Ubuntu source fingerprints differ")
    receipt = output / "launch.json"
    admission = json.loads(receipt.read_bytes())
    if (
        admission["case"] != case
        or admission.get("fresh_b4", False) != fresh_b4
        or admission.get("expanded_routes", False) != expanded_routes
        or admission.get("terrain") != terrain
        or not 0 <= time.time() - admission["created"] <= 45
    ):
        raise ValueError("A fresh guarded launch receipt is required")
    receipt.unlink()

    def stop(_number, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    if case in ("charging-plant", "charging-plant-outage", "charging-plant-recovery"):
        sys.path.insert(0, str(ROOT / "scripts/geographic"))
        from charging_plant_probe import run

        raw_profile = (output / "profile.json").read_bytes()
        if hashlib.sha256(raw_profile).hexdigest() != admission["charging_profile"]:
            raise ValueError("Charging plant profile changed after host admission")
        result = run(
            output,
            json.loads(raw_profile),
            outage=case == "charging-plant-outage",
            recovery=case == "charging-plant-recovery",
        )
    elif case in ("map-journey", "map-journey-source-distance"):
        sys.path.insert(0, str(ROOT / "scripts/geographic"))
        from evaluate import run

        result = run(output, source_distance=case == "map-journey-source-distance")
    elif case.startswith("study-"):
        from study import run

        result = run(output, case=case, fresh_b4=fresh_b4, expanded_routes=expanded_routes)
    else:
        if case == "worker-database":
            from database_check import run

            result = run(output, case=case)
        else:
            from connected_check import run

            result = run(output, case=case, terrain=terrain)
    result["source_unchanged"] = source() == expected
    result["passed"] = bool(result["passed"] and result["source_unchanged"])
    write(output / "worker-result.json", result)
    return 0 if result["passed"] else 1


def launch(
    case,
    output,
    reference_reuse=None,
    *,
    fresh_b4=False,
    expanded_routes=False,
    terrain=None,
    journey=None,
    charging_profile=None,
):
    from windows_resources import Sampler

    output.mkdir(parents=True)
    if journey is not None:
        write(output / "journey.json", journey)
    if charging_profile is not None:
        write(output / "profile.json", charging_profile)
    if reference_reuse is not None:
        write(output / "reference-reuse.json", {"folder": reference_reuse})
    report = {"case": case, "passed": False, "workload_started": False, "automatic_retry": False}
    if fresh_b4:
        report["fresh_b4"] = True
    if expanded_routes:
        report["expanded_routes"] = True
    if terrain is not None:
        report["terrain"] = terrain
    sampler, child, browser, guard = None, None, None, None
    invocation = uuid4().hex
    try:
        print(
            "Checking active Ubuntu, storage, and the unchanged 2 GiB Windows reserve...",
            flush=True,
        )
        report["preflight"] = preflight(output)
        if not report["preflight"]["passed"]:
            report["status"] = "blocked_by_host_preflight"
            raise RuntimeError("Host preflight blocked this attempt")
        before = source()
        write(output / "source.json", before)
        guard = WindowsSmokeGuard(output / "linux-guard.json", live_clock=True)
        sampler = Sampler(output / "windows-resources.jsonl", on_sample=guard.observe)
        sampler.start()
        deadline = time.monotonic() + 6
        while not guard.first_sample_validated:
            if sampler.error or guard.failure or time.monotonic() > deadline:
                raise RuntimeError("Runtime memory guard did not admit the session")
            time.sleep(0.1)
        write(
            output / "launch.json",
            {
                "case": case,
                "created": time.time(),
                "fresh_b4": fresh_b4,
                "expanded_routes": expanded_routes,
                "terrain": terrain,
                "charging_profile": (
                    hashlib.sha256((output / "profile.json").read_bytes()).hexdigest()
                    if charging_profile is not None
                    else None
                ),
            },
        )
        relative = output.relative_to(ROOT).as_posix()
        command = [
            sys.executable,
            "scripts/linux.py",
            "--run-id",
            invocation,
            "--timeout",
            "1200",
            "--",
            ".runtime/energy-v2/venv/bin/python",
            "scripts/route_aware_energy/qualify.py",
            "--case",
            case,
            "--output",
            relative,
            "--worker",
        ]
        if fresh_b4:
            command.append("--fresh-b4")
        if expanded_routes:
            command.append("--expanded-routes")
        if terrain is not None:
            command.extend(("--terrain-manifest", terrain["path"]))
        print(f"Starting one {case} session. Logs: {relative}/worker.log", flush=True)
        with (output / "worker.log").open("xb") as log:
            child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
            report["workload_started"] = True
            while child.poll() is None:
                if sampler.error or guard.failure:
                    raise RuntimeError(str(sampler.error or guard.failure))
                if (
                    case == "connected"
                    and (output / "browser-ready.json").is_file()
                    and browser is None
                ):
                    from smoke_policy import verify_browser_tools, verify_chromium_binary

                    report["browser_tools"] = {
                        "playwright": verify_browser_tools(),
                        "chromium": verify_chromium_binary(),
                    }
                    with (output / "browser.log").open("xb") as browser_log:
                        browser = subprocess.Popen(
                            ["node.exe", "energy-route.mjs", str(output)],
                            cwd=ROOT / "qualification/browser",
                            env={
                                **os.environ,
                                "PLAYWRIGHT_BROWSERS_PATH": str(
                                    ROOT / ".runtime/playwright-browsers"
                                ),
                            },
                            stdout=browser_log,
                            stderr=browser_log,
                        )
                if browser is not None and browser.poll() not in (None, 0):
                    raise RuntimeError("Connected browser check failed; inspect browser.log")
                time.sleep(0.5)
        result = json.loads((output / "worker-result.json").read_bytes())
        report["worker"] = result
        report["passed"] = child.returncode == 0 and result["passed"] and source() == before
        report["status"] = "passed" if report["passed"] else "failed_or_incomplete"
    except (Exception, KeyboardInterrupt) as exc:
        report.update(
            passed=False, status=report.get("status", "failed_or_incomplete"), error=repr(exc)
        )
    finally:
        try:
            if child and child.poll() is None:
                subprocess.run(
                    [sys.executable, "scripts/linux.py", "--cancel", invocation],
                    cwd=ROOT,
                    timeout=20,
                    check=True,
                )
                child.wait(timeout=90)
            if browser and browser.poll() is None:
                (output / "browser-stop").touch()
                browser.wait(timeout=15)
        except Exception as exc:
            report.update(passed=False, cleanup_error=repr(exc))
        finally:
            finish_host_guard(report, sampler, guard)
            if not report["passed"] and report.get("status") != "blocked_by_host_preflight":
                report["status"] = "failed_or_incomplete"
            write(output / "qualification.json", report)
            print(f"{case}: {report.get('status', 'incomplete')}; report: {output}", flush=True)
    return (
        0 if report["passed"] else 2 if report.get("status") == "blocked_by_host_preflight" else 1
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=CASES)
    parser.add_argument("--output")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--reference-reuse")
    parser.add_argument("--terrain-manifest")
    parser.add_argument("--charging-profile")
    parser.add_argument("--journey", nargs="+", help="Prepared real OSM journeys, run sequentially")
    study = parser.add_mutually_exclusive_group()
    study.add_argument("--fresh-b4", action="store_true")
    study.add_argument("--expanded-routes", action="store_true")
    args = parser.parse_args()
    if (args.fresh_b4 or args.expanded_routes) and not args.case.startswith("study-"):
        parser.error("Study selection applies only to the independent study")
    if args.reference_reuse and args.case not in ("study-development", "study-validation"):
        parser.error("Only development/validation reference trips can be reused explicitly")
    if args.terrain_manifest and args.case != "connected":
        parser.error("Terrain input applies only to connected verification")
    charging_cases = ("charging-plant", "charging-plant-outage", "charging-plant-recovery")
    if args.charging_profile and (args.worker or args.case not in charging_cases):
        parser.error("A charging profile applies only to the Windows charging plant check")
    charging_profile = None
    if args.case in charging_cases and not args.worker:
        if not args.charging_profile:
            parser.error("--charging-profile is required")
        sys.path.insert(0, str(ROOT / "scripts/geographic"))
        from live_inputs import check, local_path, read

        charging_profile = read(local_path(args.charging_profile))
        check(charging_profile)
        if "charging_assignment" not in charging_profile:
            parser.error("Use a profile with a reviewed station assignment")
        if args.case == "charging-plant-recovery" and "held_recovery_probe" not in charging_profile:
            parser.error("The isolated recovery case requires its sealed held-state inputs")
    terrain = terrain_input(args.terrain_manifest)
    journey = None
    map_cases = ("map-journey", "map-journey-source-distance")
    if args.journey:
        if args.case not in map_cases:
            parser.error("--journey applies only to map-journey")
        journey = []
        for name in args.journey:
            folder = (ROOT / name).resolve(strict=True)
            if not folder.is_relative_to(ROOT / "evidence/energy-v2"):
                parser.error("Prepared journeys must stay in project evidence storage")
            journey.append(
                {
                    "folder": folder.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256((folder / "heights.json").read_bytes()).hexdigest(),
                    "inputs": {
                        name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                        for name in (
                            "heights.json",
                            "mission.json",
                            "stops.json",
                            "roads.json",
                            "geometry.json",
                            "roads.osm.xml",
                            "elevated-route.net.xml",
                            "elevated-plant.net.xml",
                        )
                    },
                }
            )
    if args.case in map_cases and not args.worker and journey is None:
        parser.error("--journey is required")
    name = args.output or (
        "evidence/energy-v2/"
        + args.case
        + "-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid4().hex[:6]
    )
    output = (ROOT / name).resolve()
    if output.parent != ROOT / "evidence/energy-v2":
        parser.error("Use a fresh folder directly under evidence/energy-v2")
    if args.worker:
        if os.name == "nt" or not output.is_dir():
            parser.error("The guarded Windows launcher must create the session")
        return worker(
            args.case,
            output,
            fresh_b4=args.fresh_b4,
            expanded_routes=args.expanded_routes,
            terrain=terrain,
        )
    if os.name != "nt" or output.exists():
        parser.error("Start from Windows and preserve all earlier result folders")
    import msvcrt

    lock_path = ROOT / ".runtime/p6-monitoring.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            return launch(
                args.case,
                output,
                args.reference_reuse,
                fresh_b4=args.fresh_b4,
                expanded_routes=args.expanded_routes,
                terrain=terrain,
                journey=journey,
                charging_profile=charging_profile,
            )
        finally:
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
