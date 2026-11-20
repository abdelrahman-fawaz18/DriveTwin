"""Pure route-aware forecast entry point and bounded result contract."""

import json
from dataclasses import dataclass, fields

from . import MODEL_VERSION
from .contracts import SourceScope
from .geometry import RemainingRoute, RouteUnavailable, remaining_route
from .ledger import EnergyLedger, MotionSample, condition_at, integrate
from .motion import MotionPlan, plan_motion, prefix_plan
from .scenarios import apply_stop_assumptions
from .validation import choice, digest, integer, number, text
from .wire import ForecastRequest, decode_record, fingerprint

RESULT_VERSION = "route-aware-result/2.0.0"
SUMO_EULER_MODEL_VERSION = "route-aware-energy/2.0.1-dev"
GEOGRAPHIC_MODEL_VERSION = "route-aware-energy/2.1.0-dev"
FIXED_SIGNAL_MODEL_VERSION = "route-aware-energy/2.1.1-dev"
SOURCE_DISTANCE_MODEL_VERSION = "route-aware-energy/2.1.2-dev"
YELLOW_CLEARANCE_MODEL_VERSION = "route-aware-energy/2.1.3-dev"


def _model_version(request: ForecastRequest) -> str:
    if request.policy.sumo_yellow_clearance:
        return YELLOW_CLEARANCE_MODEL_VERSION
    if request.policy.sumo_braking_source_distance:
        return SOURCE_DISTANCE_MODEL_VERSION
    if request.policy.fixed_signal_timing:
        return FIXED_SIGNAL_MODEL_VERSION
    if request.policy.power_limited_motion or request.policy.road_limits_include_turns:
        return GEOGRAPHIC_MODEL_VERSION
    return SUMO_EULER_MODEL_VERSION if request.policy.sumo_euler_step_working_s else MODEL_VERSION


@dataclass(frozen=True)
class ComparisonWindow:
    duration_working_s: float
    predicted_net_j: float
    integration_steps: int

    def __post_init__(self) -> None:
        number(self.duration_working_s, "comparison duration", 1, 30)
        number(self.predicted_net_j, "comparison energy", -1e14, 1e14)
        integer(self.integration_steps, "comparison steps", 1, 100_000)


@dataclass(frozen=True)
class ForecastSummary:
    remaining_distance_m: float
    duration_working_s: float
    arrival_working_s: float
    arrival_soc: float | None
    minimum_soc: float
    ledger: EnergyLedger
    comparison_window: ComparisonWindow | None = None

    def __post_init__(self) -> None:
        number(self.remaining_distance_m, "remaining distance", 0, 10_000_000)
        number(self.duration_working_s, "forecast duration", 0, 14_400)
        number(self.arrival_working_s, "arrival working time", 0, 1e12)
        if self.arrival_soc is not None:
            number(self.arrival_soc, "arrival charge", -1e-12, 1 + 1e-12)
        number(self.minimum_soc, "minimum charge", -1e-12, 1 + 1e-12)
        if not isinstance(self.ledger, EnergyLedger):
            raise ValueError("Energy ledger required")
        if self.comparison_window is not None and (
            not isinstance(self.comparison_window, ComparisonWindow)
            or self.comparison_window.duration_working_s > self.duration_working_s
        ):
            raise ValueError("Comparison window must lie inside the movement plan")
        for field in fields(self.ledger):
            if field.name.endswith("_j"):
                number(getattr(self.ledger, field.name), field.name, -1e14, 1e14)
        integer(self.ledger.integration_steps, "integration steps", 0, 100_000)
        if (
            type(self.ledger.motion_feasible) is not bool
            or type(self.ledger.reserve_breached) is not bool
        ):
            raise ValueError("Ledger state flags must be boolean")
        if self.ledger.capacity_j <= 0:
            raise ValueError("Positive capacity required")
        for name in (
            "initial_energy_j",
            "battery_draw_j",
            "battery_recovery_j",
            "propulsion_required_j",
            "auxiliary_required_j",
            "regeneration_potential_j",
            "regeneration_used_j",
            "rejected_regeneration_j",
            "unserved_energy_j",
            "drive_conversion_loss_j",
            "braking_to_heat_j",
            "energy_balance_error_j",
        ):
            if getattr(self.ledger, name) < -1e-6:
                raise ValueError("Nonnegative energy account is negative")
        if not isinstance(self.ledger.assumptions, tuple) or len(self.ledger.assumptions) > 128:
            raise ValueError("Bounded ledger assumptions required")
        for assumption in self.ledger.assumptions:
            text(assumption, "ledger assumption")
        tolerance = 1e-6 + 1e-10 * max(
            self.ledger.initial_energy_j, self.ledger.battery_draw_j, self.ledger.battery_recovery_j
        )
        if (
            not -tolerance
            <= self.ledger.minimum_energy_j
            <= min(self.ledger.initial_energy_j, self.ledger.final_energy_j) + tolerance
            or self.ledger.final_energy_j > self.ledger.capacity_j + tolerance
        ):
            raise ValueError("Ledger charge bounds disagree")
        if (
            abs(
                self.ledger.initial_energy_j
                - self.ledger.net_battery_j
                - self.ledger.final_energy_j
            )
            > tolerance
            or abs(
                self.ledger.net_battery_j
                - self.ledger.battery_draw_j
                + self.ledger.battery_recovery_j
            )
            > tolerance
        ):
            raise ValueError("Ledger balance disagrees")
        demand_error = (
            self.ledger.propulsion_required_j
            + self.ledger.auxiliary_required_j
            - self.ledger.battery_draw_j
            - self.ledger.regeneration_used_j
            + self.ledger.battery_recovery_j
            - self.ledger.unserved_energy_j
        )
        recovery_error = (
            self.ledger.regeneration_potential_j
            - self.ledger.regeneration_used_j
            - self.ledger.rejected_regeneration_j
        )
        if (
            max(abs(demand_error), abs(recovery_error), self.ledger.energy_balance_error_j)
            > tolerance
        ):
            raise ValueError("Ledger component balance disagrees")
        if self.ledger.motion_feasible != (self.ledger.unserved_energy_j <= tolerance):
            raise ValueError("Feasibility differs from unserved demand")
        if (
            abs(self.minimum_soc * self.ledger.capacity_j - self.ledger.minimum_energy_j)
            > tolerance
        ):
            raise ValueError("Minimum charge differs from the ledger")
        if (
            self.arrival_soc is not None
            and abs(self.arrival_soc * self.ledger.capacity_j - self.ledger.final_energy_j)
            > tolerance
        ):
            raise ValueError("Arrival charge differs from the ledger")


@dataclass(frozen=True)
class ForecastResult:
    version: str
    model_version: str
    input_sha256: str
    route_sha256: str
    vehicle_sha256: str
    scope: SourceScope
    generation: int
    cutoff_working_s: float
    requested_real_life_s: float
    status: str
    reason: str | None
    uncertainty_method: str
    summary: ForecastSummary | None

    def __post_init__(self) -> None:
        choice(self.version, "result version", (RESULT_VERSION,))
        choice(
            self.model_version,
            "model version",
            (
                MODEL_VERSION,
                SUMO_EULER_MODEL_VERSION,
                GEOGRAPHIC_MODEL_VERSION,
                FIXED_SIGNAL_MODEL_VERSION,
                SOURCE_DISTANCE_MODEL_VERSION,
                YELLOW_CLEARANCE_MODEL_VERSION,
            ),
        )
        choice(self.status, "forecast status", ("available", "infeasible", "unavailable"))
        choice(self.uncertainty_method, "uncertainty method", ("not_established",))
        for value in (self.input_sha256, self.route_sha256, self.vehicle_sha256):
            digest(value, "result input fingerprint")
        if not isinstance(self.scope, SourceScope):
            raise ValueError("Source scope required")
        integer(self.generation, "result generation", 1, 9_007_199_254_740_991)
        number(self.cutoff_working_s, "cutoff working time", 0, 1e12)
        number(self.requested_real_life_s, "request time", 0, 1e12)
        if self.reason is not None:
            text(self.reason, "unavailable reason")
        if self.status == "unavailable":
            if self.summary is not None or self.reason is None:
                raise ValueError("Unavailable result cannot contain an arrival forecast")
        else:
            if not isinstance(self.summary, ForecastSummary):
                raise ValueError("Completed calculation requires its summary")
            if (
                abs(
                    self.summary.arrival_working_s
                    - self.cutoff_working_s
                    - self.summary.duration_working_s
                )
                > 1e-6
            ):
                raise ValueError("Arrival working time differs from plan duration")
            if self.status == "available":
                if (
                    self.reason is not None
                    or self.summary.arrival_soc is None
                    or not self.summary.ledger.motion_feasible
                ):
                    raise ValueError("Available result must have a feasible arrival")
            elif (
                self.reason != "unserved_energy_or_power"
                or self.summary.arrival_soc is not None
                or self.summary.ledger.motion_feasible
            ):
                raise ValueError("Infeasible plan cannot claim an achievable arrival charge")


def _motion(request: ForecastRequest, route: RemainingRoute) -> MotionPlan:
    localized = (
        {road.occurrence_index: road.intervals for road in request.scenario.roads}
        if request.scenario
        else {}
    )
    factors = tuple(
        condition_at(
            localized.get(piece.occurrence_index, request.conditions), request.state.working_s
        ).speed_factor
        for piece in route.pieces
    )
    # Time-dependent caps use the most restrictive interval intersecting a piece.
    # Iteration is bounded; oscillating future assumptions make the result unavailable.
    for _ in range(8):
        movement_factors, accelerations = factors, None
        if request.policy.power_limited_motion:
            from .power import movement_limits

            movement_factors, accelerations = movement_limits(request, route, factors)
        if request.policy.fixed_signal_timing:
            from .signals import plan_fixed_signals

            plan = plan_fixed_signals(
                route,
                request.state,
                request.vehicle,
                request.policy,
                speed_factors=movement_factors,
                accelerations_m_s2=accelerations,
            )
        else:
            plan = plan_motion(
                route,
                request.state,
                request.vehicle,
                request.policy,
                speed_factors=movement_factors,
                accelerations_m_s2=accelerations,
            )
        spans: dict[int, tuple[float, float]] = {}
        for phase in plan.phases:
            if phase.piece_index is None or phase.kind == "dwell":
                continue
            start, end = phase.start_working_s, phase.start_working_s + phase.duration_working_s
            previous = spans.get(phase.piece_index)
            spans[phase.piece_index] = (previous[0] if previous else start, end)
        updated = []
        for index in range(len(route.pieces)):
            if index not in spans:
                raise RouteUnavailable("road_piece_below_motion_resolution")
            start, end = spans[index]
            relevant = [
                c.speed_factor
                for c in localized.get(route.pieces[index].occurrence_index, request.conditions)
                if c.from_working_s < end and c.until_working_s > start
            ]
            if not relevant:
                raise RouteUnavailable("conditions_do_not_cover_plan")
            updated.append(min(relevant))
        if tuple(updated) == factors:
            return plan
        factors = tuple(updated)
    raise RouteUnavailable("movement_conditions_did_not_converge")


def forecast(request: ForecastRequest, *, sample: MotionSample | None = None) -> ForecastResult:
    summary = None
    reason = None
    status = "unavailable"
    try:
        route = remaining_route(
            request.route, request.state, max_pieces=request.policy.max_segments
        )
        route = apply_stop_assumptions(route, request.scenario)
        plan = _motion(request, route)
        ledger = integrate(
            route,
            plan,
            request.state,
            request.vehicle,
            request.conditions,
            request.policy,
            scenario=request.scenario,
            sample=sample,
        )
        status = "available" if ledger.motion_feasible else "infeasible"
        reason = None if ledger.motion_feasible else "unserved_energy_or_power"
        comparison = None
        # Save a causal near-term prediction now; its later assessment needs no
        # second model execution on the controller or access to future inputs.
        duration = min(30, int(plan.duration_working_s))
        if ledger.motion_feasible and duration >= 1:
            prefix = integrate(
                route,
                prefix_plan(plan, duration),
                request.state,
                request.vehicle,
                request.conditions,
                request.policy,
                scenario=request.scenario,
            )
            if (
                ledger.integration_steps + prefix.integration_steps
                > request.policy.max_integration_steps
            ):
                raise RouteUnavailable("combined_integration_budget_exceeded")
            comparison = ComparisonWindow(duration, prefix.net_battery_j, prefix.integration_steps)
        summary = ForecastSummary(
            plan.distance_m,
            plan.duration_working_s,
            request.state.working_s + plan.duration_working_s,
            ledger.final_energy_j / ledger.capacity_j if ledger.motion_feasible else None,
            ledger.minimum_energy_j / ledger.capacity_j,
            ledger,
            comparison,
        )
    except RouteUnavailable as exc:
        status, summary = "unavailable", None
        reason = exc.reason
    return ForecastResult(
        RESULT_VERSION,
        _model_version(request),
        fingerprint(request),
        fingerprint(request.route),
        fingerprint(request.vehicle),
        request.state.scope,
        request.generation,
        request.state.working_s,
        request.requested_real_life_s,
        status,
        reason,
        "not_established",
        summary,
    )


def parse_result(raw: bytes) -> ForecastResult:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 262_144:
        raise ValueError("Result exceeds its byte limit")
    from .wire import _bad_constant, _pairs

    try:
        return decode_record(
            ForecastResult,
            json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant),
        )
    except (UnicodeError, RecursionError, OverflowError, TypeError) as exc:
        raise ValueError("Malformed forecast result") from exc


def validate_result(request: ForecastRequest, result: ForecastResult) -> None:
    if (
        result.model_version != _model_version(request)
        or result.input_sha256 != fingerprint(request)
        or result.scope != request.state.scope
        or result.generation != request.generation
        or result.route_sha256 != fingerprint(request.route)
        or result.vehicle_sha256 != fingerprint(request.vehicle)
        or result.cutoff_working_s != request.state.working_s
        or result.requested_real_life_s != request.requested_real_life_s
    ):
        raise ValueError("Result does not match its input generation and source")
    if result.summary is not None:
        ledger = result.summary.ledger
        capacity = request.vehicle.capacity_kwh * 3_600_000
        if (
            abs(ledger.capacity_j - capacity) > 1e-6
            or abs(ledger.initial_energy_j - request.state.soc * capacity) > 1e-6
        ):
            raise ValueError("Result uses a different battery state/profile")
        if ledger.reserve_breached != (
            ledger.minimum_energy_j < request.vehicle.reserve_soc * capacity
        ):
            raise ValueError("Reserve flag differs from the recorded minimum")
        comparison = result.summary.comparison_window
        if (
            ledger.integration_steps + (comparison.integration_steps if comparison else 0)
            > request.policy.max_integration_steps
        ):
            raise ValueError("Result exceeds the combined integration budget")
