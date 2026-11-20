"""Develop, freeze, and score the separate synthetic thermal advisory protocol."""

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy
from synthetic_thermal_faults import generate, scenarios
from thermal_advisory import ThermalAdvisory

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "qualification/thermal/synthetic-fault-protocol-1.0.0.json"
RELEASE = ROOT / "qualification/thermal/synthetic-advisory-release-1.0.0.json"
CLAIM = ROOT / "evidence/p5/synthetic-advisory-1.0.0-reserved-claim.json"
THERMAL = {"cooling_consistent", "ambiguous"}
ALERT = THERMAL | {"sensor_suspect"}
DUAL_COOLING = {"cooling_loss", "cooling_dropout"}
WARNING = DUAL_COOLING | {"single_probe_cooling"}


def read(path):
    return json.loads(path.read_bytes())


def write(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, indent=2, allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime():
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def sources():
    paths = (
        POLICY,
        Path(__file__),
        *(
            Path(__file__).parent / name
            for name in (
                "thermal_network.py",
                "thermal_advisory.py",
                "synthetic_thermal_faults.py",
                "check_rtamt_thermal_rule.py",
            )
        ),
        ROOT / "tests/thermal/test_thermal_advisory.py",
    )
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in paths}


def first(states, after, accepted):
    return next((i for i in range(after, len(states)) if states[i] in accepted), None)


def alert_count(states):
    return sum(
        state in ALERT and (index == 0 or states[index - 1] not in ALERT)
        for index, state in enumerate(states)
    )


def evaluate_case(scenario, observations, truth, policy, rules):
    warmup = policy["warmup_s"]
    monitor = ThermalAdvisory(rules, warmup)
    decisions = [monitor.step(sample) for sample in observations]
    states = [advice.state for advice in decisions]
    end_prefault = len(states) if truth.onset_s is None else truth.onset_s
    alarm_episodes = alert_count(states[warmup:end_prefault])
    warning = (
        None if truth.cooling_started_s is None else first(states, truth.cooling_started_s, THERMAL)
    )
    cooling = (
        None
        if truth.cooling_started_s is None
        else first(states, truth.cooling_started_s, {"cooling_consistent"})
    )
    warning_required = scenario.kind in WARNING
    crossed = (
        warning_required
        and truth.hot_limit_s is not None
        and truth.matched_healthy_limit_s is None
        and truth.hot_limit_s >= truth.cooling_started_s
    )
    attribution_end = truth.bias_started_s if truth.bias_started_s is not None else len(states)
    allowed_attribution = scenario.kind in (DUAL_COOLING | {"cooling_bias"})
    unexpected = sum(
        state == "cooling_consistent"
        and not (allowed_attribution and truth.cooling_started_s <= i < attribution_end)
        for i, state in enumerate(states)
    )
    unavailable_violations = 0
    recovered_cooling = None
    if truth.unavailable is not None:
        start, end = truth.unavailable
        unavailable_violations = sum(state != "data_unavailable" for state in states[start:end])
        if scenario.kind == "stale":
            stale_at = start + rules["maximum_age_s"]
            unavailable_violations += sum(d.reason != "stale" for d in decisions[stale_at:end])
        if scenario.kind == "cooling_dropout":
            recovered_cooling = first(states, end, {"cooling_consistent"})
            if recovered_cooling is None or recovered_cooling < end + rules["persistence_s"]:
                unavailable_violations += 1

    required_advice = {
        "step_bias": "sensor_suspect",
        "cooling_bias": "sensor_suspect",
        "slow_bias": "ambiguous",
        "single_probe_cooling": "ambiguous",
    }.get(scenario.kind)
    required_at = None
    missed_ambiguity_or_sensor = False
    if required_advice:
        start = truth.bias_started_s if required_advice == "sensor_suspect" else truth.onset_s
        deadline = (
            1
            if required_advice == "sensor_suspect"
            else policy["limits"]["maximum_cooling_delay_s"]
        )
        required_at = first(states, start, {required_advice})
        missed_ambiguity_or_sensor = required_at is None or required_at - start > deadline

    if scenario.kind == "cooling_dropout" and recovered_cooling is not None:
        cooling = recovered_cooling
    return {
        "kind": scenario.kind,
        "seed": scenario.seed,
        "fault_onset_s": truth.onset_s,
        "cooling_onset_s": truth.cooling_started_s,
        "bias_onset_s": truth.bias_started_s,
        "unavailable": truth.unavailable,
        "remaining_housing_conductance_fraction": truth.severity,
        "first_thermal_advice_s": warning,
        "first_eligible_cooling_s": cooling,
        "required_advice": required_advice,
        "required_advice_s": required_at,
        "missed_sensor_or_ambiguity": missed_ambiguity_or_sensor,
        "healthy_or_prefault_alert_episodes": alarm_episodes,
        "unexpected_cooling_attribution_samples": unexpected,
        "unavailable_window_violations": unavailable_violations,
        "ambiguous_samples": states.count("ambiguous"),
        "matched_healthy_limit_s": truth.matched_healthy_limit_s,
        "fault_hot_limit_s": truth.hot_limit_s,
        "lead_eligible_crossing": crossed,
        "thermal_warning_lead_s": truth.hot_limit_s - warning
        if crossed and warning is not None
        else None,
    }


def evaluate(fixtures, policy, candidate):
    rules = {**policy["common_rules"], **candidate}
    rows = [
        evaluate_case(scenario, inputs, truth, policy, rules)
        for scenario, inputs, truth in fixtures
    ]
    healthy = [row for row in rows if row["kind"] == "healthy"]
    healthy_hours = len(healthy) * (policy["duration_s"] - policy["warmup_s"]) / 3600
    alarms = sum(row["healthy_or_prefault_alert_episodes"] for row in healthy)
    pre_fault_alarms = sum(
        row["healthy_or_prefault_alert_episodes"] for row in rows if row["kind"] != "healthy"
    )
    dual = [row for row in rows if row["kind"] in DUAL_COOLING]
    delays = [
        row["first_eligible_cooling_s"] - row["cooling_onset_s"]
        for row in dual
        if row["first_eligible_cooling_s"] is not None
    ]
    crossed = [row for row in rows if row["lead_eligible_crossing"]]
    leads = [
        row["thermal_warning_lead_s"]
        for row in crossed
        if row["thermal_warning_lead_s"] is not None
    ]
    metrics = {
        "case_count": len(rows),
        "healthy_hours": healthy_hours,
        "healthy_alert_episodes": alarms,
        "healthy_alerts_per_hour": alarms / healthy_hours,
        "prefault_alert_episodes": pre_fault_alarms,
        "dual_cooling_cases": len(dual),
        "missed_dual_cooling": len(dual) - len(delays),
        "maximum_cooling_delay_s": max(delays) if delays else None,
        "fault_induced_threshold_crossings": len(crossed),
        "warnings_before_crossing": sum(x > 0 for x in leads),
        "minimum_thermal_warning_lead_s": min(leads) if leads else None,
        "unexpected_cooling_attribution_samples": sum(
            row["unexpected_cooling_attribution_samples"] for row in rows
        ),
        "missed_sensor_or_ambiguity_cases": sum(row["missed_sensor_or_ambiguity"] for row in rows),
        "unavailable_window_violations": sum(row["unavailable_window_violations"] for row in rows),
        "ambiguous_case_count": sum(row["ambiguous_samples"] > 0 for row in rows),
        "ambiguity_by_case": {
            kind: sum(row["kind"] == kind and row["ambiguous_samples"] > 0 for row in rows)
            for kind in policy["cases_per_partition"]
        },
    }
    limits = policy["limits"]
    checks = {
        "false_alert_rate": metrics["healthy_alerts_per_hour"]
        <= limits["maximum_healthy_alerts_per_hour"],
        "prefault_alerts": pre_fault_alarms <= limits["maximum_pre_fault_alerts"],
        "missed_cooling": metrics["missed_dual_cooling"] <= limits["maximum_missed_dual_cooling"],
        "cooling_delay": bool(delays) and max(delays) <= limits["maximum_cooling_delay_s"],
        "thermal_lead": len(crossed) >= limits["minimum_fault_induced_threshold_crossings"]
        and len(leads) == len(crossed)
        and min(leads) >= limits["minimum_thermal_warning_lead_s"],
        "attribution": metrics["unexpected_cooling_attribution_samples"]
        <= limits["maximum_unexpected_cooling_attributions"],
        "sensor_and_ambiguity": metrics["missed_sensor_or_ambiguity_cases"]
        <= limits["maximum_missed_sensor_or_ambiguity_cases"],
        "unavailable": metrics["unavailable_window_violations"]
        <= limits["maximum_unavailable_window_violations"],
    }
    return {
        "candidate": candidate,
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "cases": rows,
    }


def collect(policy, partition):
    return [(case, *generate(case, policy)) for case in scenarios(policy, partition)]


def develop(policy, output):
    before = sources()
    fixtures = collect(policy, "development")
    results = [
        evaluate(fixtures, policy, candidate) for candidate in policy["development_candidates"]
    ]
    passing = [result for result in results if result["passed"]]
    selected = (
        min(
            passing,
            key=lambda result: (
                result["metrics"]["healthy_alert_episodes"],
                -result["metrics"]["minimum_thermal_warning_lead_s"],
            ),
        )
        if passing
        else None
    )
    if sources() != before:
        raise ValueError("Development sources changed during evaluation")
    write(
        output,
        {
            "source_sha256": before,
            "runtime": runtime(),
            "results": results,
            "selected": None if selected is None else selected["candidate"],
        },
    )
    for result in results:
        print(result["candidate"]["name"], result["passed"], result["checks"])
    print("Selected:", None if selected is None else selected["candidate"]["name"])
    return 0 if selected else 1


def freeze(policy, report_path, oracle_path):
    report, oracle = read(report_path), read(oracle_path)
    oracle_source = ROOT / "scripts/thermal/check_rtamt_thermal_rule.py"
    if (
        report["source_sha256"] != sources()
        or report["runtime"] != runtime()
        or report["selected"] is None
        or not oracle["passed"]
        or oracle["source_sha256"] != digest(oracle_source)
        or oracle["development_report_sha256"] != digest(report_path)
        or oracle["persistence_s"] != report["selected"]["persistence_s"]
    ):
        raise ValueError("A passing development report and temporal comparison are required")
    development_seeds = {case.seed for case in scenarios(policy, "development")}
    reserved_seeds = {case.seed for case in scenarios(policy, "reserved")}
    if development_seeds & reserved_seeds:
        raise ValueError("Development and reserved scenario seeds overlap")
    write(
        RELEASE,
        {
            "version": "synthetic-thermal-advisory/1.0.0",
            "frozen_utc": datetime.now(timezone.utc).isoformat(),
            "selected": report["selected"],
            "limits": policy["limits"],
            "runtime": runtime(),
            "source_sha256": report["source_sha256"],
            "development_report_sha256": digest(report_path),
            "temporal_report_sha256": digest(oracle_path),
            "reserved_case_count": len(reserved_seeds),
        },
    )
    print(f"Frozen {RELEASE.relative_to(ROOT)}. Commit it before reserved evaluation.")
    return 0


def reserved(policy, output):
    release = read(RELEASE)
    committed = subprocess.check_output(
        ["git", "show", f"HEAD:{RELEASE.relative_to(ROOT).as_posix()}"], cwd=ROOT
    )
    if (
        committed != RELEASE.read_bytes()
        or release["runtime"] != runtime()
        or release["source_sha256"] != sources()
        or release["limits"] != policy["limits"]
    ):
        raise ValueError("Frozen advisory release or source bytes differ")
    write(
        CLAIM,
        {
            "state": "reserved_attempt_consumed",
            "release_sha256": digest(RELEASE),
            "output": output.relative_to(ROOT).as_posix(),
        },
    )
    result = evaluate(collect(policy, "reserved"), policy, release["selected"])
    if (
        result["metrics"]["case_count"] != release["reserved_case_count"]
        or sources() != release["source_sha256"]
    ):
        raise ValueError("Reserved inventory or source bytes differ")
    result["release_sha256"] = digest(RELEASE)
    write(output, result)
    print("Reserved synthetic advisory evaluation completed. Passed:", result["passed"])
    print(result["checks"])
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("develop", "freeze", "reserved", "inspect"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--oracle", type=Path)
    args = parser.parse_args()
    if args.mode == "inspect":
        if not CLAIM.exists():
            print("No reserved synthetic advisory evaluation has been claimed.")
            return 0
        path = ROOT / read(CLAIM)["output"]
        print(
            f"Passed: {read(path)['passed']}. Full report: {path.relative_to(ROOT)}"
            if path.exists()
            else "Reserved claim exists. Evaluation report is incomplete."
        )
        return 0
    if args.mode in ("freeze", "reserved") and CLAIM.exists():
        parser.error("The reserved evaluation has been claimed. Use inspect.")
    if args.report is None:
        parser.error("Choose a report path under evidence/p5")
    path = args.report.resolve()
    if not path.is_relative_to(ROOT / "evidence/p5") or path.exists() != (args.mode == "freeze"):
        parser.error("Use an existing development report for freeze or a fresh private run report")
    policy = read(POLICY)
    if args.mode == "develop":
        return develop(policy, path)
    if args.mode == "freeze":
        if args.oracle is None or not args.oracle.resolve().is_relative_to(ROOT / "evidence/p5"):
            parser.error("Choose the passing RTAMT report under evidence/p5")
        return freeze(policy, path, args.oracle.resolve())
    return reserved(policy, path)


if __name__ == "__main__":
    raise SystemExit(main())
