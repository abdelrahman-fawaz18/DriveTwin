"""Freeze P6-A2 inputs and apply launch and live resource guards."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from host_memory_policy import Guard, assess, values
from measurement_report import read_rows
from workload_profile import select

from evdt.workload.correlation import LinuxResourceSamples, ResourceTimeline

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "benchmarks/release-monitoring-baseline.json"
RELEASE = "benchmarks/release-monitoring-smoke.json"
CAPACITY_RELEASE = "benchmarks/release-monitoring-capacity.json"
SMOKE = "monitoring-smoke"
CAPACITY = ("W0-30", "W1-30")
CAPACITY_GROWTH = {"W0-30": 4 * 1024**3, "W1-30": 8 * 1024**3}
CAPACITY_WORKER_TIMEOUT_S = 3000
INPUT_PATHS = (
    ".dockerignore",
    "apps/dashboard",
    "benchmarks/history.json",
    BASELINE,
    "benchmarks/workloads.json",
    "benchmarks/monitoring-host.json",
    "catalog",
    "ops/compose/compose.monitoring.yaml",
    "ops/compose/compose.extension.yaml",
    "ops/compose/compose.recovery.yaml",
    "containers/monitoring.Dockerfile",
    "containers/sumo-debian.sources",
    "contracts",
    "data/public/montreal-core-v1",
    "data/public/montreal-backdrop-v1",
    "environments/engineering-images.json",
    "environments/dataflow-images.json",
    "migrations",
    "pyproject.toml",
    "qualification/browser",
    "qualification/energy/product-profile.json",
    "qualification/energy/product-profile-1.1.0.json",
    "qualification/energy/station-navigation.json",
    "qualification/extension",
    "qualification/workload",
    "scripts/linux.py",
    "scripts/monitoring",
    "scripts/release_checks",
    "simulation/montreal",
    "simulation/montreal-bus-002",
    "simulation/montreal-history",
    "simulation/montreal-sustained",
    "src",
    "uv.lock",
)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, timeout=30)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def baseline():
    return json.loads((ROOT / BASELINE).read_bytes())


def inventory(paths=INPUT_PATHS):
    listing = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *paths)
    paths = sorted(set(listing.decode("utf-8").rstrip("\0").split("\0")))
    fingerprints = {}
    for name in paths:
        path = ROOT / name
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"Missing or redirected smoke input: {name}")
        fingerprints[name] = sha(path.read_bytes())
    return {
        "files": len(fingerprints),
        "sha256": sha(json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()),
    }


def capacity_inventory():
    return inventory(
        (*INPUT_PATHS, "scripts/run-release-monitoring.ps1", "scripts/windows_awake.py")
    )


def verify_sources(*, committed=True, workload=SMOKE):
    if workload not in (SMOKE, *CAPACITY):
        raise ValueError("Unknown Phase 6 monitoring profile")
    capacity = workload in CAPACITY
    path = CAPACITY_RELEASE if capacity else RELEASE
    raw = (ROOT / path).read_bytes()
    release = json.loads(raw)
    if capacity:
        if (
            release.get("version") != "phase6-monitoring-capacity/1.0.0"
            or release.get("checkpoint") != "P6-B1"
            or release.get("projected_growth_bytes") != CAPACITY_GROWTH
        ):
            raise ValueError("Unreviewed P6-B1 capacity release")
    elif (
        release.get("version") != "phase6-monitoring-smoke/1.0.0"
        or release.get("projected_smoke_growth_bytes") != 4 * 1024**3
    ):
        raise ValueError("Unreviewed P6-A2 smoke release")
    if committed and git("show", f"HEAD:{path}") != raw:
        raise ValueError("Commit the reviewed Phase 6 release before launching")
    frozen = baseline()
    if release["baseline_sha256"] != sha((ROOT / BASELINE).read_bytes()) or any(
        not (ROOT / name).is_file() or sha((ROOT / name).read_bytes()) != expected
        for name, expected in frozen["reference_inputs_sha256"].items()
    ):
        raise ValueError(
            "Frozen release inputs differ from the recorded baseline. "
            "Use the recorded development revision for this qualification."
        )
    observed = capacity_inventory() if capacity else inventory()
    if observed != release["reviewed_source_inventory"]:
        raise ValueError("Phase 6 source bytes or input inventory differ from the reviewed release")
    return {"passed": True, "release_sha256": sha(raw), "input_inventory": observed}


def smoke_profile():
    spec = baseline()
    workload = spec["sequence"][0]
    if (
        workload
        != {
            "checkpoint": "P6-A2",
            "run": "monitoring-smoke",
            "assets": 1,
            "measured_s": 30,
            "segments": [{"duration_s": 30, "messages_per_asset_s": 1}],
            "release_workload": False,
        }
        or spec["warmup_s"] != 60
        or spec["signals_per_asset"] != 20
        or spec["monitoring_profile"]["asset_ids_for_single_bus"] != ["bus-001"]
    ):
        raise ValueError("P6-A2 implements the frozen one-bus smoke profile only")
    profile = select("W0", spec["monitoring_profile"]["browser"])
    return {
        **{key: value for key, value in profile.items() if key != "phase_1_policy_version"},
        "phase_6_policy_version": spec["version"],
        "workload": workload["run"],
        "measured_samples": workload["measured_s"],
        "total_samples": spec["warmup_s"] + workload["measured_s"],
        "full_duration": False,
        "qualification_scope": "P6-A2 one-bus smoke; separate from release workloads",
        "host_memory_policy": "phase6-smoke-reserve/1.0.0",
        "emergency_host_policy": profile["host_memory_policy"],
    }


def monitoring_profile(workload):
    if workload == SMOKE:
        return smoke_profile()
    if workload not in CAPACITY:
        raise ValueError("Unknown Phase 6 monitoring profile")
    spec = baseline()
    count = 1 if workload == "W0-30" else 10
    source_profile = select("W0" if count == 1 else "W1", spec["monitoring_profile"]["browser"])
    matching = [item for item in spec["sequence"] if item["run"] == workload]
    if matching != [
        {
            "checkpoint": "P6-B1",
            "run": workload,
            "assets": count,
            "measured_s": 1800,
            "segments": [{"duration_s": 1800, "messages_per_asset_s": 1}],
            "expected_measured_events": count * 1800,
            "expected_measured_scalar_samples": count * 1800 * 20,
        }
    ] or (
        spec["warmup_s"] != 60
        or spec["signals_per_asset"] != 20
        or spec["monitoring_profile"]["browser"] != "chromium-automated"
        or spec["monitoring_profile"][
            "asset_ids_for_single_bus" if count == 1 else "asset_ids_for_fleet"
        ]
        != source_profile["assets"]
    ):
        raise ValueError("P6-B1 requires the frozen 30-minute monitoring profile")
    return {
        **{key: value for key, value in source_profile.items() if key != "phase_1_policy_version"},
        "phase_6_policy_version": spec["version"],
        "workload": workload,
        "measured_samples": 1800,
        "total_samples": 1860,
        "qualification_scope": "P6-B1 fault-free 30-minute monitoring and host capacity",
        "host_memory_policy": "phase6-capacity-reserve/1.0.0",
        "emergency_host_policy": source_profile["host_memory_policy"],
    }


def verify_browser_tools():
    package = ROOT / "qualification/browser"
    expected = json.loads((package / "package-lock.json").read_bytes())["packages"][
        "node_modules/playwright"
    ]["version"]
    installed = json.loads((package / "node_modules/playwright/package.json").read_bytes())
    if (
        installed.get("version") != expected
        or not (package / "node_modules/playwright/cli.js").is_file()
        or not shutil.which("node.exe")
    ):
        raise ValueError("Pinned Windows Playwright and Node must be available before startup")
    return expected


def verify_chromium_binary():
    package = ROOT / "qualification/browser"
    registry = json.loads((package / "node_modules/playwright-core/browsers.json").read_bytes())
    chromium = next(browser for browser in registry["browsers"] if browser["name"] == "chromium")
    # The qualified Playwright project uses channel=chromium, even when headless.
    executable = Path(
        subprocess.check_output(
            ["node.exe", "-p", "require('playwright').chromium.executablePath()"],
            cwd=package,
            env={
                **os.environ,
                "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".runtime/playwright-browsers"),
            },
            text=True,
            timeout=15,
        ).strip()
    )
    if not executable.is_file() or not executable.is_relative_to(
        ROOT / ".runtime/playwright-browsers" / ("chromium-" + chromium["revision"])
    ):
        raise ValueError("The pinned Chromium executable is missing")
    return chromium["browserVersion"]


def host_precheck(rows, policy):
    host = assess(rows)
    checks = {
        "emergency_guard": host["passed"],
        "valid_sample_span": values(rows[-1])[0] - values(rows[0])[0]
        >= policy["active_ubuntu_valid_sample_span_min_s"],
        "sample_spacing": all(
            0 < values(right)[0] - values(left)[0] <= policy["max_interval_between_samples_s"]
            for left, right in zip(rows, rows[1:])
        ),
        "available_ram": host["available_min_bytes"]
        >= policy["windows_available_physical_min_bytes_each_sample"],
        "commit_headroom": host["commit_headroom_min_bytes"]
        >= policy["windows_commit_headroom_min_bytes_each_sample"],
        "wsl_working_set": all(
            type(row.get("wsl_name_total_bytes")) is int and row["wsl_name_total_bytes"] > 0
            for row in rows
        ),
    }
    return {"passed": all(checks.values()), "checks": checks, "samples": len(rows), "host": host}


def preflight(evidence, *, workload=SMOKE):
    from measurement_preflight import run

    policy = baseline()["host_launch_precheck"]
    reserve = policy["disk_reserve_bytes_in_addition_to_projected_run_growth"]
    result = {"passed": False}
    try:
        # Read-only inventory. The inherited sampler runs last to give a fresh memory check.
        code = (
            "import json,shutil,subprocess;"
            "print(json.dumps({'linux_free_bytes':shutil.disk_usage('/').free,"
            "'running_containers':len(subprocess.check_output(['docker','ps','-q']).splitlines())}))"
        )
        disk = json.loads(
            subprocess.check_output(
                [
                    sys.executable,
                    "scripts/linux.py",
                    "--timeout",
                    "30",
                    "--",
                    "python3",
                    "-c",
                    code,
                ],
                cwd=ROOT,
                timeout=45,
            )
        )
        disk["windows_free_bytes"] = shutil.disk_usage(ROOT).free
        disk["required_free_bytes"] = reserve + CAPACITY_GROWTH.get(workload, 4 * 1024**3)
        result["inventory"] = disk
        result["inventory_passed"] = (
            disk["running_containers"] == 0
            and min(disk["linux_free_bytes"], disk["windows_free_bytes"])
            >= disk["required_free_bytes"]
        )
        sampled = run(evidence)
        if not sampled["passed"]:
            raise RuntimeError("Active Ubuntu resource sample failed; inspect preflight.json")
        result["memory"] = host_precheck(read_rows(evidence / "preflight-windows.jsonl"), policy)
        result["passed"] = result["inventory_passed"] and result["memory"]["passed"]
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    with (evidence / "phase6-preflight.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result


class WindowsSmokeGuard(Guard):
    def __init__(self, linux_failure, *, live_clock=False, allow_gradual_wall_drift=False):
        super().__init__()
        self.linux_failure = linux_failure
        self.reserve = baseline()["host_launch_precheck"][
            "disk_reserve_bytes_in_addition_to_projected_run_growth"
        ]
        self.low_start = None
        self.first_sample_validated = False
        self.timeline = (
            ResourceTimeline(allow_gradual_wall_drift=allow_gradual_wall_drift)
            if live_clock
            else None
        )

    def observe(self, row):
        previous = self.previous
        super().observe(row)
        if self.failure:
            return
        stamp, physical, commit = values(row)
        try:
            if self.timeline is not None:
                self.timeline.observe(row)
            if type(row.get("wsl_name_total_bytes")) is not int or row["wsl_name_total_bytes"] <= 0:
                raise ValueError("Required Ubuntu working-set counter is unavailable")
            if previous is None and (
                physical < self.policy["warning_available_bytes"]
                or commit < self.policy["emergency_commit_headroom_bytes"]
            ):
                raise ValueError("Windows memory fell below the launch floor before startup")
            if physical < self.policy["warning_available_bytes"]:
                if self.low_start is None:
                    self.low_start = previous if previous is not None else stamp
            elif (
                self.low_start is not None
                and stamp - self.low_start <= self.policy["warning_duration_s"]
            ):
                self.low_start = None
            if (
                self.low_start is not None
                and stamp - self.low_start > self.policy["warning_duration_s"]
            ):
                raise ValueError("Windows available RAM below 2 GiB for over 30 seconds")
            if shutil.disk_usage(ROOT).free < self.reserve:
                raise ValueError("Windows disk reserve breached")
            if self.linux_failure.is_file():
                raise ValueError("Linux resource guard stopped the smoke run")
            self.first_sample_validated = True
        except (KeyError, OSError, TypeError, ValueError) as exc:
            self.failure = {"reason": str(exc)}


class LinuxSmokeGuard:
    def __init__(self, *, restartable_services=()):
        self.samples = LinuxResourceSamples(restartable_services=restartable_services)
        self.reserve = baseline()["host_launch_precheck"][
            "disk_reserve_bytes_in_addition_to_projected_run_growth"
        ]

    def observe(self, row):
        if shutil.disk_usage("/").free < self.reserve:
            raise ValueError("Ubuntu disk reserve breached")
        self.samples.observe(row)
        if not self.samples.within_budget:
            raise ValueError("Linux core-memory or rolling swap limit breached")


def launch_receipt(control, services, workload=SMOKE):
    with (control / "phase6-launch.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "target": services.relative_to(ROOT).as_posix(),
                "workload": workload,
                "created": time.time(),
            },
            stream,
        )


def consume_receipt(control, services, workload=SMOKE):
    receipt = control / "phase6-launch.json"
    request = json.loads(receipt.read_bytes())
    if (
        request["target"] != services.relative_to(ROOT).as_posix()
        or request["workload"] != workload
        or not 0 <= time.time() - request["created"] <= 45
    ):
        raise ValueError(
            "Phase 6 requires a fresh precheck for this exact evidence folder and profile"
        )
    receipt.unlink()
