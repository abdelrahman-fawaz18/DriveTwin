"""Independent analytical cases for the advisory energy kernel, not bus calibration."""

import math
from dataclasses import FrozenInstanceError, replace

import pytest

from evdt.energy.model import DrivingInterval, EnergyParameters, estimate_energy


@pytest.fixture
def parameters():
    return EnergyParameters(1, 1000, 0, 2, 0, 0, 1, 1, 1, 100_000, 100_000, 10)


def interval(v0=0, v1=0, duration=10, grade=0, auxiliary=0):
    return DrivingInterval(duration, v0, v1, grade, auxiliary)


def test_no_demand_or_empty_plan_preserves_charge(parameters):
    for plan in [(), (interval(duration=100),)]:
        result = estimate_energy(parameters, plan, 0.5)
        assert result.final_energy_kwh == 0.5
        assert result.requested_draw_j == result.potential_regeneration_j == 0
        assert result.energy_plan_feasible


def test_stopped_auxiliary_load_is_counted_once(parameters):
    result = estimate_energy(parameters, (interval(duration=3600, auxiliary=500),), 1)
    assert result.requested_draw_j == 1_800_000
    assert result.final_energy_kwh == pytest.approx(0.5)
    assert result.planned_distance_m == 0


def test_acceleration_work_matches_kinetic_energy_and_efficiency(parameters):
    p = replace(parameters, propulsion_efficiency=0.5, equivalent_rotating_mass_kg=200)
    result = estimate_energy(p, (interval(0, 10),), 1)
    assert result.requested_draw_j == pytest.approx(0.5 * 1200 * 10**2 / 0.5)
    assert result.planned_distance_m == pytest.approx(50)


def test_rolling_resistance_matches_force_times_distance(parameters):
    p = replace(parameters, rolling_resistance_coefficient=0.01)
    result = estimate_energy(p, (interval(10, 10, duration=100),), 1)
    assert result.requested_draw_j == pytest.approx(1000 * 10 * 0.01 * 1000)


def test_air_drag_matches_constant_power(parameters):
    p = replace(parameters, air_drag_coefficient=1)
    result = estimate_energy(p, (interval(10, 10, duration=100),), 1)
    assert result.requested_draw_j == pytest.approx(0.5 * 1 * 1 * 2 * 10**3 * 100)


def test_hill_cycle_records_conversion_losses(parameters):
    p = replace(parameters, propulsion_efficiency=0.8, regeneration_efficiency=0.5)
    result = estimate_energy(p, (interval(10, 10, grade=0.1), interval(10, 10, grade=-0.1)), 1)
    gravitational_work = 1000 * 10 * (0.1 / math.sqrt(1.01)) * 100
    assert result.requested_draw_j == pytest.approx(gravitational_work / 0.8)
    assert result.potential_regeneration_j == pytest.approx(gravitational_work * 0.5)
    assert result.final_energy_kwh < 1


def test_regeneration_respects_efficiency_and_electrical_power_limit(parameters):
    p = replace(parameters, max_electrical_regeneration_power_w=1000)
    result = estimate_energy(p, (interval(10, 0),), 0.5, step_s=1)
    assert result.potential_regeneration_j == pytest.approx(50_000)
    assert result.regeneration_power_rejected_j == pytest.approx(40_500)
    assert result.final_energy_kwh == pytest.approx(0.5 + 9500 / 3_600_000)
    inefficient = estimate_energy(
        replace(parameters, regeneration_efficiency=0.5), (interval(10, 0),), 0.5
    )
    assert inefficient.potential_regeneration_j == pytest.approx(25_000)


def test_full_battery_rejects_excess_recovery(parameters):
    result = estimate_energy(parameters, (interval(10, 0),), 1)
    assert result.final_soc == 1
    assert result.regeneration_capacity_rejected_j == pytest.approx(50_000)
    assert result.balance_residual_j == pytest.approx(0, abs=1e-6)


def test_regeneration_can_supply_auxiliaries_before_reaching_a_full_pack(parameters):
    result = estimate_energy(parameters, (interval(10, 0, auxiliary=100),), 1, step_s=1)
    assert result.final_soc == 1
    assert result.regeneration_capacity_rejected_j == pytest.approx(49_000)
    assert result.unserved_demand_j == 0


def test_empty_battery_exposes_unmet_demand(parameters):
    result = estimate_energy(parameters, (interval(auxiliary=1000),), 0)
    assert result.final_soc == 0
    assert result.unserved_demand_j == pytest.approx(10_000)
    assert result.first_shortfall_step_start_s == 0
    assert not result.energy_plan_feasible


def test_power_limit_is_infeasible_even_with_energy_available(parameters):
    p = replace(parameters, max_battery_discharge_power_w=1000)
    result = estimate_energy(p, (interval(auxiliary=2000),), 1)
    assert result.unserved_demand_j == pytest.approx(10_000)
    assert result.final_energy_kwh == pytest.approx(1 - 10_000 / 3_600_000)
    assert not result.energy_plan_feasible


def test_later_recovery_does_not_hide_an_earlier_shortfall(parameters):
    result = estimate_energy(
        parameters,
        (interval(10, 10, duration=2, auxiliary=1000), interval(10, 0)),
        1000 / 3_600_000,
        step_s=1,
    )
    assert result.first_shortfall_step_start_s == 1
    assert result.final_energy_kwh > 0
    assert result.unserved_demand_j == pytest.approx(1000)
    assert not result.energy_plan_feasible


def test_zero_regeneration_efficiency_never_charges(parameters):
    result = estimate_energy(
        replace(parameters, regeneration_efficiency=0), (interval(10, 0),), 0.5
    )
    assert result.final_energy_kwh == 0.5
    assert result.potential_regeneration_j == 0


def test_midpoint_refinement_converges_to_independent_cubic_integral(parameters):
    p = replace(parameters, loaded_mass_kg=1, air_drag_coefficient=1)
    # Kinetic work is 50 J; integral of v^3 over 0..10 seconds is 2500.
    exact_j = 2550
    errors = [
        abs(estimate_energy(p, (interval(0, 10),), 1, step_s=dt).requested_draw_j - exact_j)
        for dt in (0.4, 0.2, 0.1)
    ]
    assert errors[2] < errors[1] < errors[0]
    assert errors[0] / errors[1] == pytest.approx(4, rel=0.01)
    assert errors[1] / errors[2] == pytest.approx(4, rel=0.01)


def test_repeatability_and_input_immutability(parameters):
    plan = (interval(0, 10), interval(10, 0))
    assert estimate_energy(parameters, plan, 0.5) == estimate_energy(parameters, plan, 0.5)
    with pytest.raises(FrozenInstanceError):
        parameters.loaded_mass_kg = 2000


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1])
def test_invalid_physical_numbers_are_rejected(parameters, value):
    with pytest.raises(ValueError):
        replace(parameters, loaded_mass_kg=value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("declared_capacity_kwh", 0),
        ("propulsion_efficiency", 0),
        ("propulsion_efficiency", 1.01),
        ("regeneration_efficiency", 1.01),
        ("max_electrical_regeneration_power_w", -1),
        ("max_battery_discharge_power_w", 0),
        ("declared_capacity_kwh", 1e308),
    ],
)
def test_invalid_parameter_combinations_are_rejected(parameters, field, value):
    with pytest.raises(ValueError):
        replace(parameters, **{field: value})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration": 0},
        {"duration": float("nan")},
        {"v0": -1},
        {"v1": True},
        {"grade": float("inf")},
        {"auxiliary": -1},
    ],
)
def test_invalid_planned_intervals_are_rejected(kwargs):
    with pytest.raises(ValueError):
        interval(**kwargs)


@pytest.mark.parametrize("soc", [True, -0.1, 1.1, float("nan")])
def test_invalid_initial_charge_is_rejected(parameters, soc):
    with pytest.raises(ValueError):
        estimate_energy(parameters, (), soc)


@pytest.mark.parametrize("step", [True, 0, -1, float("inf")])
def test_invalid_step_is_rejected(parameters, step):
    with pytest.raises(ValueError):
        estimate_energy(parameters, (), 0.5, step_s=step)


def test_excessive_work_is_rejected_before_integration(parameters):
    with pytest.raises(ValueError, match="budget"):
        estimate_energy(parameters, (interval(duration=1000),), 0.5, max_steps=10)
    with pytest.raises(ValueError):
        estimate_energy(parameters, (interval(duration=1e308),), 0.5, step_s=1e-308)
    with pytest.raises(ValueError):
        estimate_energy(parameters, (), 0.5, max_steps=True)


def test_plan_is_immutable_and_outputs_cannot_silently_overflow(parameters):
    with pytest.raises(ValueError):
        estimate_energy(parameters, [interval()], 0.5)
    with pytest.raises(ValueError):
        estimate_energy(replace(parameters, air_drag_coefficient=1), (interval(1e200, 1e200),), 0.5)


def test_speed_jumps_cannot_create_free_recoverable_energy(parameters):
    with pytest.raises(ValueError, match="continuous"):
        estimate_energy(parameters, (interval(), interval(10, 0)), 0)


def test_large_totals_fail_closed_instead_of_emitting_infinity(parameters):
    p = replace(parameters, max_battery_discharge_power_w=1e308)
    with pytest.raises(ValueError, match="cannot be represented"):
        estimate_energy(p, (interval(duration=2, auxiliary=1e308),), 1, step_s=1)
