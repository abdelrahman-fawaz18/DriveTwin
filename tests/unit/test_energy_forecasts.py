"""Observation validity, advisory references and explicit scenario/provenance behavior."""

import copy
from dataclasses import asdict, replace

import pytest

from evdt.energy.forecasts import (
    ForecastClock,
    ForecastPolicy,
    MissionRequest,
    planned_forecast,
    reference_forecast,
)
from evdt.energy.model import DrivingInterval, EnergyParameters
from evdt.energy.observations import (
    SUPPORTED_ENVELOPE_VERSIONS,
    InputUnavailable,
    ObservationScope,
    SignalBindings,
    observation_point,
)


@pytest.fixture
def scope():
    return ObservationScope(
        "simulation", "run-a", "bus-a", "source-a", "session-a", 1, "simulation"
    )


@pytest.fixture
def packets(scope):
    result = []
    for sequence, (time, distance, energy, soc) in enumerate(
        [(0, 0, 3, 0.3), (50, 500, 2.5, 0.25), (100, 1000, 2, 0.2)]
    ):
        values = [
            ("battery.pack.energy", energy * 3_600_000, "J"),
            ("motion.distance", distance, "m"),
            ("battery.pack.soc", soc, "1"),
            ("motion.speed", 10, "m/s"),
        ]
        result.append(
            {
                **asdict(scope),
                "schema_version": "3.0.0",
                "event_id": f"event-{sequence}",
                "stream_seq": sequence,
                "sim_time_s": time,
                "samples": [
                    {
                        "signal_id": signal,
                        "value": value,
                        "unit": unit,
                        "quality": "good",
                        "provenance": "simulated-observation",
                    }
                    for signal, value, unit in values
                ],
            }
        )
    return tuple(result)


@pytest.fixture
def policy():
    return ForecastPolicy("test-window/1", 10, 300, 10, 60, 5, 100)


@pytest.fixture
def clock():
    return ForecastClock(100, "recorded_analysis", "frozen")


@pytest.fixture
def mission():
    return MissionRequest(
        "request-a", "mission-a", "plan/1", 1000, 10, "capacity/1", 0.05, "absent"
    )


def sample(packet, signal):
    return next(p for p in packet["samples"] if p["signal_id"] == signal)


def check_unavailable(result, code):
    assert result.status == "unavailable"
    assert result.reason_codes == (code,)
    assert result.values is None
    assert result.observation_references == ()
    assert result.forecast_id is None
    assert result.model_qualified is False


def test_reference_arithmetic_and_conditional_range_time(packets, scope, clock, mission, policy):
    r = reference_forecast(packets, scope, clock, mission, policy)
    assert r.status == "advisory" and r.model_qualified is False
    assert r.uncertainty_method == "not_established"
    v = r.values
    assert v.estimated_initial_energy_kwh == 2
    assert v.recent_net_kwh_per_km == 1
    assert v.recent_average_power_kw == 36
    assert v.net_mission_energy_kwh == 1
    assert v.projected_final_soc == pytest.approx(0.1)
    assert v.conditional_distance_to_reserve_m == 1500
    assert v.conditional_time_to_reserve_s == 150
    assert v.reserve_feasible and v.energy_plan_feasible
    assert len(r.observation_references) == 3
    assert r.input_age_s == 0


def test_insufficient_energy_preserves_deficit_instead_of_only_clamping_soc(
    packets, scope, clock, mission, policy
):
    r = reference_forecast(
        packets, scope, clock, replace(mission, remaining_distance_m=3000), policy
    )
    assert r.values.projected_final_soc == 0
    assert r.values.unserved_energy_kwh == 1
    assert not r.values.energy_plan_feasible and not r.values.reserve_feasible


def test_zero_charge_is_a_valid_observation(packets, scope, clock, mission, policy):
    p = copy.deepcopy(packets)
    sample(p[-1], "battery.pack.energy")["value"] = 0
    sample(p[-1], "battery.pack.soc")["value"] = 0
    r = reference_forecast(p, scope, clock, mission, policy)
    assert r.status == "advisory"
    assert r.values.conditional_time_to_reserve_s == 0
    assert r.values.conditional_distance_to_reserve_m == 0
    assert r.values.unserved_energy_kwh > 0


def test_declared_capacity_is_not_inferred_from_observed_energy_and_soc(
    packets, scope, clock, mission, policy
):
    r = reference_forecast(
        packets, scope, clock, replace(mission, declared_capacity_kwh=20), policy
    )
    assert r.values.estimated_initial_energy_kwh == 4
    assert r.values.projected_final_soc == pytest.approx(0.15)


def test_recent_net_rate_does_not_double_count_intermediate_regeneration(
    packets, scope, clock, mission, policy
):
    p = copy.deepcopy(packets)
    sample(p[1], "battery.pack.energy")["value"] = 4 * 3_600_000
    r = reference_forecast(p, scope, clock, mission, policy)
    assert r.values.recent_net_kwh_per_km == 1


def test_identical_inputs_repeat_and_policy_or_assumption_changes_change_identity(
    packets, scope, clock, mission, policy
):
    r = reference_forecast(packets, scope, clock, mission, policy)
    assert r == reference_forecast(copy.deepcopy(packets), scope, clock, mission, policy)
    assert (
        r.forecast_id
        != reference_forecast(
            packets, scope, clock, replace(mission, capacity_assumption_id="capacity/2"), policy
        ).forecast_id
    )
    assert (
        r.forecast_id
        != reference_forecast(
            packets, scope, clock, mission, replace(policy, policy_id="test-window/2")
        ).forecast_id
    )
    sample(packets[-1], "battery.pack.soc")["value"] = 0.1
    assert r.values.projected_final_soc == pytest.approx(0.1)


def test_unrelated_sensor_and_hidden_annotations_are_not_forecast_inputs(
    packets, scope, clock, mission, policy
):
    before = reference_forecast(packets, scope, clock, mission, policy)
    for p in packets:
        p["annotations"] = {"hidden_truth": object()}
        p["samples"].append({"signal_id": "coolant.extra", "quality": "invalid", "value": object()})
    assert reference_forecast(packets, scope, clock, mission, policy) == before


def test_alternate_sensor_bindings_preserve_values_without_core_changes(
    packets, scope, clock, mission, policy
):
    expected = reference_forecast(packets, scope, clock, mission, policy)
    bindings = SignalBindings("energy", "odometer", "charge", "speed")
    mapping = dict(zip(asdict(SignalBindings()).values(), asdict(bindings).values(), strict=True))
    for p in packets:
        for s in p["samples"]:
            s["signal_id"] = mapping[s["signal_id"]]
    actual = reference_forecast(packets, scope, clock, mission, policy, bindings)
    assert actual.values == expected.values
    assert actual.forecast_id != expected.forecast_id


@pytest.mark.parametrize("field", ["asset_id", "run_id", "source_id", "session_id", "source_epoch"])
def test_mixed_identity_never_leaks_partial_forecast(packets, scope, clock, mission, policy, field):
    packets[-1][field] = 2 if field == "source_epoch" else "other"
    check_unavailable(
        reference_forecast(packets, scope, clock, mission, policy), "observation_scope_mismatch"
    )


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1, 10**400])
def test_bad_required_values_produce_no_prediction(packets, scope, clock, mission, policy, value):
    sample(packets[-1], "battery.pack.energy")["value"] = value
    r = reference_forecast(packets, scope, clock, mission, policy)
    assert r.status == "unavailable" and r.values is None


@pytest.mark.parametrize("quality", ["missing", "invalid", "unknown", None])
def test_non_good_required_quality_is_not_filled_with_zero(
    packets, scope, clock, mission, policy, quality
):
    sample(packets[-1], "battery.pack.energy")["quality"] = quality
    check_unavailable(
        reference_forecast(packets, scope, clock, mission, policy), "required_signal_not_good"
    )


@pytest.mark.parametrize(
    "kind,code",
    [
        ("missing", "required_signal_missing"),
        ("duplicate", "duplicate_required_signal"),
        ("unit", "required_signal_unit_mismatch"),
        ("provenance", "observation_provenance_not_supported"),
        ("format", "unsupported_observation_format"),
    ],
)
def test_adapter_and_provenance_fail_closed(packets, scope, clock, mission, policy, kind, code):
    p = packets[-1]
    if kind == "missing":
        p["samples"].pop()
    elif kind == "duplicate":
        p["samples"].append(dict(p["samples"][0]))
    elif kind == "unit":
        p["samples"][0]["unit"] = "Wh"
    elif kind == "provenance":
        p["samples"][0]["provenance"] = "unreviewed"
    else:
        p["schema_version"] = "future"
    check_unavailable(reference_forecast(packets, scope, clock, mission, policy), code)


@pytest.mark.parametrize(
    "kind,code",
    [
        ("event", "duplicate_observation"),
        ("sequence", "observation_order_invalid"),
        ("time", "observation_order_invalid"),
        ("odometer", "odometer_regression"),
        ("gap", "observation_gap"),
    ],
)
def test_bad_window_is_not_sorted_filtered_or_blended(
    packets, scope, clock, mission, policy, kind, code
):
    if kind == "event":
        packets[-1]["event_id"] = packets[0]["event_id"]
    elif kind == "sequence":
        packets[-1]["stream_seq"] = 0
    elif kind == "time":
        packets[-1]["sim_time_s"] = packets[1]["sim_time_s"]
    elif kind == "odometer":
        sample(packets[-1], "motion.distance")["value"] = 0
    else:
        policy = replace(policy, max_gap_s=40)
    check_unavailable(reference_forecast(packets, scope, clock, mission, policy), code)


def test_age_boundary_future_samples_and_clock_state(packets, scope, clock, mission, policy):
    assert (
        reference_forecast(
            packets, scope, replace(clock, as_of_sim_time_s=105), mission, policy
        ).status
        == "advisory"
    )
    check_unavailable(
        reference_forecast(
            packets, scope, replace(clock, as_of_sim_time_s=105.001), mission, policy
        ),
        "stale_observation",
    )
    check_unavailable(
        reference_forecast(packets, scope, replace(clock, as_of_sim_time_s=99), mission, policy),
        "future_observation",
    )
    for state in ["paused", "finished", "disconnected", "unknown"]:
        live = ForecastClock(100, "live_snapshot", state)
        check_unavailable(
            reference_forecast(packets, scope, live, mission, policy), "source_clock_not_ready"
        )
    assert (
        reference_forecast(
            packets, scope, ForecastClock(100, "live_snapshot", "running"), mission, policy
        ).status
        == "advisory"
    )


@pytest.mark.parametrize("state", ["unknown", "present"])
def test_external_charging_must_be_explicitly_excluded(
    packets, scope, clock, mission, policy, state
):
    check_unavailable(
        reference_forecast(
            packets, scope, clock, replace(mission, external_charging_state=state), policy
        ),
        "external_charging_not_excluded",
    )


def test_zero_motion_short_window_and_net_recovery_are_unavailable(
    packets, scope, clock, mission, policy
):
    check_unavailable(
        reference_forecast(packets[:1], scope, clock, mission, policy), "stale_observation"
    )
    check_unavailable(
        reference_forecast(packets, scope, clock, mission, replace(policy, min_window_s=101)),
        "insufficient_history_duration",
    )
    for p in packets:
        sample(p, "motion.distance")["value"] = 0
    check_unavailable(
        reference_forecast(packets, scope, clock, mission, policy), "insufficient_history_distance"
    )


def test_zero_or_negative_recent_consumption_is_not_infinite_range(
    packets, scope, clock, mission, policy
):
    for energy in [3, 4]:
        sample(packets[-1], "battery.pack.energy")["value"] = energy * 3_600_000
        check_unavailable(
            reference_forecast(packets, scope, clock, mission, policy),
            "nonpositive_recent_net_consumption",
        )


def test_count_mode_and_history_span_limits(packets, scope, clock, mission, policy):
    check_unavailable(
        reference_forecast((), scope, clock, mission, policy), "observation_count_out_of_bounds"
    )
    check_unavailable(
        reference_forecast(packets, scope, clock, mission, replace(policy, max_points=2)),
        "observation_count_out_of_bounds",
    )
    check_unavailable(
        reference_forecast(packets, scope, clock, mission, replace(policy, max_window_s=99)),
        "history_window_too_long",
    )
    check_unavailable(
        reference_forecast(packets, replace(scope, mode="replay"), clock, mission, policy),
        "mode_or_clock_domain_not_supported",
    )


@pytest.fixture
def parameters():
    return EnergyParameters(10, 1000, 0, 2, 0, 0, 1, 1, 1, 100_000, 100_000, 10)


def test_supplied_plan_uses_current_charge_without_infinite_range_claim(
    packets, scope, clock, mission, policy, parameters
):
    plan = (DrivingInterval(100, 10, 10, 0, 36_000),)
    r = planned_forecast(packets, scope, clock, mission, policy, parameters, plan)
    assert r.status == "advisory" and r.numerical_model_version.endswith("1.1.0")
    assert r.values.projected_final_soc == pytest.approx(0.1)
    assert r.values.minimum_projected_energy_kwh == pytest.approx(1)
    assert r.values.reserve_feasible
    assert r.values.conditional_distance_to_reserve_m is None
    assert r.values.conditional_time_to_reserve_s is None


def test_reserve_breach_cannot_be_hidden_by_later_downhill_recovery(
    packets, scope, clock, mission, policy, parameters
):
    sample(packets[-1], "battery.pack.soc")["value"] = 0.1
    plan = (DrivingInterval(60, 10, 10, 0, 36_000), DrivingInterval(60, 10, 10, -0.1, 0))
    r = planned_forecast(
        packets,
        scope,
        clock,
        replace(mission, remaining_distance_m=1200),
        policy,
        replace(parameters, loaded_mass_kg=10_000),
        plan,
    )
    assert r.values.projected_final_energy_kwh > 0.5
    assert r.values.minimum_projected_energy_kwh == pytest.approx(0.4)
    assert r.values.energy_plan_feasible
    assert not r.values.reserve_feasible


def test_numerical_step_is_part_of_forecast_identity(
    packets, scope, clock, mission, policy, parameters
):
    plan = (DrivingInterval(100, 10, 10, 0, 36_000),)
    coarse = planned_forecast(packets, scope, clock, mission, policy, parameters, plan, step_s=0.2)
    fine = planned_forecast(packets, scope, clock, mission, policy, parameters, plan, step_s=0.1)
    assert coarse.status == fine.status == "advisory"
    assert coarse.forecast_id != fine.forecast_id
    assert coarse.integration_step_s == 0.2 and fine.integration_step_s == 0.1
    assert fine == planned_forecast(
        packets, scope, clock, mission, policy, parameters, plan, step_s=0.1
    )


@pytest.mark.parametrize(
    "kind,code",
    [
        ("capacity", "declared_capacity_mismatch"),
        ("speed", "plan_initial_speed_mismatch"),
        ("distance", "plan_distance_mismatch"),
        ("budget", "motion_plan_size_invalid"),
        ("discontinuous", "invalid_motion_calculation"),
    ],
)
def test_motion_plan_inconsistency_returns_no_numeric_result(
    packets, scope, clock, mission, policy, parameters, kind, code
):
    plan = (DrivingInterval(100, 10, 10, 0, 36_000),)
    if kind == "capacity":
        parameters = replace(parameters, declared_capacity_kwh=20)
    elif kind == "speed":
        plan = (DrivingInterval(100, 0, 10, 0, 0),)
    elif kind == "distance":
        mission = replace(mission, remaining_distance_m=2000)
    elif kind == "budget":
        policy = replace(policy, max_integration_steps=1)
        plan = plan * 2
    else:
        plan = (DrivingInterval(10, 10, 0, 0, 0), DrivingInterval(100, 10, 10, 0, 0))
    check_unavailable(
        planned_forecast(packets, scope, clock, mission, policy, parameters, plan), code
    )


def test_bad_policy_and_request_configuration_are_rejected(policy, mission):
    for kwargs in [
        {"reserve_soc": 1.1},
        {"reserve_soc": True},
        {"declared_capacity_kwh": 0},
        {"remaining_distance_m": -1},
    ]:
        with pytest.raises(ValueError):
            replace(mission, **kwargs)
    for kwargs in [
        {"max_gap_s": 0},
        {"max_points": True},
        {"max_window_s": 1},
        {"accepted_provenance": ()},
    ]:
        with pytest.raises(ValueError):
            replace(policy, **kwargs)
    with pytest.raises(ValueError):
        SignalBindings("same", "same", "soc", "speed")


def test_adapter_does_not_claim_full_schema_or_original_wire_hash(packets):
    p = observation_point(packets[0])
    assert len(p.normalized_sha256) == 64
    packets[0]["samples"][0]["quality"] = "invalid"
    with pytest.raises(InputUnavailable):
        observation_point(packets[0])


@pytest.mark.parametrize("version", SUPPORTED_ENVELOPE_VERSIONS)
def test_reviewed_operational_envelope_versions_share_selected_quantities(
    packets, scope, clock, mission, policy, version
):
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    schemas = [
        json.loads((root / f"contracts/telemetry/{v}.schema.json").read_bytes())
        for v in SUPPORTED_ENVELOPE_VERSIONS
    ]
    # Refuse future drift in the reviewed common format, not just the version string.
    for schema in schemas:
        schema.pop("$id")
        schema["properties"].pop("schema_version")
        schema["properties"].pop("catalog_version")
    assert all(schema == schemas[0] for schema in schemas)
    expected = reference_forecast(packets, scope, clock, mission, policy)
    for packet in packets:
        packet["schema_version"] = version
    actual = reference_forecast(packets, scope, clock, mission, policy)
    assert actual == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_epoch", True),
        ("run_id", None),
        ("event_id", ""),
        ("stream_seq", "2"),
        ("sim_time_s", float("nan")),
    ],
)
def test_malformed_identity_or_time_returns_unavailable(
    packets, scope, clock, mission, policy, field, value
):
    packets[-1][field] = value
    r = reference_forecast(packets, scope, clock, mission, policy)
    assert r.status == "unavailable" and r.values is None
    assert r.observation_references == ()
