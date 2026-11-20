"""Deterministic lifecycle; storage owns durable fences and global worker exclusion.

All clock values are explicit. Monotonic values are valid only within one
controller process. Restoring state requires reconciliation of any owned worker.
"""

import math
from dataclasses import dataclass, replace

from .forecast import ForecastResult, validate_result
from .validation import integer, number, text
from .wire import ForecastRequest, fingerprint


def route_context_key(request: ForecastRequest) -> str:
    """Identify the assigned path independently of its prepared remaining suffix.

    The authenticated input builder binds an assignment to the complete geometry
    bundle and a source plan revision. Its occurrence indices can change as the
    bus progresses. Standalone requests have no such binding and retain full
    geometry comparison.
    """
    if request.assignment_sha256 is None:
        return fingerprint(request.route)
    route = request.route
    return fingerprint(
        (
            route.network_sha256,
            route.revision,
            route.horizontal_frame,
            route.vertical_datum,
            route.elevation_origin,
        )
    )


def context_key(request: ForecastRequest) -> str:
    """Changes that invalidate a result independently of ordinary bus progress."""
    return fingerprint(
        (
            request.state.scope,
            route_context_key(request),
            request.vehicle,
            request.conditions,
            request.policy,
            # Assigned scenarios are covered by the original assignment digest.
            # Their prepared occurrence indices shift with the remaining route.
            request.scenario if request.assignment_sha256 is None else None,
            request.assignment_sha256,
        )
    )


@dataclass(frozen=True)
class Impact:
    energy_kwh: float = 0
    soc_fraction: float = 0
    reserve_crossed: bool = False
    domain_changed: bool = False

    def __post_init__(self) -> None:
        number(self.energy_kwh, "estimated impact", 0, 1_000_000)
        number(self.soc_fraction, "estimated charge change", 0, 1)
        if type(self.reserve_crossed) is not bool:
            raise ValueError("Reserve classification must be boolean")
        if type(self.domain_changed) is not bool:
            raise ValueError("Domain classification must be boolean")


@dataclass(frozen=True)
class Claim:
    fence: int
    owner: str
    request: ForecastRequest
    started_monotonic_s: float
    deadline_monotonic_s: float


@dataclass(frozen=True)
class Completion:
    result: ForecastResult
    classification: str
    reason: str
    fence: int


@dataclass
class Scheduler:
    """One asset, one active request, one replaceable latest snapshot.

    This object is serialized transactionally by the controller repository.
    A second controller cannot gain authority by constructing another object.
    """

    latest: ForecastRequest | None = None
    accepted_request: ForecastRequest | None = None
    accepted_result: ForecastResult | None = None
    active: Claim | None = None
    phase: str = "waiting"
    reason: str = "required_inputs_unavailable"
    pending_reason: str | None = None
    scope_key: str | None = None
    relevant_key: str | None = None
    configuration_key: str | None = None
    high_water_working_s: float = 0
    generation: int = 0
    last_fence: int = 0
    failures: int = 0
    retry_after_monotonic_s: float = 0
    material_since_working_s: float | None = None
    material_latched: bool = False
    last_material_working_s: float | None = None
    coalesced_periods: int = 0

    def observe(
        self,
        request: ForecastRequest | None,
        *,
        unavailable: str = "stale",
        impact: Impact = Impact(),
    ) -> None:
        if request is None:
            text(unavailable, "unavailable reason")
            self.latest, self.pending_reason = None, None
            self.phase = (
                "paused"
                if unavailable == "source_paused"
                else ("finished" if unavailable == "source_finished" else "waiting")
            )
            self.reason = unavailable
            self.material_since_working_s = None
            return
        working = request.state.working_s
        scope = fingerprint(request.state.scope)
        relevant = context_key(request)
        configuration = fingerprint(
            (route_context_key(request), request.vehicle, request.policy, request.assignment_sha256)
        )
        changed_configuration = self.configuration_key != configuration
        previous, previous_phase = self.latest, self.phase
        if self.scope_key == scope and working < self.high_water_working_s:
            self.observe(None, unavailable="working_clock_regression")
            return
        changed_scope = self.scope_key != scope
        route_reference = previous or (
            self.active.request if self.active else self.accepted_request
        )
        changed_route = route_reference is not None and route_context_key(
            request
        ) != route_context_key(route_reference)
        changed_profile = previous is not None and (
            request.vehicle != previous.vehicle
            or request.policy != previous.policy
            or request.assignment_sha256 != previous.assignment_sha256
        )
        first = self.scope_key is None
        self.latest = request
        self.phase, self.reason = (
            ("calculating", "worker_active") if self.active else ("ready", "inputs_ready")
        )
        self.scope_key, self.relevant_key = scope, relevant
        self.configuration_key = configuration
        self.high_water_working_s = working
        if changed_scope or changed_configuration:
            self.failures, self.retry_after_monotonic_s = 0, 0
            self.material_since_working_s, self.material_latched = None, False
        if first:
            self.pending_reason = "first_ready"
        elif changed_scope or previous_phase in ("paused", "finished"):
            self.pending_reason = "recovery"
        elif changed_route:
            self.pending_reason = "route_change"
        elif previous_phase == "waiting":
            self.pending_reason = "recovery"
        elif changed_profile:
            # A confirmed vehicle/policy revision is a new declared model input.
            self.pending_reason = "conditions"
        if self.failures >= 2:
            self.phase, self.reason, self.pending_reason = "failed", "worker_retry_exhausted", None
            return
        if self.pending_reason is not None:
            return
        reference = self.active.request if self.active else self.accepted_request
        if reference is None:
            self.pending_reason = "first_ready"
            return
        if working - reference.state.working_s >= request.policy.period_working_s:
            self.pending_reason = "periodic"
            return
        policy = request.policy
        remaining_draw = 0.0
        if self.accepted_result and self.accepted_result.summary:
            remaining_draw = self.accepted_result.summary.ledger.battery_draw_j / 3_600_000
        energy_limit = max(
            policy.condition_energy_kwh, policy.condition_energy_fraction * remaining_draw
        )
        ratio = max(
            impact.energy_kwh / energy_limit, impact.soc_fraction / policy.condition_soc_fraction
        )
        if impact.reserve_crossed or impact.domain_changed:
            self.pending_reason = "conditions"
            return
        if ratio < policy.hysteresis_reset_fraction:
            self.material_latched, self.material_since_working_s = False, None
        elif ratio >= 1 and not self.material_latched:
            if self.material_since_working_s is None:
                self.material_since_working_s = working
            cooled = (
                self.last_material_working_s is None
                or working - self.last_material_working_s >= policy.condition_cooldown_working_s
            )
            if (
                working - self.material_since_working_s >= policy.condition_persistence_working_s
                and cooled
            ):
                self.pending_reason = "conditions"
                self.material_latched = True
        elif ratio < 1:
            self.material_since_working_s = None

    def claim(self, *, fence: int, owner: str, monotonic_s: float) -> Claim | None:
        """Call under the durable global lock and asset row lock."""
        integer(fence, "fence", 1, 9_007_199_254_740_991)
        text(owner, "controller owner")
        number(monotonic_s, "monotonic clock", 0, 1e15)
        if self.active or not self.latest or not self.pending_reason or self.failures >= 2:
            return None
        if monotonic_s < self.retry_after_monotonic_s:
            return None
        if fence <= self.last_fence:
            raise ValueError("A new claim requires a newer durable fence")
        self.generation += 1
        request = replace(self.latest, generation=self.generation, trigger=self.pending_reason)
        reference = self.accepted_request
        if reference and reference.state.scope == request.state.scope:
            elapsed = request.state.working_s - reference.state.working_s
            self.coalesced_periods += max(
                0, math.floor(elapsed / request.policy.period_working_s) - 1
            )
        self.active = Claim(
            fence,
            owner,
            request,
            monotonic_s,
            monotonic_s + request.policy.worker_deadline_real_life_s,
        )
        self.last_fence = fence
        if self.pending_reason == "conditions":
            self.last_material_working_s = request.state.working_s
        self.pending_reason = None
        self.phase, self.reason = "calculating", "worker_active"
        return self.active

    def expired(self, monotonic_s: float) -> bool:
        number(monotonic_s, "monotonic clock", 0, 1e15)
        return self.active is not None and monotonic_s >= self.active.deadline_monotonic_s

    def _owned(self, fence: int, owner: str) -> Claim:
        if not self.active or (self.active.fence, self.active.owner) != (fence, owner):
            raise ValueError("Obsolete or foreign worker claim")
        return self.active

    def finish(
        self,
        result: ForecastResult,
        *,
        fence: int,
        owner: str,
        monotonic_s: float,
        now_real_life_s: float,
    ) -> Completion:
        active = self._owned(fence, owner)
        validate_result(active.request, result)
        reason = self._validity(active.request, monotonic_s, now_real_life_s)
        self.active = None
        classification = "current" if reason == "current" else "historical"
        if classification == "current":
            self.accepted_request, self.accepted_result = active.request, result
            self.failures = 0
        elif reason in ("worker_deadline_exceeded", "monotonic_clock_regression"):
            self.failures += 1
            self.retry_after_monotonic_s = monotonic_s + 1
            self.pending_reason = "recovery" if self.failures < 2 and self.latest else None
        self.phase = "ready" if self.latest else self.phase
        self.reason = reason
        return Completion(result, classification, reason, fence)

    def _validity(
        self,
        request: ForecastRequest,
        monotonic_s: float,
        now_real_life_s: float,
        *,
        check_deadline: bool = True,
    ) -> str:
        number(now_real_life_s, "read time", 0, 1e12)
        number(monotonic_s, "monotonic clock", 0, 1e15)
        if check_deadline and self.active and monotonic_s < self.active.started_monotonic_s:
            return "monotonic_clock_regression"
        if check_deadline and self.expired(monotonic_s):
            return "worker_deadline_exceeded"
        if not self.latest:
            return "current_source_unavailable"
        if context_key(request) != context_key(self.latest):
            return "obsolete_context"
        if (
            not -0.05
            <= now_real_life_s - self.latest.requested_real_life_s
            <= request.policy.source_max_age_real_life_s
        ):
            return "current_source_stale"
        if any(
            not -0.05 <= now_real_life_s - stamp <= request.policy.source_max_age_real_life_s
            for stamp in (
                self.latest.state.observed_real_life_s,
                self.latest.state.received_real_life_s,
            )
        ):
            return "current_source_stale"
        working_age = self.latest.state.working_s - request.state.working_s
        real_age = now_real_life_s - request.requested_real_life_s
        if not 0 <= working_age <= request.policy.forecast_max_age_working_s:
            return "forecast_working_age_exceeded"
        if not -0.05 <= real_age <= request.policy.forecast_max_age_real_life_s:
            return "forecast_real_life_age_exceeded"
        return "current"

    def current_status(self, *, now_real_life_s: float, monotonic_s: float) -> str:
        if not self.latest:
            return self.reason
        if not self.accepted_request:
            return "pending" if self.active or self.pending_reason else self.reason
        # An unrelated new calculation's deadline cannot expire a prior valid result.
        return self._validity(
            self.accepted_request, monotonic_s, now_real_life_s, check_deadline=False
        )

    def failed(self, *, fence: int, owner: str, monotonic_s: float) -> None:
        active = self._owned(fence, owner)
        number(monotonic_s, "monotonic clock", 0, 1e15)
        self.active = None
        if self.latest and context_key(active.request) != context_key(self.latest):
            self.phase, self.reason = "ready", "superseded_worker_stopped"
            if self.pending_reason is None:
                self.pending_reason = "recovery"
            return
        self.failures += 1
        self.retry_after_monotonic_s = monotonic_s + 1
        self.pending_reason = "recovery" if self.failures < 2 and self.latest else None
        self.phase = "ready" if self.failures < 2 else "failed"
        self.reason = "worker_retry_pending" if self.failures < 2 else "worker_retry_exhausted"

    def reconcile_restart(self, *, owned_worker_stopped: bool) -> None:
        """Durable state never resumes an old process's monotonic lease."""
        if type(owned_worker_stopped) is not bool or not owned_worker_stopped:
            raise ValueError("Confirm owned worker cleanup before releasing its claim")
        if self.active:
            self.active = None
            self.failures += 1
        self.latest, self.pending_reason = None, None
        self.retry_after_monotonic_s = 0
        self.phase, self.reason = "waiting", "restart_requires_fresh_input"
