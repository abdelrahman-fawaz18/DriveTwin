"""Compare RTAMT's temporal rule with the advisory state machine on fixed traces."""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path

import rtamt
from thermal_advisory import Reading, Sample, ThermalAdvisory

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "qualification/thermal/synthetic-fault-protocol-1.0.0.json"


def evaluate(rules):
    duration = rules["persistence_s"]
    high = rules["hot_residual_k"]
    case_residual = rules["case_residual_k"]
    traces = {
        "exact_boundary": ["high"] * (duration + 1),
        "too_short": ["high"] * duration,
        "predicate_break": ["high"] * duration + ["low"] + ["high"] * (duration + 1),
        "missing_break": ["high"] * duration + ["missing"] + ["high"] * (duration + 1),
        "housing_loss": ["high"] * (duration + 1) + ["housing_missing"] + ["high"] * (duration + 1),
    }
    comparisons = {}
    formula = f"historically[0,{duration}] (eligible >= 0.5)"
    for name, values in traces.items():
        spec = rtamt.StlDiscreteTimeSpecification()
        spec.declare_var("eligible", "float")
        spec.spec = formula
        spec.parse()
        advisory = ThermalAdvisory(rules, 0)
        reference_ticks, advisory_ticks = [], []
        for tick, state in enumerate(values):
            motor = (
                None
                if state == "missing"
                else Reading(330 + high - (1 if state == "low" else 0), tick)
            )
            housing = None if state == "housing_missing" else Reading(310 + case_residual, tick)
            actual = advisory.step(Sample(tick, motor, housing, 330.0, 310.0)).state
            reference = spec.update(tick, [("eligible", float(state == "high"))])
            if tick >= duration and reference >= 0:
                reference_ticks.append(tick)
            if actual == "cooling_consistent":
                advisory_ticks.append(tick)
        comparisons[name] = {
            "ticks": len(values),
            "rtamt_cooling_ticks": reference_ticks,
            "state_machine_cooling_ticks": advisory_ticks,
            "passed": reference_ticks == advisory_ticks,
        }
    return formula, comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in (args.development, args.output):
        if not path.resolve().is_relative_to(ROOT / "evidence/p5"):
            parser.error("Use the private Phase 5 evidence directory")
    development = json.loads(args.development.read_bytes())
    policy = json.loads(POLICY.read_bytes())
    rules = {**policy["common_rules"], **development["selected"]}
    formula, comparisons = evaluate(rules)
    report = {
        "passed": all(result["passed"] for result in comparisons.values()),
        "rtamt_version": version("rtamt"),
        "persistence_s": rules["persistence_s"],
        "formula": formula,
        "cases": comparisons,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "development_report_sha256": hashlib.sha256(args.development.read_bytes()).hexdigest(),
    }
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"RTAMT comparison passed: {report['passed']}; {len(comparisons)} traces.")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
