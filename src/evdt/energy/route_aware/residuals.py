"""Causal error records for a frozen forecast's completed working-time prefix."""

from dataclasses import dataclass

from .contracts import SourceScope
from .forecast import ForecastResult, _motion, validate_result
from .geometry import remaining_route
from .ledger import integrate
from .motion import prefix_plan
from .scenarios import apply_stop_assumptions
from .validation import choice, digest, number, sequence, text
from .wire import ForecastRequest, fingerprint


@dataclass(frozen=True)
class EnergyObservation:
    scope: SourceScope
    event_id: str
    wire_sha256: str
    route_revision: str
    working_s: float
    received_real_life_s: float
    stored_energy_j: float
    charging_state: str
    quality: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, SourceScope):
            raise ValueError("Energy observation scope required")
        text(self.event_id, "energy event")
        digest(self.wire_sha256, "energy wire fingerprint")
        text(self.route_revision, "energy route revision")
        number(self.working_s, "energy working time", 0, 1e12)
        number(self.received_real_life_s, "energy receipt time", 0, 1e12)
        number(self.stored_energy_j, "stored energy", 0, 1e13)
        choice(
            self.charging_state, "energy charging state", ("not_charging", "charging", "unknown")
        )
        choice(self.quality, "energy quality", ("good", "suspect", "invalid"))


@dataclass(frozen=True)
class Residual:
    input_sha256: str
    scope: SourceScope
    route_revision: str
    from_working_s: float
    until_working_s: float
    assessed_real_life_s: float
    observation_sha256: str
    predicted_net_j: float
    observed_net_j: float
    signed_error_j: float
    interpretation: str = "positive_means_overprediction_at_equal_elapsed_working_time"

    def __post_init__(self) -> None:
        digest(self.input_sha256, "residual input")
        digest(self.observation_sha256, "residual observations")
        if not isinstance(self.scope, SourceScope):
            raise ValueError("Residual source scope required")
        text(self.route_revision, "residual route revision")
        number(self.from_working_s, "residual start", 0, 1e12)
        number(self.until_working_s, "residual end", self.from_working_s + 1e-9, 1e12)
        number(self.assessed_real_life_s, "residual assessment", 0, 1e12)
        for value in (self.predicted_net_j, self.observed_net_j, self.signed_error_j):
            number(value, "residual energy", -1e14, 1e14)
        if abs(self.predicted_net_j - self.observed_net_j - self.signed_error_j) > 1e-6:
            raise ValueError("Residual signed error does not close")
        choice(
            self.interpretation,
            "residual interpretation",
            ("positive_means_overprediction_at_equal_elapsed_working_time",),
        )


def assess_saved_window(
    request: ForecastRequest,
    result: ForecastResult,
    observations: tuple[EnergyObservation, ...],
    *,
    assessed_real_life_s: float,
) -> Residual:
    """Assess the worker's saved prefix without recomputing its prediction."""
    validate_result(request, result)
    if (
        result.status != "available"
        or result.summary is None
        or result.summary.comparison_window is None
    ):
        raise ValueError("A feasible saved comparison window is required")
    window = result.summary.comparison_window
    sequence(observations, "comparison observations", EnergyObservation, 2, 1000)
    number(assessed_real_life_s, "assessment time", request.requested_real_life_s, 1e12)
    capacity = request.vehicle.capacity_kwh * 3_600_000
    if (
        observations[0].working_s != request.state.working_s
        or observations[-1].working_s != request.state.working_s + window.duration_working_s
        or len({o.event_id for o in observations}) != len(observations)
        or any(
            o.scope != request.state.scope
            or o.route_revision != request.route.revision
            or o.quality != "good"
            or o.charging_state != "not_charging"
            or o.received_real_life_s > assessed_real_life_s
            or o.stored_energy_j > capacity
            for o in observations
        )
        or any(
            not 0 < b.working_s - a.working_s <= 1.000001
            or b.received_real_life_s < a.received_real_life_s
            for a, b in zip(observations, observations[1:])
        )
        or abs(observations[0].stored_energy_j - request.state.soc * capacity)
        > max(1, capacity * 1e-6)
    ):
        raise ValueError("Comparison observations are incomplete, incompatible, or noncausal")
    observed = observations[0].stored_energy_j - observations[-1].stored_energy_j
    return Residual(
        fingerprint(request),
        request.state.scope,
        request.route.revision,
        observations[0].working_s,
        observations[-1].working_s,
        assessed_real_life_s,
        fingerprint(observations),
        window.predicted_net_j,
        observed,
        window.predicted_net_j - observed,
    )


def compare_prefix(
    request: ForecastRequest,
    observations: tuple[EnergyObservation, ...],
    *,
    assessed_real_life_s: float,
    max_gap_working_s: float = 5,
) -> Residual:
    """Compare with later readings; keep the original forward assumptions intact."""
    sequence(observations, "completed energy observations", EnergyObservation, 2, 20_000)
    number(assessed_real_life_s, "residual assessment time", request.requested_real_life_s, 1e12)
    number(max_gap_working_s, "maximum observation gap", 0.001, 60)
    if len({item.event_id for item in observations}) != len(observations):
        raise ValueError("Repeated energy observation")
    for item in observations:
        if (
            item.scope != request.state.scope
            or item.route_revision != request.route.revision
            or item.quality != "good"
            or item.charging_state != "not_charging"
            or item.received_real_life_s > assessed_real_life_s
        ):
            raise ValueError("Residual input identity, quality, charging, or causality mismatch")
    if observations[0].working_s != request.state.working_s:
        raise ValueError("Residual must begin at the original forecast cutoff")
    if any(
        not 0 < b.working_s - a.working_s <= max_gap_working_s
        or b.received_real_life_s < a.received_real_life_s
        for a, b in zip(observations, observations[1:])
    ):
        raise ValueError("Residual observations must be ordered and sufficiently complete")
    capacity = request.vehicle.capacity_kwh * 3_600_000
    if any(item.stored_energy_j > capacity for item in observations):
        raise ValueError("Observed energy exceeds the assigned capacity")
    if abs(observations[0].stored_energy_j - request.state.soc * capacity) > max(
        1, capacity * 1e-6
    ):
        raise ValueError("Observed energy and charge do not share the assigned capacity scale")
    route = remaining_route(request.route, request.state, max_pieces=request.policy.max_segments)
    route = apply_stop_assumptions(route, request.scenario)
    plan = _motion(request, route)
    prefix = prefix_plan(plan, observations[-1].working_s - observations[0].working_s)
    ledger = integrate(
        route,
        prefix,
        request.state,
        request.vehicle,
        request.conditions,
        request.policy,
        scenario=request.scenario,
    )
    if not ledger.motion_feasible:
        raise ValueError("Infeasible prediction cannot establish an accuracy residual")
    observed = observations[0].stored_energy_j - observations[-1].stored_energy_j
    return Residual(
        fingerprint(request),
        request.state.scope,
        request.route.revision,
        observations[0].working_s,
        observations[-1].working_s,
        assessed_real_life_s,
        fingerprint(observations),
        ledger.net_battery_j,
        observed,
        ledger.net_battery_j - observed,
    )


def persistent_mismatch(residuals: tuple[Residual, ...], *, threshold_j: float) -> bool:
    """Three independent nonoverlapping intervals with same-sign material errors."""
    sequence(residuals, "residual window", Residual, 0, 100)
    number(threshold_j, "residual threshold", 0.001, 1e12)
    selected = residuals[-3:]
    if len(selected) < 3:
        return False
    if any(
        a.scope != b.scope
        or a.route_revision != b.route_revision
        or a.until_working_s > b.from_working_s
        for a, b in zip(selected, selected[1:])
    ):
        raise ValueError("Mismatch intervals must be independent and share source and route")
    return all(r.signed_error_j >= threshold_j for r in selected) or all(
        r.signed_error_j <= -threshold_j for r in selected
    )
