"""Verify the measured local Phase 4 gates and write a sanitized closeout record."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_D2 = "d8d62a8"
CASES = {
    "mission": None,
    "interrupted": None,
    "lost-grant": None,
    "override": "operator_override",
    "thermal": "local_protection",
    "local": "local_protection",
    "stale": "charging_observation_stale_or_future",
    "wall-expiry": "elapsed_deadline",
    "simulation-expiry": "charging_expired_or_early",
    "changed-station": "charging_lineage_mismatch",
}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def inside(path):
    resolved = (ROOT / path).resolve()
    require(resolved.is_relative_to(ROOT), "Evidence must remain in EV-DT")
    return resolved


def load(path):
    return json.loads(inside(path).read_bytes())


def digest(path):
    return hashlib.sha256(inside(path).read_bytes()).hexdigest()


def check_hashes(inventory, revision=None):
    require(inventory, "Missing fingerprint inventory")
    for name, sha in inventory.items():
        inside(name)
        actual = (
            digest(name)
            if revision is None
            else hashlib.sha256(
                subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=ROOT)
            ).hexdigest()
        )
        require(actual == sha, f"Fingerprint changed: {name}")


def review(automatic, operator, matched, shared):
    a, b, d = (
        load(f"docs/verification/{name}.json")
        for name in (
            "p4-station-forecast-reserved",
            "p4-offline-charging-advisor",
            "p4-registered-charging-and-dynamic",
        )
    )
    require(
        a["passed"] and a["pre_drive_seal_verified"] and a["reserved_drives_completed"] == 6,
        "Frozen approach qualification is incomplete",
    )
    check_hashes(
        {
            a["release"]: a["release_sha256"],
            a["private_qualification"]: a["private_qualification_sha256"],
            a["private_prediction_seal"]: a["private_prediction_seal_sha256"],
        }
    )
    check_hashes(load(a["release"])["source_sha256"])
    require(
        b["passed"]
        and b["decision_count"] == 10
        and any(item["reason"] == "no_feasible_station" for item in b["timeline"]),
        "Offline selection and replanning evidence is incomplete",
    )
    check_hashes(b["source_sha256"] | {b["private_replay"]: b["private_replay_sha256"]})
    check_hashes({row["path"]: row["sha256"] for row in d["source_files"]}, ORIGINAL_D2)
    check_hashes(d["private_report_sha256"])
    require(d["comparison"]["bounded_parked_fallback"], "Parked outage fallback did not qualify")
    c = load("evidence/p4/charging-missions-d-regression/qualification.json")
    require(
        all(
            c[key]
            for key in ("fixture_passed", "protection_checks_passed", "adverse_checks_passed")
        ),
        "Local adverse-mission or protection checks failed",
    )
    check_hashes(c["source_sha256"])

    ar, op, comparison, engineering = (
        load(path) for path in (automatic, operator, matched, shared)
    )
    require(
        ar["passed"]
        and ar["complete_automatic_matrix"]
        and ar["outcomes"] == dict.fromkeys(CASES, True),
        "The complete automatic matrix is required",
    )
    require(
        op["passed"]
        and op["outcomes"] == {"mission": True, "interrupted": True}
        and not op["complete_automatic_matrix"],
        "Current default operator mode was not requalified",
    )
    require(
        comparison["passed_for_measured_fixture"]
        and comparison["shared_departure_power_verified"]
        and comparison["comparison_mode"] == "common-departure-only",
        "Common-departure comparison did not qualify",
    )
    require(
        engineering["passed"] and engineering["checks"]["contracts"]["mismatches"] == 0,
        "Shared engineering checks failed",
    )
    for report in (ar, op, comparison, engineering):
        check_hashes(report["source_sha256"])

    case_dir = inside(automatic).parent
    files = [inside(path) for path in (automatic, operator, matched)]
    case_outcomes = {}
    for case, expected in CASES.items():
        report = case_dir / case / "qualification.json"
        files.append(report)
        result = load(report)
        require(
            result["passed"] and result["cleanup_passed"] and result["registered_inputs_verified"],
            f"Failed or incomplete case: {case}",
        )
        require(result["effect_observed"] == (case == "mission"), f"Unexpected effect: {case}")
        if case == "mission":
            require(
                result["automatic_authority_verified"] and result["durable_claim_verified"],
                "Automatic authority and durable effect are both required",
            )
            case_outcomes[case] = {
                "completed": True,
                "approach_forecast": result["approach_forecast"],
            }
        else:
            require(
                result["no_measured_effect"] and result["denial"] == expected,
                f"Missing refusal proof: {case}",
            )
            require(
                result["actuation_granted"] == (case == "lost-grant"), f"Unexpected grant: {case}"
            )
            case_outcomes[case] = {
                key: result.get(key)
                for key in ("denial", "actuation_granted", "recovery_denial", "no_measured_effect")
            }

    mission = load(case_dir / "mission/mission/result.json")
    require(
        mission["completed"]
        and mission["resumed_after_charging"]
        and mission["target_reached"]
        and not mission["reserve_violation"],
        "Automatic mission failed its physical acceptance",
    )
    case_outcomes["mission"].update(
        {
            key: mission[key]
            for key in (
                "station_charged",
                "charge_delivered_kwh",
                "minimum_kwh",
                "target_observed_kwh",
                "terminal_sim_s",
                "physical_steps_reconciled",
                "max_balance_residual_j",
            )
        }
    )
    metrics = (
        "completed",
        "terminal_reason",
        "delay_vs_reference_s",
        "station_charged",
        "charge_delivered_kwh",
        "minimum_kwh",
        "reserve_violation",
        "depleted",
        "target_reached",
        "replans",
        "operator_fixture_interventions",
        "external_rescue_interventions",
    )
    compared = {
        case: {arm: {key: row[key] for key in metrics} for arm, row in arms.items()}
        for case, arms in comparison["outcomes"].items()
    }
    require(
        set(compared) == {"departure-outage", "in-service-outage"}
        and all(set(arms) == {"advisor", "nearest", "stationfinder"} for arms in compared.values()),
        "The full matched mission ledger is required",
    )

    source_files = ar["source_sha256"] | comparison["source_sha256"]
    return {
        "checkpoint": "P4-D3 Bounded automatic dispatch and Phase 4 closeout",
        "phase_4_complete": True,
        "phase_5_ready": True,
        "scope": "Local single-bus, stationary-departure, isolated simulation qualification",
        "automatic_dispatch_approval": "Explicit approval for the isolated profile only; see ADR 0063",
        "automatic_cases": case_outcomes,
        "operator_mode_requalified_and_automatic_default_off": True,
        "matched_departure": {
            "reserve_kwh": comparison["reserve_kwh"],
            "stationfinder_settings": comparison["outcomes"]["departure-outage"]["stationfinder"][
                "arm_settings"
            ],
            "reference_terminal_sim_s": comparison["reference_terminal_sim_s"],
            "outcomes": compared,
        },
        "shared_engineering_passed": True,
        "private_report_sha256": {
            path.relative_to(ROOT).as_posix(): digest(path) for path in files
        },
        "shared_engineering_report_sha256": digest(shared),
        "source_files": [{"path": name, "sha256": sha} for name, sha in source_files.items()],
        "next_checkpoint": "P5-A1 Motor data audit and thermal qualification design",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("automatic", "operator", "matched", "shared", "output"):
        parser.add_argument("--" + flag, required=True, type=Path)
    args = parser.parse_args()
    summary = review(args.automatic, args.operator, args.matched, args.shared)
    with inside(args.output).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print("Phase 4 local evidence verified; Phase 5 entry is ready.")


if __name__ == "__main__":
    main()
