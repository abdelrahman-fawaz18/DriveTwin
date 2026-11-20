"""Offline advisory input/forecast qualification; optional accepted-fixture reuse."""

import argparse
import json
import math
import platform
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.energy.forecasts import (  # noqa: E402
    ForecastClock,
    ForecastPolicy,
    MissionRequest,
    planned_forecast,
    reference_forecast,
)
from evdt.energy.model import DrivingInterval, EnergyParameters  # noqa: E402
from evdt.energy.observations import (  # noqa: E402
    SUPPORTED_ENVELOPE_VERSIONS,
    ObservationScope,
    fingerprint,
)


def sha(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def synthetic_packets(data, scope):
    result = []
    for seq, point in enumerate(data["points"]):
        quantities = [
            ("battery.pack.energy", point["energy_kwh"] * 3_600_000, "J"),
            ("motion.distance", point["distance_m"], "m"),
            ("battery.pack.soc", point["soc"], "1"),
            ("motion.speed", point["speed_m_s"], "m/s"),
        ]
        result.append(
            {
                **asdict(scope),
                "schema_version": "3.0.0",
                "event_id": f"event-{seq}",
                "stream_seq": seq,
                "sim_time_s": point["sim_time_s"],
                "samples": [
                    {
                        "signal_id": name,
                        "value": value,
                        "unit": unit,
                        "quality": "good",
                        "provenance": "simulated-observation",
                    }
                    for name, value, unit in quantities
                ],
            }
        )
    return tuple(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--accepted-fixture", type=Path)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if not output.is_relative_to(ROOT / "evidence/p2") or output.exists():
        parser.error("Choose a new directory under evidence/p2")
    fixture = args.accepted_fixture.resolve() if args.accepted_fixture else None
    if fixture and not fixture.is_relative_to(ROOT / "evidence/p1"):
        parser.error("Recorded fixture must be under evidence/p1")
    output.mkdir(parents=True)
    cases_path = ROOT / "qualification/energy/forecast-cases.json"
    data = json.loads(cases_path.read_bytes())
    if data["version"] != "energy-advisory-cases/1.0.0":
        parser.error("Unsupported advisory case version")
    paths = [
        *sorted((ROOT / "src/evdt/energy").glob("*.py")),
        Path(__file__),
        cases_path,
        *[ROOT / f"contracts/telemetry/{v}.schema.json" for v in SUPPORTED_ENVELOPE_VERSIONS],
    ]
    if fixture:
        paths += [fixture / "qualification.json"]
        paths += [fixture / s["folder"] / "source-report.json" for s in data["recorded_sources"]]
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    report = {
        "passed": False,
        "checkpoint": "P2-A2 offline advisory forecasts",
        "scope": "Analytical input/forecast behavior and optional frozen-record analysis; no mission accuracy or runtime isolation claim",
        "python": platform.python_version(),
        "phase_2_complete": False,
        "synthetic_cases": [],
        "recorded_cases": [],
    }
    try:
        identifiers = [c["id"] for c in data["checks"]]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate case IDs")
        scope = ObservationScope(
            "simulation",
            "analytic-run",
            "analytic-bus",
            "analytic-source",
            "analytic-session",
            1,
            "simulation",
        )
        policy = ForecastPolicy(**data["policy"])
        for case in data["checks"]:
            packets = synthetic_packets(data, scope)
            mutation = case.get("mutation")
            energy = packets[-1]["samples"][0]
            if mutation == "missing_energy":
                packets[-1]["samples"].pop(0)
            elif mutation == "invalid_quality":
                energy["quality"] = "invalid"
            elif mutation == "mixed_asset":
                packets[-1]["asset_id"] = "other-bus"
            elif mutation == "zero_net":
                energy["value"] = packets[0]["samples"][0]["value"]
            elif mutation == "odometer_reset":
                packets[-1]["samples"][1]["value"] = 0
            elif mutation == "lower_charge":
                packets[-1]["samples"][2]["value"] = 0.1
            elif mutation is not None:
                raise ValueError("Unknown mutation")
            clock = ForecastClock(case.get("as_of", 100), "recorded_analysis", "frozen")
            mission = MissionRequest(**{**data["mission"], **case.get("mission_overrides", {})})
            if case["method"] == "reference":
                result = reference_forecast(packets, scope, clock, mission, policy)
            else:
                parameters = EnergyParameters(
                    **{**data["parameters"], **case.get("parameter_overrides", {})}
                )
                plan = tuple(DrivingInterval(**p) for p in case["plan"])
                result = planned_forecast(packets, scope, clock, mission, policy, parameters, plan)
            if result.status != case["expected_status"]:
                raise AssertionError("Wrong status: " + case["id"])
            if result.status == "unavailable":
                assert result.values is None and not result.observation_references
                assert result.reason_codes == (case["expected_reason"],)
            else:
                values = asdict(result.values)
                for key, expected in case.get("expected_values", {}).items():
                    actual = values[key]
                    assert (
                        (actual is expected)
                        if isinstance(expected, bool)
                        else math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8)
                    )
                if "final_energy_must_exceed_kwh" in case:
                    assert (
                        values["projected_final_energy_kwh"] > case["final_energy_must_exceed_kwh"]
                    )
            report["synthetic_cases"].append(
                {"id": case["id"], "passed": True, "forecast": asdict(result)}
            )
        if fixture:
            accepted = json.loads((fixture / "qualification.json").read_bytes())
            assert accepted["passed"] and accepted["extension"]["passed"]
            assert accepted["extension"]["original_packet_and_recipe_bytes"]
            recorded_policy = ForecastPolicy(**data["recorded_policy"])
            for source in data["recorded_sources"]:
                recording = json.loads(
                    (fixture / source["folder"] / "source-report.json").read_bytes()
                )
                assert recording["passed"]
                events = recording["events"]
                assert len(events) == accepted["extension"]["assets"][source["asset_id"]]["events"]
                assert all(fingerprint(e["body"]) == e["wire_sha256"] for e in events)
                packets = tuple(e["body"] for e in events)
                first, last = packets[0], packets[-1]
                scope = ObservationScope(
                    **{n: first[n] for n in ObservationScope.__dataclass_fields__}
                )
                assert scope.run_id == accepted["run_id"] and scope.asset_id == source["asset_id"]
                clock = ForecastClock(last["sim_time_s"], "recorded_analysis", "frozen")
                speed = next(
                    s["value"] for s in last["samples"] if s["signal_id"] == "motion.speed"
                )
                assumption = data["recorded_future_assumption"]
                target = assumption["target_speed_m_s"]
                plan = (
                    DrivingInterval(
                        assumption["transition_s"],
                        speed,
                        target,
                        assumption["grade_ratio"],
                        source["auxiliary_power_w"],
                    ),
                    DrivingInterval(
                        assumption["cruise_s"],
                        target,
                        target,
                        assumption["grade_ratio"],
                        source["auxiliary_power_w"],
                    ),
                )
                distance = sum(
                    0.5 * (p.start_speed_m_s + p.end_speed_m_s) * p.duration_s for p in plan
                )
                mission = MissionRequest(
                    "recorded-" + source["asset_id"],
                    "declared-forward-scenario",
                    "declared-forward/1",
                    distance,
                    source["capacity_kwh"],
                    "declared-capacity/1",
                    assumption["reserve_soc"],
                    "absent",
                )
                parameters = EnergyParameters(
                    **{
                        **data["recorded_declared_parameters"],
                        "declared_capacity_kwh": source["capacity_kwh"],
                        "loaded_mass_kg": source["loaded_mass_kg"],
                    }
                )
                reference = reference_forecast(packets, scope, clock, mission, recorded_policy)
                planned = planned_forecast(
                    packets, scope, clock, mission, recorded_policy, parameters, plan
                )
                assert reference.status == planned.status == "advisory"
                assert not reference.model_qualified and not planned.model_qualified
                report["recorded_cases"].append(
                    {
                        "asset": scope.asset_id,
                        "events": len(events),
                        "canonical_packet_hashes_verified": True,
                        "recorded_origin": True,
                        "actual_future_or_hidden_parameters_used": False,
                        "reference": asdict(reference),
                        "assumed_plan": asdict(planned),
                    }
                )
        report["passed"] = len(report["synthetic_cases"]) == len(data["checks"]) > 0
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    report["source_and_input_sha256"] = before
    if {p.relative_to(ROOT).as_posix(): sha(p) for p in paths} != before:
        report.update(passed=False, error="Sources or input data changed during qualification")
    (output / "qualification.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        f"Advisory forecasts passed: {report['passed']}; synthetic: {len(report['synthetic_cases'])}; recorded assets: {len(report['recorded_cases'])}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
