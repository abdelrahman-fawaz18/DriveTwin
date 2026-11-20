"""Independent fitting examples, causal historical plans and honest uncertainty."""

import importlib.util
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from evdt.energy.calibration import (
    MotionTemplate,
    TemplatePoint,
    TrainingTrip,
    assumed_plan,
    fit_grid,
    joint_parameter_pairs,
    select_template,
    sensitivity_envelope,
)
from evdt.energy.model import DrivingInterval, EnergyParameters, estimate_energy
from evdt.energy.observations import EnergyPoint, ObservationScope, fingerprint

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def parameters():
    return EnergyParameters(200, 1000, 0, 2, 0, 0.01, 1, 0.9, 0.5, 100_000, 100_000, 10)


def make_trip(name, speeds, parameters):
    scope = ObservationScope("simulation", name, "bus", "source", name, 1, "simulation")
    energy = 100.0
    distance = 0
    points = []
    for i, speed in enumerate(speeds):
        if i:
            result = estimate_energy(
                parameters, (DrivingInterval(1, speeds[i - 1], speed, 0, 100),), energy / 200
            )
            energy = result.final_energy_kwh
            distance += (speeds[i - 1] + speed) / 2
        points.append(
            EnergyPoint(
                scope,
                f"{name}-{i}",
                i,
                float(i),
                energy * 3_600_000,
                distance,
                energy / 200,
                speed,
                ("simulated-observation",) * 4,
            )
        )
    return TrainingTrip(name, "train", tuple(points), 100)


@pytest.fixture
def trips(parameters):
    return (
        make_trip("a", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 10], parameters),
        make_trip("b", [0, 1, 2, 3, 3, 3, 3, 3, 2, 1, 0, 0, 0, 0, 0], parameters),
    )


def test_grid_recovers_known_effective_parameters_without_truth_inputs(trips, parameters):
    base = replace(parameters, rolling_resistance_coefficient=0.006, propulsion_efficiency=0.8)
    result = fit_grid(trips, base, (0.006, 0.01, 0.014), (0.8, 0.9, 1.0))
    assert result.parameters.rolling_resistance_coefficient == 0.01
    assert result.parameters.propulsion_efficiency == 0.9
    assert result.training_mse_kwh2 < 1e-18
    assert result == fit_grid(trips, base, (0.006, 0.01, 0.014), (0.8, 0.9, 1.0))
    assert len(result.leave_one_trip_out_parameters) == 2
    assert len(result.candidate_scores) == 9


def test_non_training_trip_and_mixed_session_are_refused(trips):
    with pytest.raises(ValueError):
        replace(trips[0], partition="validation")
    points = list(trips[0].points)
    points[-1] = replace(points[-1], scope=replace(points[-1].scope, session_id="other"))
    with pytest.raises(ValueError):
        replace(trips[0], points=tuple(points))


@pytest.mark.parametrize(
    "kind", ["empty", "duplicate", "bad_efficiency", "bad_rolling", "huge_grid"]
)
def test_fit_configuration_and_family_inputs_are_bounded(trips, parameters, kind):
    rolling = (0.01,)
    efficiency = (0.9,)
    if kind == "empty":
        trips = ()
    elif kind == "duplicate":
        trips = (trips[0], trips[0])
    elif kind == "bad_efficiency":
        efficiency = (1.1,)
    elif kind == "bad_rolling":
        rolling = (-0.1,)
    else:
        rolling = tuple(range(257))
    with pytest.raises(ValueError):
        fit_grid(trips, parameters, rolling, efficiency)


def test_template_is_training_motion_only_and_ignores_energy_truth(trips):
    original = select_template(trips)
    modified = tuple(
        replace(t, points=tuple(replace(p, energy_j=17, soc=0.1) for p in t.points)) for t in trips
    )
    assert select_template(modified) == original
    assert original.training_mission_id in {t.mission_id for t in trips}
    assert len(original.training_content_sha256) == 64


def test_assumed_plan_depends_on_current_inputs_not_actual_future(trips):
    template = select_template(trips)
    plan = assumed_plan(
        template,
        current_distance_m=0,
        current_speed_m_s=2,
        remaining_distance_m=200,
        auxiliary_power_w=500,
    )
    assert plan[0].start_speed_m_s == 2
    assert all(a.end_speed_m_s == b.start_speed_m_s for a, b in zip(plan, plan[1:]))
    assert math.fsum(
        0.5 * (p.start_speed_m_s + p.end_speed_m_s) * p.duration_s for p in plan
    ) == pytest.approx(200)
    assert all(p.auxiliary_power_w == 500 for p in plan)
    # The plan interface has no target/future-trip argument, and its identity uses
    # only the frozen training template and declared current assumptions.
    assert plan == assumed_plan(
        template,
        current_distance_m=0,
        current_speed_m_s=2,
        remaining_distance_m=200,
        auxiliary_power_w=500,
    )


def test_long_template_stays_within_worker_interval_budget():
    points = tuple(TemplatePoint(float(i), float(i) * 10, 10) for i in range(2500))
    template = MotionTemplate("test/1", "training-only", fingerprint("trace"), 24990, points)
    plan = assumed_plan(
        template,
        current_distance_m=0,
        current_speed_m_s=10,
        remaining_distance_m=24990,
        auxiliary_power_w=0,
    )
    assert len(plan) <= 900
    assert sum(p.duration_s for p in plan) == pytest.approx(2499)


def test_sensitivity_is_an_explicit_scenario_range_not_a_confidence_claim(parameters):
    policy = json.loads((ROOT / "qualification/energy/development-protocol.json").read_bytes())[
        "sensitivity"
    ]
    plan = (DrivingInterval(100, 10, 10, 0, 1000),)
    envelope = sensitivity_envelope(parameters, plan, 0.5, policy)
    point = estimate_energy(parameters, plan, 0.5)
    assert (
        envelope.minimum_energy_kwh
        <= point.initial_energy_kwh - point.final_energy_kwh
        <= envelope.maximum_energy_kwh
    )
    assert envelope.minimum_final_soc <= point.final_soc <= envelope.maximum_final_soc
    assert envelope.probability_guarantee is False


def test_joint_scenarios_preserve_training_tradeoff_and_leave_out_choices(trips, parameters):
    fit = fit_grid(trips, parameters, (0.006, 0.01, 0.014), (0.8, 0.9, 1.0))
    pairs = joint_parameter_pairs(fit, relative_mse_slack=0.1, absolute_mse_slack_kwh2=0.0025)
    assert (0.01, 0.9) in pairs
    assert set(fit.leave_one_trip_out_parameters) <= set(pairs)
    assert all(pair in {(r, e) for _, r, e in fit.candidate_scores} for pair in pairs)
    policy = json.loads((ROOT / "qualification/energy/development-protocol.json").read_bytes())[
        "sensitivity"
    ]
    envelope = sensitivity_envelope(
        parameters, (DrivingInterval(100, 10, 10, 0, 1000),), 0.5, policy, parameter_pairs=pairs
    )
    assert (
        envelope.method == "training-joint-grid-envelope/1.0.0"
        and not envelope.probability_guarantee
    )
    with pytest.raises(ValueError):
        sensitivity_envelope(parameters, (), 0.5, policy, parameter_pairs=())


def test_validation_metrics_count_failures_and_use_percentage_points():
    path = ROOT / "scripts/known_route_energy/analyze_energy_development.py"
    spec = importlib.util.spec_from_file_location("energy_validation_metrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    targets = json.loads((ROOT / "qualification/energy/development-protocol.json").read_bytes())[
        "proposed_D18_targets"
    ]
    rows = [
        {
            "mission_id": "one",
            "status": "advisory",
            "baseline_status": "advisory",
            "energy_error_kwh": 0.5,
            "soc_error_pp": 0.2,
            "baseline_energy_error_kwh": 1,
            "joint_covered": True,
            "energy_width_kwh": 1,
            "soc_width_pp": 1,
            "refinement_relative": 0.001,
        }
    ]
    result = module.summary(rows, targets)
    assert (
        result["targets_met"]
        and result["energy_mae_kwh"] == 0.5
        and result["soc_mae_percentage_points"] == 0.2
    )
    rows.append({"mission_id": "failed", "status": "unavailable", "baseline_status": "unavailable"})
    result = module.summary(rows, targets)
    assert not result["targets_met"] and result["failed_or_unavailable"] == ["failed"]
    assert result["joint_envelope_coverage"] == 0.5


@pytest.mark.parametrize(
    "tamper", [None, "model", "job", "predictions", "source", "targets", "held_out_flag"]
)
def test_actual_frozen_artifact_guards_detect_tampering(tmp_path, monkeypatch, tamper):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/known_route_energy"))
    spec = importlib.util.spec_from_file_location(
        "release_integrity_check", ROOT / "scripts/known_route_energy/qualify_energy_calibration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)

    def write(name, data):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")

    write("source.json", {"code": "fixed"})
    write("qualification/energy/development-protocol.json", {"proposed_D18_targets": {"budget": 1}})
    params = {"example": 1}
    template = fingerprint("historical-training-template")
    write(
        "model.json",
        {
            "training_only": True,
            "model_qualified": False,
            "parameters": params,
            "template_sha256": template,
        },
    )
    write("targets.json", {"targets": {"budget": 1}, "automatic_dispatch_approved": False})
    write(
        "evaluation-protocol.json",
        {
            "required_model_sha256": module.sha(tmp_path / "model.json"),
            "required_targets_sha256": module.sha(tmp_path / "targets.json"),
        },
    )
    planned = {"parameters": params, "mission": {"plan_version": template}}
    write("trip-planned-job.json", planned)
    write("trip-baseline-job.json", {})
    predictions = [
        {
            "mission_id": "trip",
            "candidate": {"job_sha256": fingerprint(planned)},
            "baseline": {"job_sha256": fingerprint({})},
        }
    ]
    write("predictions.json", predictions)
    write(
        "validation.json",
        {
            "summary": {"checked": True},
            "predictions_sha256": module.sha(tmp_path / "predictions.json"),
        },
    )
    q = {
        "passed": True,
        "held_out_generated_or_scored": False,
        "source_sha256": {"source.json": module.sha(tmp_path / "source.json")},
        "frozen_artifact_sha256": {
            n: module.sha(tmp_path / n)
            for n in ("model.json", "targets.json", "evaluation-protocol.json")
        },
        "model_parameters": params,
        "validation": {"checked": True},
    }
    write("qualification.json", q)
    if tamper is None:
        assert module.check_frozen(tmp_path)["passed"]
        return
    if tamper == "held_out_flag":
        q["held_out_generated_or_scored"] = True
        write("qualification.json", q)
    else:
        name = {
            "model": "model.json",
            "job": "trip-planned-job.json",
            "predictions": "predictions.json",
            "source": "source.json",
            "targets": "targets.json",
        }[tamper]
        write(name, {"modified": True})
    with pytest.raises(ValueError):
        module.check_frozen(tmp_path)
