"""Advisory reference/assumed-plan forecasts with explicit observation validity."""

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

from evdt.energy.model import (
    JOULES_PER_KWH,
    MODEL_VERSION,
    DrivingInterval,
    EnergyParameters,
    estimate_energy,
)
from evdt.energy.observations import (
    EnergyPoint,
    InputUnavailable,
    ObservationScope,
    SignalBindings,
    fingerprint,
    integer,
    number,
    observation_point,
    text,
)

BASELINE_VERSION = "recent-net-distance-baseline/1.0.0"
PLAN_VERSION = "observed-plan-advisory/1.0.0"


@dataclass(frozen=True)
class ForecastClock:
    as_of_sim_time_s: float
    origin: Literal["recorded_analysis", "live_snapshot"]
    source_state: str

    def __post_init__(self) -> None:
        if number(self.as_of_sim_time_s, "as_of_sim_time_s") < 0:
            raise ValueError("Nonnegative forecast time required")
        if self.origin not in ("recorded_analysis", "live_snapshot"):
            raise ValueError("Unknown forecast input origin")
        text(self.source_state, "source_state")


@dataclass(frozen=True)
class ForecastPolicy:
    policy_id: str
    min_window_s: float
    max_window_s: float
    min_distance_m: float
    max_gap_s: float
    max_input_age_s: float
    max_points: int
    plan_distance_tolerance_m: float = 1e-6
    initial_speed_tolerance_m_s: float = 1e-6
    max_integration_steps: int = 100_000
    accepted_provenance: tuple[str, ...] = ("simulated-observation",)

    def __post_init__(self) -> None:
        text(self.policy_id, "policy_id")
        for name in ("min_window_s", "max_window_s", "min_distance_m", "max_gap_s"):
            if number(getattr(self, name), name) <= 0:
                raise ValueError("Positive window/distance/gap policy required")
        for name in ("max_input_age_s", "plan_distance_tolerance_m", "initial_speed_tolerance_m_s"):
            if number(getattr(self, name), name) < 0:
                raise ValueError("Nonnegative age/tolerance policy required")
        if self.max_window_s < self.min_window_s or integer(self.max_points, "max_points") < 2:
            raise ValueError("Invalid window or point budget")
        if integer(self.max_integration_steps, "max_integration_steps") < 1:
            raise ValueError("Positive integration budget required")
        if not isinstance(self.accepted_provenance, tuple) or not self.accepted_provenance:
            raise ValueError("Explicit accepted provenance required")
        for value in self.accepted_provenance:
            text(value, "accepted_provenance")


@dataclass(frozen=True)
class MissionRequest:
    request_id: str
    mission_id: str
    plan_version: str
    remaining_distance_m: float
    declared_capacity_kwh: float
    capacity_assumption_id: str
    reserve_soc: float
    external_charging_state: Literal["absent", "present", "unknown"] = "unknown"

    def __post_init__(self) -> None:
        for name in ("request_id", "mission_id", "plan_version", "capacity_assumption_id"):
            text(getattr(self, name), name)
        if number(self.remaining_distance_m, "remaining_distance_m") < 0:
            raise ValueError("Nonnegative remaining distance required")
        if number(self.declared_capacity_kwh, "declared_capacity_kwh") <= 0:
            raise ValueError("Positive declared capacity required")
        number(self.declared_capacity_kwh * JOULES_PER_KWH, "declared_capacity_j")
        if not 0 <= number(self.reserve_soc, "reserve_soc") <= 1:
            raise ValueError("Reserve must be a fraction in [0,1]")
        if self.external_charging_state not in ("absent", "present", "unknown"):
            raise ValueError("Unknown external charging state")


@dataclass(frozen=True)
class ObservationReference:
    event_id: str
    stream_seq: int
    sim_time_s: float
    normalized_sha256: str


@dataclass(frozen=True)
class ForecastValues:
    estimated_initial_energy_kwh: float
    net_mission_energy_kwh: float
    projected_final_energy_kwh: float
    projected_final_soc: float
    minimum_projected_energy_kwh: float
    unserved_energy_kwh: float
    energy_plan_feasible: bool
    reserve_feasible: bool
    conditional_distance_to_reserve_m: float | None = None
    conditional_time_to_reserve_s: float | None = None
    recent_net_kwh_per_km: float | None = None
    recent_average_power_kw: float | None = None


@dataclass(frozen=True)
class AdvisoryForecast:
    record_version: str
    model_version: str
    numerical_model_version: str | None
    status: Literal["advisory", "unavailable"]
    reason_codes: tuple[str, ...]
    scope: ObservationScope
    request: MissionRequest
    clock: ForecastClock
    policy_id: str
    input_age_s: float | None
    observation_references: tuple[ObservationReference, ...]
    forecast_id: str | None
    values: ForecastValues | None
    integration_step_s: float | None
    model_qualified: bool = False
    uncertainty_method: str = "not_established"


def _prepare(
    packets: tuple[Mapping[str, Any], ...],
    scope: ObservationScope,
    clock: ForecastClock,
    mission: MissionRequest,
    policy: ForecastPolicy,
    bindings: SignalBindings,
) -> tuple[EnergyPoint, ...]:
    if scope.mode != "simulation" or scope.clock_domain != "simulation":
        raise InputUnavailable("mode_or_clock_domain_not_supported")
    expected_state = "frozen" if clock.origin == "recorded_analysis" else "running"
    if clock.source_state != expected_state:
        raise InputUnavailable("source_clock_not_ready")
    if mission.external_charging_state != "absent":
        raise InputUnavailable("external_charging_not_excluded")
    if not isinstance(packets, tuple) or not 0 < len(packets) <= policy.max_points:
        raise InputUnavailable("observation_count_out_of_bounds")
    points = tuple(observation_point(p, bindings) for p in packets)
    seen: set[str] = set()
    previous: EnergyPoint | None = None
    for point in points:
        if point.scope != scope:
            raise InputUnavailable("observation_scope_mismatch")
        if point.event_id in seen:
            raise InputUnavailable("duplicate_observation")
        seen.add(point.event_id)
        if any(p not in policy.accepted_provenance for p in point.provenance):
            raise InputUnavailable("observation_provenance_not_supported")
        if point.sim_time_s > clock.as_of_sim_time_s:
            raise InputUnavailable("future_observation")
        if previous is not None:
            gap = point.sim_time_s - previous.sim_time_s
            if gap <= 0 or point.stream_seq <= previous.stream_seq:
                raise InputUnavailable("observation_order_invalid")
            if gap > policy.max_gap_s:
                raise InputUnavailable("observation_gap")
            if point.distance_m < previous.distance_m:
                raise InputUnavailable("odometer_regression")
        previous = point
    if clock.as_of_sim_time_s - points[-1].sim_time_s > policy.max_input_age_s:
        raise InputUnavailable("stale_observation")
    if points[-1].sim_time_s - points[0].sim_time_s > policy.max_window_s:
        raise InputUnavailable("history_window_too_long")
    return points


def _result(
    version: str,
    scope: ObservationScope,
    clock: ForecastClock,
    mission: MissionRequest,
    policy: ForecastPolicy,
    bindings: SignalBindings,
    points: tuple[EnergyPoint, ...] = (),
    values: ForecastValues | None = None,
    reason: str | None = None,
    parameters: EnergyParameters | None = None,
    plan: tuple[DrivingInterval, ...] | None = None,
    step_s: float | None = None,
) -> AdvisoryForecast:
    references = tuple(
        ObservationReference(p.event_id, p.stream_seq, p.sim_time_s, p.normalized_sha256)
        for p in points
    )
    numerical_version = MODEL_VERSION if parameters is not None else None
    identity = None
    if values is not None:
        identity = fingerprint(
            {
                "model_version": version,
                "numerical_model_version": numerical_version,
                "scope": asdict(scope),
                "clock": asdict(clock),
                "request": asdict(mission),
                "policy": asdict(policy),
                "bindings": asdict(bindings),
                "observations": [asdict(p) for p in references],
                "parameters": asdict(parameters) if parameters is not None else None,
                "plan": [asdict(p) for p in plan] if plan is not None else None,
                "integration_step_s": step_s,
            }
        )
    return AdvisoryForecast(
        "advisory-energy-forecast/1.0.0",
        version,
        numerical_version,
        "advisory" if values is not None else "unavailable",
        (reason,) if reason else ("model_uncalibrated", "declared_forward_conditions"),
        scope,
        mission,
        clock,
        policy.policy_id,
        clock.as_of_sim_time_s - points[-1].sim_time_s if points else None,
        references,
        identity,
        values,
        step_s,
    )


def reference_forecast(
    packets: tuple[Mapping[str, Any], ...],
    scope: ObservationScope,
    clock: ForecastClock,
    mission: MissionRequest,
    policy: ForecastPolicy,
    bindings: SignalBindings = SignalBindings(),
) -> AdvisoryForecast:
    """Extrapolate net energy/distance and net energy/time as conditional references."""
    try:
        points = _prepare(packets, scope, clock, mission, policy, bindings)
        first, last = points[0], points[-1]
        duration = last.sim_time_s - first.sim_time_s
        distance = last.distance_m - first.distance_m
        if duration < policy.min_window_s:
            raise InputUnavailable("insufficient_history_duration")
        if distance < policy.min_distance_m:
            raise InputUnavailable("insufficient_history_distance")
        consumed_kwh = (first.energy_j - last.energy_j) / JOULES_PER_KWH
        if consumed_kwh <= 0:
            raise InputUnavailable("nonpositive_recent_net_consumption")
        per_m = consumed_kwh / distance
        per_s = consumed_kwh / duration
        if per_m <= 0 or per_s <= 0:
            raise InputUnavailable("consumption_rate_not_representable")
        initial = mission.declared_capacity_kwh * last.soc
        required = number(per_m * mission.remaining_distance_m, "projected_energy")
        final = max(0.0, initial - required)
        unserved = max(0.0, required - initial)
        reserve = mission.reserve_soc * mission.declared_capacity_kwh
        budget = max(0.0, initial - reserve)
        values = ForecastValues(
            initial,
            required,
            final,
            final / mission.declared_capacity_kwh,
            final,
            unserved,
            unserved == 0,
            unserved == 0 and final >= reserve,
            number(budget / per_m, "conditional_distance_to_reserve"),
            number(budget / per_s, "conditional_time_to_reserve"),
            number(per_m * 1000, "recent_net_kwh_per_km"),
            number(per_s * 3600, "recent_average_power_kw"),
        )
        return _result(BASELINE_VERSION, scope, clock, mission, policy, bindings, points, values)
    except InputUnavailable as exc:
        return _result(BASELINE_VERSION, scope, clock, mission, policy, bindings, reason=exc.code)


def planned_forecast(
    packets: tuple[Mapping[str, Any], ...],
    scope: ObservationScope,
    clock: ForecastClock,
    mission: MissionRequest,
    policy: ForecastPolicy,
    parameters: EnergyParameters,
    plan: tuple[DrivingInterval, ...],
    bindings: SignalBindings = SignalBindings(),
    *,
    step_s: float = 0.2,
) -> AdvisoryForecast:
    """Seed the numerical model from the latest usable observation and declared plan."""
    try:
        points = _prepare(packets, scope, clock, mission, policy, bindings)
        if not isinstance(parameters, EnergyParameters):
            raise InputUnavailable("invalid_estimator_parameters")
        if parameters.declared_capacity_kwh != mission.declared_capacity_kwh:
            raise InputUnavailable("declared_capacity_mismatch")
        if not isinstance(plan, tuple) or len(plan) > policy.max_integration_steps:
            raise InputUnavailable("motion_plan_size_invalid")
        if not all(isinstance(p, DrivingInterval) for p in plan):
            raise InputUnavailable("invalid_motion_plan")
        if (
            plan
            and abs(plan[0].start_speed_m_s - points[-1].speed_m_s)
            > policy.initial_speed_tolerance_m_s
        ):
            raise InputUnavailable("plan_initial_speed_mismatch")
        estimate = estimate_energy(
            parameters, plan, points[-1].soc, step_s=step_s, max_steps=policy.max_integration_steps
        )
        if not math.isclose(
            estimate.planned_distance_m,
            mission.remaining_distance_m,
            rel_tol=0,
            abs_tol=policy.plan_distance_tolerance_m,
        ):
            raise InputUnavailable("plan_distance_mismatch")
        initial = estimate.initial_energy_kwh
        final = estimate.final_energy_kwh
        unserved = estimate.unserved_demand_j / JOULES_PER_KWH
        reserve = mission.reserve_soc * mission.declared_capacity_kwh
        values = ForecastValues(
            initial,
            number(initial - final + unserved, "net_mission_energy"),
            final,
            estimate.final_soc,
            estimate.minimum_energy_kwh,
            unserved,
            estimate.energy_plan_feasible,
            estimate.energy_plan_feasible and estimate.minimum_energy_kwh >= reserve,
        )
        return _result(
            PLAN_VERSION,
            scope,
            clock,
            mission,
            policy,
            bindings,
            points,
            values,
            parameters=parameters,
            plan=plan,
            step_s=step_s,
        )
    except InputUnavailable as exc:
        return _result(PLAN_VERSION, scope, clock, mission, policy, bindings, reason=exc.code)
    except (ValueError, ArithmeticError):
        return _result(
            PLAN_VERSION,
            scope,
            clock,
            mission,
            policy,
            bindings,
            reason="invalid_motion_calculation",
        )
