"""Bounded-memory battery accounting along an explicit motion plan."""

import math
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import Conditions, Curve, LiveState, VehicleProfile
from .environment import EquipmentDemand, equipment_demand
from .geometry import RemainingRoute, RoadPiece, RouteUnavailable
from .motion import MotionPhase, MotionPlan
from .policy import ForecastPolicy
from .scenarios import RouteScenario

MotionSample = Callable[[float, int, float, RoadPiece | None, MotionPhase, float, float], None]


class StableSum:
    """Neumaier accumulation keeps small energy terms next to large totals."""

    def __init__(self) -> None:
        self._sum = 0.0
        self._correction = 0.0

    def add(self, value: float) -> None:
        total = self._sum + value
        if abs(self._sum) >= abs(value):
            self._correction += (self._sum - total) + value
        else:
            self._correction += (value - total) + self._sum
        self._sum = total

    @property
    def value(self) -> float:
        return self._sum + self._correction


@dataclass(frozen=True)
class EnergyLedger:
    initial_energy_j: float
    final_energy_j: float
    minimum_energy_j: float
    capacity_j: float
    battery_draw_j: float
    battery_recovery_j: float
    net_battery_j: float
    acceleration_work_j: float
    grade_work_j: float
    rolling_work_j: float
    air_work_j: float
    propulsion_required_j: float
    auxiliary_required_j: float
    regeneration_potential_j: float
    regeneration_used_j: float
    rejected_regeneration_j: float
    unserved_energy_j: float
    drive_conversion_loss_j: float
    braking_to_heat_j: float
    reserve_breached: bool
    motion_feasible: bool
    integration_steps: int
    energy_balance_error_j: float
    assumptions: tuple[str, ...]


def condition_at(conditions: tuple[Conditions, ...], working_s: float) -> Conditions:
    for condition in conditions:
        if condition.from_working_s <= working_s < condition.until_working_s:
            return condition
    raise RouteUnavailable("conditions_do_not_cover_plan")


def _curve(curve: Curve | None, x: float | None, default: float) -> float:
    if curve is None:
        return default
    if x is None:
        raise RouteUnavailable("required_curve_input_missing")
    try:
        return curve.at(x)
    except ValueError as exc:
        raise RouteUnavailable("vehicle_curve_input_outside_domain") from exc


def integrate(
    route: RemainingRoute,
    plan: MotionPlan,
    state: LiveState,
    vehicle: VehicleProfile,
    conditions: tuple[Conditions, ...],
    policy: ForecastPolicy,
    *,
    scenario: RouteScenario | None = None,
    sample: MotionSample | None = None,
) -> EnergyLedger:
    """Integrate the attempted plan; any unserved demand makes it infeasible.

    Temperature-dependent usable fraction raises the inaccessible-energy floor
    relative to nominal capacity. It never deletes stored energy or changes the
    source's charge scale. Wind components point in the direction air travels.
    """
    if state.charging_state != "not_charging":
        raise RouteUnavailable("charging_state_not_supported")
    if math.ulp(state.working_s + policy.max_plan_working_s) > 1e-6:
        raise RouteUnavailable("working_clock_precision_outside_domain")
    capacity = vehicle.capacity_kwh * 3_600_000
    initial = state.soc * capacity
    stored, minimum = initial, initial
    names = (
        "draw",
        "recovery",
        "acceleration",
        "grade",
        "rolling",
        "air",
        "propulsion",
        "auxiliary",
        "regen_potential",
        "regen_used",
        "regen_rejected",
        "unserved",
        "drive_loss",
        "braking_heat",
    )
    totals = {name: StableSum() for name in names}
    assumptions = set(plan.assumptions)
    assumptions.update(
        (
            "constant_effective_drag_area",
            "piecewise_constant_grade",
            "battery_temperature_held_at_current_value",
        )
    )
    reserve_breached = state.soc < vehicle.reserve_soc
    temperature = state.battery_temperature_k
    usable_fraction = _curve(vehicle.usable_fraction_by_temperature, temperature, 1)
    inaccessible = capacity * (1 - usable_fraction)
    if vehicle.usable_fraction_by_temperature is not None:
        assumptions.add("temperature_capacity_as_inaccessible_energy_floor")
    discharge_limit = min(
        vehicle.max_discharge_w,
        _curve(vehicle.discharge_by_temperature, temperature, vehicle.max_discharge_w),
    )
    temperature_regen_limit = _curve(
        vehicle.regeneration_by_temperature, temperature, vehicle.max_regeneration_w
    )
    steps = 0
    previous_mass: float | None = None
    localized = (
        {road.occurrence_index: road.intervals for road in scenario.roads} if scenario else {}
    )
    effects = {effect.reference: effect for effect in scenario.stops} if scenario else {}
    mass_override = None
    occupants = scenario.initial_occupants if scenario else 0
    equipment_cache: dict[tuple[float, int, float], EquipmentDemand] = {}
    for reference, completed_effect in effects.items():
        if reference in state.completed_stops:
            if completed_effect.loaded_mass_on_departure_kg is not None:
                mass_override = completed_effect.loaded_mass_on_departure_kg
            if completed_effect.occupants_on_departure is not None:
                occupants = completed_effect.occupants_on_departure
    last_piece_index = None
    phase_start_distance_m = 0.0
    for phase in plan.phases:
        if sample is not None and phase.piece_index != last_piece_index:
            last_piece_index = phase.piece_index
            phase_start_distance_m = 0.0
        occurrence = (
            route.pieces[phase.piece_index].occurrence_index
            if phase.piece_index is not None
            else (phase.stop.occurrence_index if phase.stop else state.occurrence_index)
        )
        phase_conditions = localized.get(occurrence, conditions)
        effect = effects.get(phase.stop) if phase.stop else None
        elapsed = 0.0
        while elapsed < phase.duration_working_s - 1e-10:
            if steps >= policy.max_integration_steps:
                raise RouteUnavailable("integration_step_budget_exceeded")
            working = phase.start_working_s + elapsed
            condition = condition_at(phase_conditions, working)
            step = min(
                policy.integration_step_working_s,
                phase.duration_working_s - elapsed,
                condition.until_working_s - working,
            )
            near_boundary = state.working_s + policy.near_horizon_working_s
            if working < near_boundary:
                step = min(step, near_boundary - working)
            if step < 1e-10:
                # Advance only roundoff at an exact input boundary. Do not spend
                # another integration step on a sub-nanosecond residual.
                target = min(
                    condition.until_working_s,
                    near_boundary if working < near_boundary else math.inf,
                )
                advanced = target - phase.start_working_s
                if advanced <= elapsed:
                    raise RouteUnavailable("condition_interval_below_numerical_resolution")
                elapsed = advanced
                continue
            near = working + step / 2 < near_boundary
            if near:
                auxiliary = state.auxiliary_power_w
                assumptions.add("near_horizon_observed_aggregate_auxiliary")
            elif scenario is not None and scenario.equipment is not None:
                if condition.ambient_temperature_k is None:
                    raise RouteUnavailable("equipment_model_ambient_temperature_missing")
                door = effect.door_open_fraction if phase.kind == "dwell" and effect else 0
                key = (condition.ambient_temperature_k, occupants, door)
                if key not in equipment_cache:
                    equipment_cache[key] = equipment_demand(
                        scenario.equipment,
                        ambient_temperature_k=condition.ambient_temperature_k,
                        occupants=occupants,
                        door_open_fraction=door,
                        absorbed_solar_w=scenario.absorbed_solar_w,
                    )
                demand = equipment_cache[key]
                auxiliary = demand.total_electrical_w
                assumptions.update(demand.assumptions)
                assumptions.add("far_horizon_equipment_profile_aggregate")
                if demand.unserved_heating_thermal_w or demand.unserved_cooling_thermal_w:
                    assumptions.add("equipment_thermal_capacity_exceeded")
            elif condition.auxiliary_power_w is not None:
                auxiliary = condition.auxiliary_power_w
                assumptions.add("far_horizon_declared_aggregate_auxiliary")
            else:
                raise RouteUnavailable("forward_auxiliary_assumption_missing")
            auxiliary += condition.extra_auxiliary_power_w
            mass = (
                mass_override
                if mass_override is not None
                else condition.loaded_mass_kg
                if condition.loaded_mass_kg is not None
                else vehicle.loaded_mass_kg
            )
            if mass > vehicle.max_loaded_mass_kg:
                raise RouteUnavailable("loaded_mass_exceeds_vehicle_limit")
            boundary_speed = phase.start_speed_m_s + phase.acceleration_m_s2 * elapsed
            if previous_mass is not None and mass != previous_mass and boundary_speed > 1e-6:
                raise RouteUnavailable("load_change_while_moving")
            previous_mass = mass
            density = vehicle.air_density_kg_m3
            assumptions.add("wind_basis_" + condition.wind_basis)
            if condition.ambient_temperature_k is not None and condition.pressure_pa is not None:
                density = condition.pressure_pa / (287.05 * condition.ambient_temperature_k)
                assumptions.add("dry_air_density_from_temperature_pressure")
            else:
                assumptions.add("declared_air_density")
            speed = max(0.0, phase.start_speed_m_s + phase.acceleration_m_s2 * (elapsed + step / 2))
            distance = (
                speed if phase.position_rate_m_s is None else phase.position_rate_m_s
            ) * step
            # Kinetic energy follows the change in speed. SUMO's discrete
            # positional update can cover a different distance in the same step.
            acceleration_j = (
                (mass + vehicle.rotating_mass_kg) * phase.acceleration_m_s2 * (speed * step)
            )
            grade_j, rolling_j, air_j = 0.0, 0.0, 0.0
            if phase.kind != "dwell":
                if phase.piece_index is None:
                    raise ValueError("Moving phase lacks its road segment")
                piece = route.pieces[phase.piece_index]
                theta = math.atan(piece.grade_ratio)
                grade_j = mass * vehicle.gravity_m_s2 * math.sin(theta) * distance
                rolling_j = (
                    vehicle.rolling_coefficient
                    * condition.rolling_multiplier
                    * mass
                    * vehicle.gravity_m_s2
                    * math.cos(theta)
                    * distance
                )
                east, north = math.sin(piece.heading_rad), math.cos(piece.heading_rad)
                relative_east = speed * east - condition.wind_east_m_s
                relative_north = speed * north - condition.wind_north_m_s
                relative_speed = math.hypot(relative_east, relative_north)
                longitudinal = relative_east * east + relative_north * north
                air_j = (
                    0.5
                    * density
                    * vehicle.drag_coefficient
                    * vehicle.frontal_area_m2
                    * relative_speed
                    * longitudinal
                    * distance
                )
            wheel_j = math.fsum((acceleration_j, grade_j, rolling_j, air_j))
            propulsion, regen = 0.0, 0.0
            if wheel_j >= 0:
                efficiency = _curve(
                    vehicle.propulsion_by_power, wheel_j / step, vehicle.propulsion_efficiency
                )
                propulsion = wheel_j / efficiency
            else:
                regen = -wheel_j * vehicle.regeneration_efficiency
            auxiliary_j = auxiliary * step
            raw_demand = propulsion + auxiliary_j - regen
            draw, recovery, unserved, rejected = 0.0, 0.0, 0.0, 0.0
            if raw_demand >= 0:
                draw = min(raw_demand, discharge_limit * step, max(0.0, stored - inaccessible))
                unserved = raw_demand - draw
            else:
                regen_limit = min(
                    vehicle.max_regeneration_w,
                    temperature_regen_limit,
                    _curve(
                        vehicle.regeneration_by_soc, stored / capacity, vehicle.max_regeneration_w
                    ),
                )
                recovery = min(-raw_demand, regen_limit * step, max(0.0, capacity - stored))
                rejected = -raw_demand - recovery
            used = regen - rejected
            for name, value in (
                ("draw", draw),
                ("recovery", recovery),
                ("acceleration", acceleration_j),
                ("grade", grade_j),
                ("rolling", rolling_j),
                ("air", air_j),
                ("propulsion", propulsion),
                ("auxiliary", auxiliary_j),
                ("regen_potential", regen),
                ("regen_used", used),
                ("regen_rejected", rejected),
                ("unserved", unserved),
                ("drive_loss", max(0.0, propulsion - max(0.0, wheel_j))),
                ("braking_heat", max(0.0, -wheel_j) - used),
            ):
                totals[name].add(value)
            stored = initial - totals["draw"].value + totals["recovery"].value
            tolerance = 1e-6 + 1e-10 * capacity
            if stored < -tolerance or stored > capacity + tolerance:
                raise ArithmeticError("Battery accounting left its physical bounds")
            minimum = min(minimum, stored)
            reserve_breached |= stored < vehicle.reserve_soc * capacity
            elapsed += step
            steps += 1
            if sample is not None:
                sample_piece = (
                    route.pieces[phase.piece_index] if phase.piece_index is not None else None
                )
                sample(
                    phase.start_working_s + elapsed,
                    occurrence,
                    stored / capacity,
                    sample_piece,
                    phase,
                    elapsed,
                    phase_start_distance_m,
                )
        if sample is not None:
            phase_start_distance_m += phase.distance_m
        if phase.kind == "dwell" and effect is not None:
            if effect.loaded_mass_on_departure_kg is not None:
                mass_override = effect.loaded_mass_on_departure_kg
            if effect.occupants_on_departure is not None:
                occupants = effect.occupants_on_departure
    values = {name: total.value for name, total in totals.items()}
    net = values["draw"] - values["recovery"]
    errors = (
        initial - net - stored,
        values["propulsion"]
        + values["auxiliary"]
        - values["draw"]
        - values["regen_used"]
        + values["recovery"]
        - values["unserved"],
        values["regen_potential"] - values["regen_used"] - values["regen_rejected"],
    )
    tolerance = 1e-6 + 1e-10 * max(initial, values["draw"], values["recovery"])
    error = max(abs(value) for value in errors)
    if error > tolerance:
        raise ArithmeticError("Energy ledger does not conserve energy")
    return EnergyLedger(
        initial,
        stored,
        minimum,
        capacity,
        values["draw"],
        values["recovery"],
        net,
        values["acceleration"],
        values["grade"],
        values["rolling"],
        values["air"],
        values["propulsion"],
        values["auxiliary"],
        values["regen_potential"],
        values["regen_used"],
        values["regen_rejected"],
        values["unserved"],
        values["drive_loss"],
        values["braking_heat"],
        reserve_breached,
        values["unserved"] <= tolerance,
        steps,
        error,
        tuple(sorted(assumptions)),
    )
