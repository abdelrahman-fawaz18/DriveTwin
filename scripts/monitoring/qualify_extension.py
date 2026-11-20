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
from functools import partial
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
        ROOT / "scripts/monitoring/extension_scope.py",
        ROOT / "scripts/monitoring/workload_scope.py",
        ROOT / "scripts/monitoring/current_scope.py",
        ROOT / "scripts/monitoring/capacity_scope.py",
        ROOT / "scripts/monitoring/runtime_scope.py",
        ROOT / "scripts/monitoring/profile_catalog.py",
        *ROOT.glob("qualification/workload/*.json"),
        *ROOT.glob("scripts/monitoring/*measurement*.py"),
        ROOT / "scripts/monitoring/windows_resources.py",
        ROOT / "scripts/monitoring/host_memory_policy.py",
        ROOT / "benchmarks/monitoring-host.json",
        ROOT / "scripts/monitoring/linux_resources.py",
        ROOT / "scripts/monitoring/docker_resources.py",
        ROOT / "scripts/monitoring/workload_profile.py",
        ROOT / "scripts/monitoring/extension_fixture.py",
        ROOT / "scripts/monitoring/workload_source_fixture.py",
        ROOT / "benchmarks/workloads.json",
        ROOT / "scripts/monitoring/qualify_workload_sources.py",
        ROOT / "qualification/extension/shared-core.json",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file()
    }


def main(*, workload_sources=False, workload_measurement=False, sustained=False, phase6=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    if phase6:
        from smoke_policy import CAPACITY, CAPACITY_WORKER_TIMEOUT_S, SMOKE

        parser.add_argument("--workload", choices=(SMOKE, *CAPACITY), default=SMOKE)
    elif sustained:
        from workload_profile import BROWSERS, select

        parser.add_argument("--workload", choices=("rehearsal", "W0", "W1"), required=True)
        parser.add_argument("--browser", choices=BROWSERS, required=True)
    args = parser.parse_args()
    if phase6:
        from smoke_policy import (
            WindowsSmokeGuard,
            launch_receipt,
            monitoring_profile,
            preflight,
            verify_browser_tools,
            verify_chromium_binary,
            verify_sources,
        )

        profile = monitoring_profile(args.workload)
        args.browser = profile["browser"]
        verify_shared = partial(verify_sources, workload=args.workload)
        preflight = partial(preflight, workload=args.workload)
    else:
        from current_scope import verify_shared

        profile = select(args.workload, args.browser) if sustained else None
    evidence = args.evidence_dir.resolve()
    evidence_root = ROOT / ("evidence/p6" if phase6 else "evidence/p1")
    if os.name != "nt" or not evidence.is_relative_to(evidence_root) or evidence.exists():
        parser.error(f"Run on Windows with a new directory under {evidence_root.relative_to(ROOT)}")
    evidence.mkdir(parents=True)
    control = ROOT / ".runtime/dashboard" / uuid.uuid4().hex
    control.mkdir(parents=True)
    shared = None if phase6 else verify_shared()
    before = fingerprints()
    record = {
        "passed": False,
        "checkpoint": ("P6-B1" if args.workload in CAPACITY else "P6-A2")
        if phase6
        else "P1-E2-C"
        if sustained
        else "P1-E2-B"
        if workload_measurement
        else "P1-E2-A"
        if workload_sources
        else "P1-D2",
        "source_sha256": before,
        "shared_core": shared,
    }
    if phase6:
        record["fixture_started"] = False
    if profile:
        record["profile"] = profile
    from host_memory_policy import (
        Emergency,
        Guard,
        assess,
        evidence_destination,
        reclaim_setup_cache,
        run_guarded,
    )
    from windows_resources import Sampler

    worker = None
    sampler = None
    guard = (
        WindowsSmokeGuard(
            evidence / "services/linux-guard.json", live_clock=args.workload in CAPACITY
        )
        if phase6
        else Guard()
    )
    worker_launcher_id = uuid.uuid4().hex
    with (control / "worker.log").open("w", encoding="utf-8") as log:
        try:
            if phase6:
                record["shared_core"] = verify_shared()
            if sustained:
                if not phase6:
                    from measurement_preflight import run as preflight

                print("Checking active-Ubuntu local memory safety before the workload", flush=True)
                record["preflight"] = preflight(evidence)
                if not record["preflight"]["passed"]:
                    raise RuntimeError("Active-Ubuntu memory preflight failed; no workload started")
            if phase6:
                record["playwright_version"] = verify_browser_tools()
                if args.workload in CAPACITY:
                    record["pinned_chromium_version"] = verify_chromium_binary()
            sampler = Sampler(evidence / "windows-resources.jsonl", on_sample=guard.observe)
            sampler.start()
            if phase6:
                deadline = time.monotonic() + 5
                while not guard.first_sample_validated and not (guard.failure or sampler.error):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Initial Windows resource sample unavailable")
                    time.sleep(0.1)
                if guard.failure or sampler.error:
                    raise Emergency(json.dumps(guard.failure or {"reason": sampler.error}))
                launch_receipt(control, evidence / "services", args.workload)
            print("Starting the owned live-source dashboard fixture", flush=True)
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/linux.py",
                    "--run-id",
                    worker_launcher_id,
                    "--timeout",
                    str(CAPACITY_WORKER_TIMEOUT_S if args.workload in CAPACITY else 600)
                    if phase6
                    else str(profile["total_samples"] + 1800)
                    if profile
                    else "2400",
                    "--",
                    "python3",
                    "scripts/monitoring/extension_fixture.py",
                    *(
                        ["--workload-measurement"]
                        if workload_measurement
                        else ["--workload-sources"]
                        if workload_sources
                        else []
                    ),
                    *(
                        (
                            ["--phase6-capacity", args.workload]
                            if args.workload in CAPACITY
                            else ["--phase6-smoke"]
                        )
                        if phase6
                        else [
                            "--sustained-workload",
                            args.workload,
                            "--measurement-browser",
                            args.browser,
                        ]
                        if sustained
                        else []
                    ),
                    "--evidence-dir",
                    (evidence.relative_to(ROOT) / "services").as_posix(),
                    "--dashboard-control",
                    control.as_posix().replace("C:/", "/mnt/c/"),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
            )
            if phase6:
                record["fixture_started"] = True
            deadline = time.monotonic() + 900
            while not (control / "ready.json").is_file():
                if guard.failure or sampler.error:
                    raise Emergency(json.dumps(guard.failure or {"reason": sampler.error}))
                if worker.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        "Dashboard fixture did not become ready; see ignored worker log"
                    )
                time.sleep(0.5)
            fixture = json.loads((control / "ready.json").read_text())
            if not phase6:
                record["setup_cache_preparation"] = reclaim_setup_cache()
            if guard.failure or sampler.error:
                raise Emergency(json.dumps(guard.failure or {"reason": sampler.error}))
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
            if workload_measurement:
                env["EVDT_MEASUREMENT_TRACE"] = "action-har/1.0.0"
            print(
                "Testing the actual dashboard in pinned Chromium"
                if phase6
                else "Testing the actual dashboard in visible Edge and automated Chromium",
                flush=True,
            )
            result = run_guarded(
                [
                    node,
                    str(package / "node_modules/playwright/cli.js"),
                    "test",
                    "--config",
                    "dashboard.config.mjs",
                    "fleet-measurement.spec.mjs"
                    if sustained
                    else "measurement.spec.mjs"
                    if workload_measurement
                    else "workload-sources.spec.mjs"
                    if workload_sources
                    else "extension.spec.mjs",
                    *(["--project", args.browser] if sustained else []),
                ],
                cwd=package,
                env=env,
                sampler=sampler,
                guard=guard,
                timeout=profile["total_samples"] + 500
                if profile
                else 650
                if workload_measurement
                else 330,
            )
            (control / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            result_suffix = (
                "-measurement-result.json"
                if workload_measurement
                else "-sources-result.json"
                if workload_sources
                else "-extension-result.json"
            )
            projects = (
                (args.browser,) if sustained else ("windows-edge-visible", "chromium-automated")
            )
            results = [
                json.loads((control / (project + result_suffix)).read_text())
                for project in projects
                if (control / (project + result_suffix)).is_file()
            ]
            if workload_measurement:
                for project in projects:
                    source_result = control / (project + result_suffix)
                    if source_result.is_file():
                        shutil.copyfile(source_result, evidence / source_result.name)
                if sustained:
                    for asset_file in control.glob("*-measurement-asset-*.json"):
                        shutil.copyfile(asset_file, evidence / asset_file.name)
            browser_passed = (
                result.returncode == 0
                and len(results) == len(projects)
                and all(r["passed"] for r in results)
            )
            if phase6 and args.workload in CAPACITY:
                browser_passed = browser_passed and all(
                    item["browser"] == record["pinned_chromium_version"] for item in results
                )
            (control / "browser-result.json").write_text(
                json.dumps({"passed": browser_passed, "projects": results})
            )
            (control / "browser-done").touch()
            if not browser_passed:
                raise RuntimeError("Base dashboard browser checks failed")
            worker_deadline = time.monotonic() + 400
            while worker.poll() is None:
                if guard.failure or sampler.error:
                    raise Emergency(json.dumps(guard.failure or {"reason": sampler.error}))
                if time.monotonic() >= worker_deadline:
                    raise TimeoutError("Owned service worker exceeded its completion deadline")
                time.sleep(0.5)
            services = json.loads((evidence / "services/qualification.json").read_text())
            record.update(
                passed=browser_passed and worker.returncode == 0 and services["passed"],
                browsers=results,
                services_passed=services["passed"],
            )
        except KeyboardInterrupt:
            record.update(passed=False, interrupted=True, error="Interrupted by the operator")
        except Exception as exc:
            record["error"] = str(exc)
        finally:
            if guard.failure or (sampler is not None and sampler.error):
                record["host_emergency"] = guard.failure or {"reason": sampler.error}
                if phase6:
                    record["passed"] = False
            if (record.get("host_emergency") or record.get("interrupted")) and (
                worker is not None and worker.poll() is None
            ):
                subprocess.run(
                    [sys.executable, "scripts/linux.py", "--cancel", worker_launcher_id],
                    cwd=ROOT,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                try:
                    worker.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    record["worker_cancel_unconfirmed"] = True
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
            owner = control / ("ready.json" if (control / "ready.json").is_file() else "owner.json")
            if worker and worker.returncode and owner.is_file():
                # A launcher timeout may stop Python before its Compose finally block.
                fixture_id = uuid.UUID(json.loads(owner.read_text())["run_id"]).hex
                fixture_env = ROOT / ".runtime/p1-uplink" / fixture_id / "compose.env"
                cleanup_argv = [
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
                    "-f",
                    "ops/compose/compose.extension.yaml",
                    *(
                        [
                            "-f",
                            str(
                                (fixture_env.parent / "sources.compose.json").relative_to(ROOT)
                            ).replace("\\", "/"),
                        ]
                        if (workload_sources or workload_measurement)
                        and (fixture_env.parent / "sources.compose.json").is_file()
                        else []
                    ),
                    "-p",
                    (
                        "evdt-e2c-"
                        if sustained
                        else "evdt-e2b-"
                        if workload_measurement
                        else "evdt-e2a-"
                        if workload_sources
                        else "evdt-d2-"
                    )
                    + fixture_id[:12],
                    "--profile",
                    "*",
                    "down",
                    "--volumes",
                    "--remove-orphans",
                ]
                if record.get("host_emergency") or record.get("interrupted"):
                    partial_output = evidence / "services/partial"
                    partial_output.mkdir(parents=True, exist_ok=True)
                    base = cleanup_argv[:-3]
                    subprocess.run(
                        [*base, "stop", "--timeout", "3"],
                        cwd=ROOT,
                        capture_output=True,
                        timeout=45,
                        check=False,
                    )
                    copies = [("sender", "/measurements/.", "traces")]
                    for asset in profile["assets"] if profile else ["bus-001", "bus-002"]:
                        index = int(asset.split("-")[1])
                        copies.append(
                            (
                                "source" if index == 1 else "source" + str(index),
                                "/source-output/.",
                                asset,
                            )
                        )
                    record["partial_copy_results"] = []
                    for service, source_path, target in copies:
                        copied = subprocess.run(
                            [
                                *base,
                                "cp",
                                service + ":" + source_path,
                                (partial_output / target).relative_to(ROOT).as_posix()
                                if phase6
                                else evidence_destination(partial_output / target),
                            ],
                            cwd=ROOT,
                            capture_output=True,
                            timeout=30,
                            check=False,
                        )
                        record["partial_copy_results"].append(
                            {"service": service, "exit_code": copied.returncode}
                        )
                    record["partial_preservation_complete"] = all(
                        item["exit_code"] == 0 for item in record["partial_copy_results"]
                    )
                    (partial_output / "incomplete.json").write_text(
                        json.dumps(
                            {
                                "status": "incomplete",
                                "reason": record.get("host_emergency") or record["error"],
                            }
                        )
                        + "\n"
                    )
                cleanup = subprocess.run(
                    cleanup_argv, cwd=ROOT, capture_output=True, text=True, timeout=120
                )
                (control / "fallback-cleanup.log").write_text(
                    cleanup.stdout + cleanup.stderr, encoding="utf-8"
                )
                record["fallback_cleanup_exit_code"] = cleanup.returncode
                if phase6:
                    label = "com.docker.compose.project=evdt-e2c-" + fixture_id[:12]
                    inventory = {}
                    for kind, command in (
                        ("containers", ["ps", "-aq"]),
                        ("volumes", ["volume", "ls", "-q"]),
                        ("networks", ["network", "ls", "-q"]),
                    ):
                        checked = subprocess.run(
                            [
                                sys.executable,
                                "scripts/linux.py",
                                "--timeout",
                                "30",
                                "--",
                                "docker",
                                *command,
                                "--filter",
                                "label=" + label,
                            ],
                            cwd=ROOT,
                            capture_output=True,
                            text=True,
                            timeout=45,
                            check=False,
                        )
                        inventory[kind] = (
                            checked.stdout.splitlines() if checked.returncode == 0 else None
                        )
                    record["fallback_cleanup_inventory"] = inventory
                    record["fallback_cleanup_verified"] = cleanup.returncode == 0 and all(
                        value == [] for value in inventory.values()
                    )
            if sampler is not None:
                try:
                    sampler.close()
                except Exception as exc:
                    record.update(passed=False, resource_error=str(exc))
            if sampler is not None and (evidence / "windows-resources.jsonl").exists():
                try:
                    from measurement_report import read_rows

                    record["local_host"] = assess(read_rows(evidence / "windows-resources.jsonl"))
                    record["passed"] = record["passed"] and record["local_host"]["passed"]
                except Exception as exc:
                    record.update(passed=False, host_policy_error=str(exc))
            if workload_measurement and record["passed"]:
                from measurement_report import build

                try:
                    if sustained:
                        from full_measurement_report import build as build_full

                        record["measurement"] = build_full(evidence, profile, phase6=phase6)
                        record["passed"] = record["passed"] and record["measurement"]["passed"]
                    else:
                        record["measurement"] = build(evidence)
                except Exception as exc:
                    record.update(passed=False, measurement_error=str(exc))
            try:
                record["shared_core_after"] = verify_shared()
            except Exception as exc:
                record.update(passed=False, core_guard_error=str(exc))
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
