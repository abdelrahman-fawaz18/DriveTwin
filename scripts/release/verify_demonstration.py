"""Review matched charging results and follow the saved command to its effect."""

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPARISON_REFERENCE = "docs/verification/stationary-charging-closeout.json"
CHARGE_REFERENCE = "docs/verification/charging-and-cooling-checks.json"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def review_comparison(path):
    actual = read(path)
    reference = read(ROOT / COMPARISON_REFERENCE)["matched_departure"]
    station_release = read(ROOT / "qualification/charging/station-arrival-release-1.0.0.json")
    require(
        actual["passed_for_measured_fixture"]
        and actual["comparison_mode"] == "common-departure-only"
        and actual["evidence_complete"]
        and actual["shared_departure_power_verified"]
        and actual["network_sha256"] == station_release["network_sha256"]
        and actual["reference_terminal_sim_s"] == reference["reference_terminal_sim_s"]
        and actual["reserve_kwh"] == reference["reserve_kwh"]
        and actual["outcomes"].keys() == reference["outcomes"].keys(),
        "Comparison profile, reserve, network or reference differs",
    )
    count = 0
    for case, expected_arms in reference["outcomes"].items():
        require(actual["outcomes"][case].keys() == expected_arms.keys(), "Comparison arms differ")
        for name, expected in expected_arms.items():
            arm = actual["outcomes"][case][name]
            require(
                arm["completed"]
                and not arm["reserve_violation"]
                and not arm["depleted"]
                and {key: arm[key] for key in expected} == expected,
                f"Measured results differ for {case}/{name}",
            )
            count += 1
    return {
        "private_report_sha256": digest(path),
        "reference": COMPARISON_REFERENCE,
        "matched_arms": count,
        "reference_terminal_sim_s": actual["reference_terminal_sim_s"],
        "shared_departure_power_verified": True,
        "all_completed_above_reserve": True,
    }


def review_charge():
    reference = read(ROOT / CHARGE_REFERENCE)["cases"]["charge"]
    folder = ROOT / reference["evidence_directory"]
    for name, expected in reference["private_evidence_sha256"].items():
        require(digest(folder / name) == expected, "Saved charging evidence fingerprint differs")
    case = read(folder / "charge/case.json")
    result = read(folder / "charge/qualification.json")
    effect = read(folder / "charge/independent-charge-effect.json")
    mission = read(folder / "charge/mission/result.json")
    trace = folder / "charge/mission/measurements.jsonl"
    events = {event["event"]: event for event in mission["events"]}
    require(
        result["passed"]
        and result["role_isolation"]
        and result["effect_reconciled"]
        and case["final_queue"] == {"pending": 0, "accepted": 1}
        and mission["completed"]
        and not mission["reserve_violation"]
        and digest(trace) == mission["trace_sha256"] == effect["trace_sha256"]
        and all(case[key] == effect[key] for key in ("run_id", "asset_id", "command_id"))
        and all(
            events[name]["correlation_id"] == case["command_id"]
            for name in ("charging_observed", "target_observed", "motion_resumed")
        ),
        "Saved command, authorized effect, queue or motion trace differs",
    )
    with trace.open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    delivered = math.fsum(row["charge_wh"] for row in rows if row["charge_wh"] > 0) / 1000
    require(
        delivered > 0
        and math.isclose(delivered, effect["delivered_kwh"], abs_tol=1e-9, rel_tol=0)
        and effect["arrived_sim_s"] < effect["target_sim_s"] < effect["resumed_sim_s"]
        and any(row["sim_s"] >= effect["resumed_sim_s"] and row["speed_m_s"] > 0 for row in rows)
        and rows[-1]["battery_wh"] / 1000 == effect["finish_kwh"],
        "Independent measurements do not reproduce the saved charging effect",
    )
    return {
        "reference": CHARGE_REFERENCE,
        "verified_private_files": len(reference["private_evidence_sha256"]),
        "observed_charge_kwh": delivered,
        "observed_at_target_kwh": effect["target_kwh"],
        "motion_resumed": True,
        "command_and_trace_identity_match": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--comparison", type=Path, required=True, help="New matched comparison report"
    )
    args = parser.parse_args()
    path = args.comparison.resolve()
    if not path.is_relative_to(ROOT) or path.name != "qualification.json":
        parser.error("Choose a comparison qualification.json inside the project")
    print(
        json.dumps(
            {
                "passed": True,
                "comparison": review_comparison(path),
                "command_effect": review_charge(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
