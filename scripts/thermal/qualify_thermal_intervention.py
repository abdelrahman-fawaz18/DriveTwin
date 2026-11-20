"""Score matched thermal missions and gate one reserved evaluation on a committed freeze."""

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import scipy
from thermal_missions import ARMS, TRACE_COLUMNS, Plant, scenarios, simulate

ROOT = Path(__file__).resolve().parents[2]
THERMAL = ROOT / "qualification/thermal"
PROTOCOL = THERMAL / "synthetic-intervention-protocol-1.0.0.json"
RELEASE = THERMAL / "synthetic-intervention-release-1.0.0.json"
FAULT_PROTOCOL = THERMAL / "synthetic-fault-protocol-1.0.0.json"
ADVISORY = THERMAL / "synthetic-advisory-release-1.0.0.json"
ADVISORY_RESULT = ROOT / "docs/verification/synthetic-advisory.json"
BENCH_RESULT = ROOT / "docs/verification/bench-thermal.json"
CLAIM = ROOT / "evidence/p5/thermal-intervention-reserved-1.0.0.claim.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(data, output, indent=2, allow_nan=False)
        output.write("\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def committed_bytes(path):
    return subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT)


def runtime():
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "jsonschema": version("jsonschema"),
    }


def sources():
    files = [
        PROTOCOL,
        ADVISORY,
        ADVISORY_RESULT,
        BENCH_RESULT,
        FAULT_PROTOCOL,
        *map(
            ROOT.joinpath,
            (
                "scripts/thermal/thermal_network.py",
                "scripts/thermal/thermal_advisory.py",
                "scripts/thermal/thermal_intervention.py",
                "scripts/thermal/thermal_missions.py",
                "scripts/thermal/qualify_thermal_intervention.py",
                "src/evdt/command_timing.py",
                "src/evdt/contract_profile.py",
                "src/evdt/config.py",
                "src/evdt/energy/charging_dispatch.py",
                "contracts/profile.json",
                "tests/thermal/test_thermal_intervention.py",
                "tests/charging/test_charging_dispatch.py",
                "uv.lock",
            ),
        ),
    ]
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in files}


def advisory_rules():
    fault, release, result, bench = map(
        read, (FAULT_PROTOCOL, ADVISORY, ADVISORY_RESULT, BENCH_RESULT)
    )
    if (
        result["passed"] is not True
        or bench["passed"] is not True
        or result["release_sha256"] != digest(ADVISORY)
        or any(digest(ROOT / path) != sha for path, sha in release["source_sha256"].items())
    ):
        raise ValueError("Passing and source-compatible P5-B/C qualification is required")
    return {**fault["common_rules"], **release["selected"]}, fault["warmup_s"]


def score(rows, policy, granted_s, command_error):
    """Read temperatures, power, and progress from the plant trace, independent of receipts."""
    time_s, motor, _, base_w, pump_w, derated, work = rows.T
    boost = policy["boost"]
    on = np.flatnonzero(pump_w > 0)
    valid_trace = (
        np.all(np.isfinite(rows))
        and np.array_equal(time_s, np.arange(len(rows)))
        and np.all((pump_w == 0) | (pump_w == boost["aux_power_w"]))
        and not np.any(derated[on])
        and (
            (granted_s is None and not len(on))
            or (
                granted_s is not None
                and 0 < len(on) <= boost["duration_s"]
                and np.array_equal(on, np.arange(granted_s, granted_s + len(on)))
            )
        )
    )
    complete = bool(work[-1] >= policy["mission_work_s"])
    return {
        "status": "failed"
        if command_error or not valid_trace
        else ("complete" if complete else "incomplete"),
        "trace_valid": bool(valid_trace),
        "command_error": command_error,
        "granted_s": granted_s,
        "boost_s": len(on),
        "peak_k": float(motor.max()),
        "exposure_k_min": float(
            np.maximum(motor - policy["scoring"]["exposure_threshold_k"], 0).sum() / 60
        ),
        "energy_kwh": float((base_w + pump_w).sum() / 3_600_000),
        "pump_kwh": float(pump_w.sum() / 3_600_000),
        "elapsed_s": len(rows),
        "derated_s": int(derated.sum()),
        "mission_delay_s": len(rows) - policy["mission_work_s"] if complete else None,
    }


def pump_effect(case, rows, target, policy):
    """Replay with the observed load protection schedule and the boost disabled."""
    comparison = Plant(case, policy)
    for tick in range(target):
        comparison.advance(tick, False, bool(rows[tick, 5]))
    return float(comparison.temperature[0] - rows[target, 1])


def evaluate(policy, partition, output, rules, warmup):
    files = {}
    records = {}
    baseline = {}
    errors = []
    censored = 0
    forecast = policy["forecast"]
    cases = scenarios(policy, partition)

    def run(case, arm, *, qualified):
        rows, forecasts, granted_s, error = simulate(
            case, arm, policy, rules, warmup, forecast_qualified=qualified
        )
        path = output / f"{case.seed}-{arm}.npz"
        np.savez_compressed(
            path, values=rows, columns=TRACE_COLUMNS, forecast=np.asarray(forecasts)
        )
        files[path.name] = digest(path)
        records[case.seed][arm] = score(rows, policy, granted_s, error)
        return rows, forecasts

    for case in cases:
        records[case.seed] = {"case": case.kind}
        rows, forecasts = run(case, "local_only", qualified=False)
        baseline[case.seed] = rows
        for tick, predicted in forecasts:
            target = tick + forecast["horizon_s"]
            if target < len(rows):
                errors.append(abs(predicted - rows[target, 1]))
            else:
                censored += 1
    forecast_metrics = {
        "count": len(errors),
        "censored_at_mission_end": censored,
        "mae_k": float(np.mean(errors)) if errors else None,
        "coverage": float(np.mean(np.asarray(errors) <= forecast["interval_half_width_k"]))
        if errors
        else None,
    }
    qualified = (
        forecast_metrics["count"] >= forecast["minimum_forecasts"]
        and forecast_metrics["mae_k"] <= forecast["maximum_mae_k"]
        and forecast_metrics["coverage"] >= forecast["minimum_interval_coverage"]
    )
    write(output / "forecast-gate.json", {"passed": qualified, **forecast_metrics})
    limits = policy["scoring"]
    for arm in ARMS[1:]:
        if arm == "predictive" and not qualified:
            for case in cases:
                records[case.seed][arm] = {"status": "blocked_by_forecast_gate"}
            continue
        for case in cases:
            rows, _ = run(case, arm, qualified=qualified)
            result = records[case.seed][arm]
            granted = result["granted_s"]
            target = granted + limits["effect_after_grant_s"] if granted is not None else None
            result["effect_k"] = (
                pump_effect(case, rows, target, policy)
                if target is not None and target < len(rows)
                else None
            )
            result["closed_loop_effect_k"] = (
                float(baseline[case.seed][target, 1] - rows[target, 1])
                if target is not None and target < min(len(rows), len(baseline[case.seed]))
                else None
            )
    summaries = {}
    for arm in ARMS:
        results = [row[arm] for row in records.values()]
        observed = [r for r in results if "trace_valid" in r]
        cooling = [row[arm] for row in records.values() if row["case"] == "cooling_loss"]
        cooling = [r for r in cooling if "trace_valid" in r]
        effects = [r["effect_k"] for r in cooling if r.get("effect_k") is not None]
        completed_cooling = [r for r in cooling if r["mission_delay_s"] is not None]
        summaries[arm] = {
            "complete": sum(r["status"] == "complete" for r in results),
            "incomplete": sum(r["status"] == "incomplete" for r in results),
            "failed": sum(r["status"] == "failed" for r in results),
            "blocked": len(results) - len(observed),
            "additional_incomplete": sum(
                row[arm]["status"] == "incomplete" and row["local_only"]["status"] == "complete"
                for row in records.values()
            ),
            "grants": sum(r["granted_s"] is not None for r in observed),
            "unsafe_grants": sum(
                row[arm].get("granted_s") is not None
                for row in records.values()
                if row["case"] != "cooling_loss"
            ),
            "qualified_effects": sum(effect >= limits["minimum_effect_k"] for effect in effects),
            "minimum_measured_effect_k": min(effects) if effects else None,
            "cooling_loss_means": {
                key: float(np.mean([r[key] for r in subset])) if subset else None
                for key, subset in (
                    ("peak_k", cooling),
                    ("exposure_k_min", cooling),
                    ("energy_kwh", cooling),
                    ("pump_kwh", cooling),
                    ("derated_s", cooling),
                    ("mission_delay_s", completed_cooling),
                )
            },
        }
    checks = {
        "forecast_qualified_before_predictive": qualified,
        "all_traces_and_commands_valid": all(
            summary["failed"] <= limits["maximum_command_failures"] and not summary["blocked"]
            for summary in summaries.values()
        ),
        "independent_effect": all(
            summaries[arm]["qualified_effects"] >= limits["minimum_effect_cases_per_policy"]
            for arm in ARMS[1:]
        ),
        "no_unsafe_grants": all(
            summary["unsafe_grants"] <= limits["maximum_unsafe_grants"]
            for summary in summaries.values()
        ),
        "no_additional_incomplete_missions": all(
            summaries[arm]["additional_incomplete"] <= limits["maximum_additional_incomplete"]
            for arm in ARMS[1:]
        ),
    }
    return {
        "partition": partition,
        "passed": all(checks.values()),
        "checks": checks,
        "forecast": forecast_metrics,
        "scenarios": len(cases),
        "arms": summaries,
        "missions": records,
        "trace_sha256": files,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("develop", "freeze", "reserved"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--development", type=Path)
    args = parser.parse_args()
    policy = read(PROTOCOL)
    rules, warmup = advisory_rules()
    fingerprints = sources()
    dev, held = (
        {case.seed for case in scenarios(policy, key)} for key in ("development", "reserved")
    )
    if dev & held:
        raise ValueError("Development and reserved seeds overlap")
    if args.action == "freeze":
        if args.development is None:
            parser.error("--development is required to freeze a passing report")
        report = read(args.development)
        if (
            report["partition"] != "development"
            or report["passed"] is not True
            or report["source_sha256"] != fingerprints
            or report["runtime"] != runtime()
        ):
            raise ValueError("Passing source-compatible development evidence is required")
        write(
            RELEASE,
            {
                "version": policy["version"],
                "frozen_utc": datetime.now(timezone.utc).isoformat(),
                "source_sha256": fingerprints,
                "runtime": runtime(),
                "development_report_sha256": digest(args.development),
                "reserved_case_count": len(held),
            },
        )
        print("Release frozen. Commit it before evaluating reserved scenarios.")
        return 0
    if args.output is None:
        parser.error("--output is required for evaluations")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "evidence/p5") or output.exists():
        raise ValueError("Choose a fresh folder below evidence/p5")
    release_sha = None
    if args.action == "reserved":
        release = read(RELEASE)
        if (
            committed_bytes(RELEASE.relative_to(ROOT).as_posix()) != RELEASE.read_bytes()
            or release["source_sha256"] != fingerprints
            or release["runtime"] != runtime()
            or release["reserved_case_count"] != len(held)
            or any(
                hashlib.sha256(committed_bytes(path)).hexdigest() != sha
                for path, sha in fingerprints.items()
            )
        ):
            raise ValueError("Frozen release, runtime, or source bytes differ")
        release_sha = digest(RELEASE)
        write(
            CLAIM,
            {
                "state": "reserved_attempt_consumed",
                "release_sha256": release_sha,
                "output": output.relative_to(ROOT).as_posix(),
            },
        )
    output.mkdir(parents=True)
    result = evaluate(
        policy, "development" if args.action == "develop" else "reserved", output, rules, warmup
    )
    if sources() != fingerprints:
        raise ValueError("Source bytes changed during evaluation")
    result.update(runtime=runtime(), source_sha256=fingerprints, release_sha256=release_sha)
    write(output / "result.json", result)
    print(
        json.dumps({key: result[key] for key in ("passed", "checks", "forecast", "arms")}, indent=2)
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
