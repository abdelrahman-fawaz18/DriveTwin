"""Executable workload arithmetic and fail-closed metric calculations, not a load test."""

import argparse
import json
import math
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def counts(workload):
    assets, signals = workload["assets"], workload["signals_per_asset"]
    if (
        isinstance(assets, bool)
        or isinstance(signals, bool)
        or not isinstance(assets, int)
        or not isinstance(signals, int)
        or min(assets, signals) <= 0
    ):
        raise ValueError("Asset/signal counts must be positive integers")
    total = 0
    for segment in workload["segments"]:
        duration, rate = segment["duration_s"], segment["messages_per_asset_s"]
        if not all(
            isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in (duration, rate)
        ):
            raise ValueError("Segments require positive integral seconds and rates")
        total += assets * duration * rate
    if sum(s["duration_s"] for s in workload["segments"]) != workload["duration_s"]:
        raise ValueError("Segment durations do not match declared workload duration")
    return {"events": total, "scalar_samples": total * signals}


def percentile(values, fraction):
    numbers = list(values)
    if not numbers or not 0 < fraction <= 1:
        raise ValueError("A percentile requires observations and a fraction in (0, 1]")
    if any(
        isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0
        for n in numbers
    ):
        raise ValueError("Metric values must be finite nonnegative numbers")
    ordered = sorted(numbers)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def latency_assessment(values_s, expected_events, uncertainty_s, max_uncertainty_s=0.05):
    numbers = list(values_s)
    if (
        not isinstance(expected_events, int)
        or isinstance(expected_events, bool)
        or expected_events <= 0
        or not math.isfinite(uncertainty_s)
        or uncertainty_s < 0
    ):
        raise ValueError("Invalid latency context")
    if len(numbers) != expected_events:
        return {"status": "fail", "reason": "missing_or_duplicate_event_measurements"}
    p95, p99 = percentile(numbers, 0.95), percentile(numbers, 0.99)
    if uncertainty_s > max_uncertainty_s:
        return {"status": "inconclusive", "reason": "unqualified_cross_clock_uncertainty"}
    return {
        "status": "pass" if p95 + uncertainty_s <= 2 and p99 + uncertainty_s <= 5 else "fail",
        "p95_s": p95,
        "p99_s": p99,
        "uncertainty_s": uncertainty_s,
    }


def planning_event(spec):
    return {
        "schema_version": "future-operational-profile-NOT-ADMITTED",
        "catalog_version": "future-monitoring-catalog-NOT-ADMITTED",
        "event_id": str(uuid.UUID(int=1)),
        "asset_id": "bus-001",
        "source_id": "sim-adapter",
        "session_id": str(uuid.UUID(int=2)),
        "stream_seq": 1,
        "run_id": str(uuid.UUID(int=3)),
        "mode": "simulation",
        "clock_domain": "simulation",
        "observed_at": "2026-11-03T00:00:00.000000Z",
        "sim_time_s": 1.0,
        "samples": [
            {
                "signal_id": s["signal_id"],
                "value": s["planning_value"],
                "unit": s["unit"],
                "quality": "valid",
                "provenance": "simulated",
            }
            for s in spec["signals"]
        ],
    }


def inspect_spec(spec):
    ids = [s["signal_id"] for s in spec["signals"]]
    if len(ids) != 20 or len(set(ids)) != 20 or spec["held_back_extension"] in ids:
        raise ValueError("Require 20 distinct initial scalars and a separate held-back extension")
    totals = {name: counts(workload) for name, workload in spec["workloads"].items()}
    payload = json.dumps(planning_event(spec), separators=(",", ":"), allow_nan=False).encode()
    limits = spec["limits"]
    maximum = limits["declared_workload_event_max_bytes"]
    if len(payload) > maximum or maximum > limits["wire_profile_max_bytes"]:
        raise ValueError("Declared planning payload exceeds its byte budget")
    per_asset_minimum_events = limits["spool_minimum_nominal_s"]
    guaranteed_bytes = per_asset_minimum_events * maximum
    if guaranteed_bytes > limits["spool_encoded_limit_bytes_per_asset"]:
        raise ValueError("Nominal spool capacity is arithmetically impossible")
    return {
        "passed": True,
        "scope": "Specification arithmetic and encoded example only",
        "workloads": totals,
        "planning_event_bytes": len(payload),
        "maximum_declared_event_bytes": maximum,
        "nominal_30min_max_encoded_bytes_per_asset": guaranteed_bytes,
        "nominal_30min_max_encoded_bytes_all_10_assets": guaranteed_bytes * 10,
        "spool_disk_budget_bytes_per_asset": limits["spool_disk_budget_bytes_per_asset"],
        "performance_targets_measured": False,
        "planning_payload_admitted_by_current_receiver": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect_spec(json.loads((ROOT / "benchmarks/workloads.json").read_text()))
    if args.output:
        path = args.output.resolve()
        if not path.is_relative_to(ROOT / "evidence/p0") or path.exists():
            parser.error("Use a new report path under evidence/p0")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
