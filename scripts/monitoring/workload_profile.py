"""User-approved short-run acceptance profiles; never sustained-load proof."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BROWSERS = ("windows-edge-visible", "chromium-automated")
PHASE_1_POLICY = {
    "policy_version": "phase1-local-monitoring/1.3.1",
    "workloads": ["W0", "W1"],
    "measured_duration_s": {"W0": 120, "W1": 300},
    "warmup_s": 60,
    "host_memory_policy": "phase1-local-host/1.0.1",
}


def select(name, browser):
    if name not in ("rehearsal", "W0", "W1") or browser not in BROWSERS:
        raise ValueError("Unknown workload or qualified browser")
    frozen = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())
    if (
        frozen.get("profile_version") != "1.3.1"
        or frozen.get("phase_1_acceptance") != PHASE_1_POLICY
    ):
        raise ValueError("Require the user-approved short-run Phase 1 workload policy")
    warmup = frozen["metric_policy"]["window_warmup_s"]
    spec = frozen["workloads"]["W1" if name == "rehearsal" else name]
    if (
        warmup != 60
        or spec["duration_s"]
        != PHASE_1_POLICY["measured_duration_s"]["W1" if name == "rehearsal" else name]
        or spec["assets"] != (1 if name == "W0" else 10)
        or spec["signals_per_asset"] != 20
        or spec["segments"] != [{"duration_s": spec["duration_s"], "messages_per_asset_s": 1}]
    ):
        raise ValueError("The runner does not implement a changed frozen profile")
    duration = 30 if name == "rehearsal" else PHASE_1_POLICY["measured_duration_s"][name]
    return {
        "workload_spec_version": frozen["profile_version"],
        "phase_1_policy_version": PHASE_1_POLICY["policy_version"],
        "workload": name,
        "browser": browser,
        "assets": [f"bus-{i:03}" for i in range(1, spec["assets"] + 1)],
        "warmup_samples": warmup,
        "measured_samples": duration,
        "total_samples": warmup + duration,
        "signals_per_asset": 20,
        "full_duration": name != "rehearsal",
        "qualification_scope": "local short-run monitoring acceptance",
        "host_memory_policy": PHASE_1_POLICY["host_memory_policy"],
        "host_capacity_qualified": False,
        "sustained_load_qualified": False,
        "independent_contexts": False,
        "browser_viewer_policy": "fleet-operator-table/1.0.0",
        "browser_storage_contexts": 1,
        "browser_pages": 1,
        "browser_trace_policy": "action-har/1.0.0",
        "resource_collector_policy": "frozen-cgroup/1.0.0",
        "synchronized_start": True,
    }
