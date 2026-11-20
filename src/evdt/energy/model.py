"""Bounded longitudinal energy estimates from an explicitly assumed motion plan.

This numerical kernel does not establish vehicle accuracy or trajectory feasibility.
Positive battery demand discharges the estimated pack; regeneration charges it.
SUMO's actual battery state is neither read nor written here.
"""

import math
from dataclasses import dataclass

MODEL_VERSION = "longitudinal-energy-estimate/1.1.0"
JOULES_PER_KWH = 3_600_000.0


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _total(values: list[float], name: str) -> float:
    try:
        return _finite(math.fsum(values), name)
    except OverflowError as exc:
        raise ValueError(f"{name} cannot be represented") from exc


@dataclass(frozen=True)
class EnergyParameters:
    """Declared estimator assumptions, never hidden actual plant parameters."""

    declared_capacity_kwh: float
    loaded_mass_kg: float
    equivalent_rotating_mass_kg: float
    frontal_area_m2: float
    air_drag_coefficient: float
    rolling_resistance_coefficient: float
    air_density_kg_m3: float
    propulsion_efficiency: float
    regeneration_efficiency: float
    max_electrical_regeneration_power_w: float
    max_battery_discharge_power_w: float
    gravity_m_s2: float

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            _finite(value, name)
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
        for name in (
            "declared_capacity_kwh",
            "loaded_mass_kg",
            "frontal_area_m2",
            "air_density_kg_m3",
            "propulsion_efficiency",
            "max_battery_discharge_power_w",
            "gravity_m_s2",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.propulsion_efficiency > 1 or self.regeneration_efficiency > 1:
            raise ValueError("Efficiencies must not exceed one")
        _finite(self.declared_capacity_kwh * JOULES_PER_KWH, "capacity in joules")


@dataclass(frozen=True)
class DrivingInterval:
    """Planned linear speed change, constant grade and constant auxiliary demand.

    Grade is rise/horizontal run, not an angle. Auxiliary power is battery-side
    demand for equipment such as heating; it is added exactly once.
    """

    duration_s: float
    start_speed_m_s: float
    end_speed_m_s: float
    grade_ratio: float
    auxiliary_power_w: float

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            _finite(value, name)
        if self.duration_s <= 0:
            raise ValueError("Interval duration must be positive")
        if min(self.start_speed_m_s, self.end_speed_m_s, self.auxiliary_power_w) < 0:
            raise ValueError("Speeds and auxiliary power must be nonnegative")


@dataclass(frozen=True)
class EnergyEstimate:
    model_version: str
    declared_capacity_kwh: float
    initial_energy_kwh: float
    final_energy_kwh: float
    final_soc: float
    requested_draw_j: float
    potential_regeneration_j: float
    regeneration_power_rejected_j: float
    regeneration_capacity_rejected_j: float
    unserved_demand_j: float
    balance_residual_j: float
    planned_duration_s: float
    planned_distance_m: float
    first_shortfall_step_start_s: float | None
    largest_integration_step_s: float
    integration_steps: int
    energy_plan_feasible: bool
    minimum_energy_kwh: float


def estimate_energy(
    parameters: EnergyParameters,
    plan: tuple[DrivingInterval, ...],
    initial_soc: float,
    *,
    step_s: float = 0.2,
    max_steps: int = 100_000,
) -> EnergyEstimate:
    """Integrate midpoint-held power and retain every rejected/unserved joule.

    The full assumed plan is evaluated even after a shortfall. A later recovery
    never removes the earlier infeasibility. Planned duration/distance are not
    predictions of achievable travel after depletion.
    """
    if not isinstance(parameters, EnergyParameters):
        raise ValueError("An EnergyParameters record is required")
    if not isinstance(plan, tuple):
        raise ValueError("Plan must be an immutable tuple of DrivingInterval records")
    initial_soc = _finite(initial_soc, "initial state of charge")
    step_s = _finite(step_s, "integration step")
    if not 0 <= initial_soc <= 1 or step_s <= 0:
        raise ValueError("State of charge must be in [0,1] and integration step positive")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("Step budget must be a positive integer")
    if len(plan) > max_steps:
        raise ValueError("Plan exceeds the configured integration step budget")
    if not all(isinstance(p, DrivingInterval) for p in plan):
        raise ValueError("Plan must contain DrivingInterval records")
    for previous, current in zip(plan, plan[1:]):
        if previous.end_speed_m_s != current.start_speed_m_s:
            raise ValueError("Planned speed must be continuous across intervals")

    counts = []
    total_steps = 0
    for interval in plan:
        ratio = _finite(interval.duration_s / step_s, "interval step count")
        count = max(1, math.ceil(ratio))
        total_steps += count
        if total_steps > max_steps:
            raise ValueError("Plan exceeds the configured integration step budget")
        counts.append(count)

    capacity_j = parameters.declared_capacity_kwh * JOULES_PER_KWH
    initial_j = capacity_j * initial_soc
    energy_j = initial_j
    minimum_j = initial_j
    draws: list[float] = []
    recoveries: list[float] = []
    power_rejections: list[float] = []
    capacity_rejections: list[float] = []
    shortfalls: list[float] = []
    distances: list[float] = []
    elapsed_s = 0.0
    largest_step_s = 0.0
    first_shortfall: float | None = None

    for interval, count in zip(plan, counts, strict=True):
        dt_s = interval.duration_s / count
        largest_step_s = max(largest_step_s, dt_s)
        acceleration = _finite(
            (interval.end_speed_m_s - interval.start_speed_m_s) / interval.duration_s,
            "planned acceleration",
        )
        angle = math.atan(interval.grade_ratio)
        gravity_force = parameters.loaded_mass_kg * parameters.gravity_m_s2
        grade_force = gravity_force * math.sin(angle)
        rolling_force = gravity_force * parameters.rolling_resistance_coefficient * math.cos(angle)
        inertia_force = (
            parameters.loaded_mass_kg + parameters.equivalent_rotating_mass_kg
        ) * acceleration
        drag_factor = (
            0.5
            * parameters.air_density_kg_m3
            * parameters.air_drag_coefficient
            * parameters.frontal_area_m2
        )
        for index in range(count):
            fraction = (index + 0.5) / count
            speed = (
                interval.start_speed_m_s
                + (interval.end_speed_m_s - interval.start_speed_m_s) * fraction
            )
            wheel_power_w = _finite(
                (inertia_force + rolling_force + grade_force + drag_factor * speed * speed) * speed,
                "wheel power",
            )
            draw_w = max(0.0, wheel_power_w) / parameters.propulsion_efficiency
            potential_w = max(0.0, -wheel_power_w) * parameters.regeneration_efficiency
            recovery_w = min(potential_w, parameters.max_electrical_regeneration_power_w)
            draw_j = _finite((draw_w + interval.auxiliary_power_w) * dt_s, "requested draw")
            potential_j = _finite(potential_w * dt_s, "potential regeneration")
            power_rejected_j = _finite((potential_w - recovery_w) * dt_s, "power rejection")
            net_j = draw_j - recovery_w * dt_s
            capacity_rejected_j = 0.0
            unserved_j = 0.0
            if net_j >= 0:
                supplied_j = min(net_j, parameters.max_battery_discharge_power_w * dt_s, energy_j)
                unserved_j = net_j - supplied_j
                energy_j -= supplied_j
                if unserved_j > 0 and first_shortfall is None:
                    first_shortfall = elapsed_s + index * dt_s
            else:
                stored_j = min(-net_j, capacity_j - energy_j)
                capacity_rejected_j = -net_j - stored_j
                energy_j += stored_j
            draws.append(draw_j)
            minimum_j = min(minimum_j, energy_j)
            recoveries.append(potential_j)
            power_rejections.append(power_rejected_j)
            capacity_rejections.append(capacity_rejected_j)
            shortfalls.append(unserved_j)
        distances.append(
            _finite(
                0.5 * (interval.start_speed_m_s + interval.end_speed_m_s) * interval.duration_s,
                "planned distance",
            )
        )
        elapsed_s = _finite(elapsed_s + interval.duration_s, "planned duration")

    draw = _total(draws, "total requested draw")
    recovery = _total(recoveries, "total potential regeneration")
    power_rejected = _total(power_rejections, "total power rejection")
    capacity_rejected = _total(capacity_rejections, "total capacity rejection")
    unserved = _total(shortfalls, "total unmet demand")
    residual = _total(
        [initial_j, recovery, unserved, -energy_j, -draw, -power_rejected, -capacity_rejected],
        "energy balance residual",
    )
    tolerance_j = 1e-6 + 1e-10 * max(initial_j, draw, recovery)
    if not 0 <= energy_j <= capacity_j or abs(residual) > tolerance_j:
        raise ArithmeticError("Estimated battery bounds or energy ledger failed")
    return EnergyEstimate(
        MODEL_VERSION,
        parameters.declared_capacity_kwh,
        initial_j / JOULES_PER_KWH,
        energy_j / JOULES_PER_KWH,
        energy_j / capacity_j,
        draw,
        recovery,
        power_rejected,
        capacity_rejected,
        unserved,
        residual,
        elapsed_s,
        _total(distances, "total planned distance"),
        first_shortfall,
        largest_step_s,
        total_steps,
        first_shortfall is None,
        minimum_j / JOULES_PER_KWH,
    )
