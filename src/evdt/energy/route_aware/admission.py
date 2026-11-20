"""Project accepted database records into a minimal route-aware request.

Call only after scoped authentication and a consistent database read. Dataclasses
cannot authenticate a caller. Neither this module nor a numerical worker accepts
browser declarations as current vehicle state.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from . import CONTRACT_VERSION
from .context import SourceContext, SourcePlan
from .contracts import Conditions, LiveState, RouteContext, SourceScope, StopRef, VehicleProfile
from .geometry import RouteUnavailable, reference_length, remaining_route
from .policy import ForecastPolicy
from .scenarios import RouteScenario
from .validation import digest, number
from .wire import ForecastRequest, fingerprint


class InputUnavailable(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class TrustedClock:
    scope: SourceScope
    state: str
    working_s: float
    observed_real_life_s: float
    received_real_life_s: float
    manifest_sha256: str


@dataclass(frozen=True)
class PreparedPlan:
    plan_sha256: str
    route: RouteContext
    external_occurrences: tuple[int, ...]
    first_external_index: int = 0
    scenario_indices: tuple[int, ...] = ()

    def occurrence_for(self, external_index: int) -> int:
        relative = external_index - self.first_external_index
        if not 0 <= relative < len(self.external_occurrences):
            raise InputUnavailable("position_outside_prepared_suffix")
        return self.external_occurrences[relative]

    def project_scenario(self, scenario: RouteScenario | None) -> RouteScenario | None:
        if scenario is None or not self.first_external_index:
            return scenario
        if len(self.scenario_indices) != len(self.route.occurrences):
            raise InputUnavailable("scenario_suffix_mapping_unavailable")
        mapping = {old: new for new, old in enumerate(self.scenario_indices) if old >= 0}
        start = self.scenario_indices[0]

        def index(old: int) -> int:
            if old not in mapping:
                raise InputUnavailable("scenario_changed_lane_path_requires_update")
            return mapping[old]

        return replace(
            scenario,
            roads=tuple(
                replace(r, occurrence_index=index(r.occurrence_index))
                for r in scenario.roads
                if r.occurrence_index >= start
            ),
            stops=tuple(
                replace(
                    s,
                    reference=replace(
                        s.reference, occurrence_index=index(s.reference.occurrence_index)
                    ),
                )
                for s in scenario.stops
                if s.reference.occurrence_index >= start
            ),
            closed_occurrences=tuple(index(i) for i in scenario.closed_occurrences if i >= start),
        )

    def validate(self, plan: SourcePlan) -> None:
        if self.plan_sha256 != plan.revision or self.route.revision != plan.revision:
            raise InputUnavailable("route_revision_mismatch")
        if self.route.network_sha256 != plan.network_sha256:
            raise InputUnavailable("route_network_mismatch")
        if type(self.first_external_index) is not int or not 0 <= self.first_external_index < len(
            plan.edges
        ):
            raise InputUnavailable("route_occurrence_mapping")
        if len(self.external_occurrences) != len(plan.edges) - self.first_external_index:
            raise InputUnavailable("route_occurrence_mapping")
        if tuple(sorted(set(self.external_occurrences))) != self.external_occurrences:
            raise InputUnavailable("route_occurrence_mapping")
        for index, edge in zip(
            self.external_occurrences, plan.edges[self.first_external_index :], strict=True
        ):
            if not 0 <= index < len(self.route.occurrences):
                raise InputUnavailable("route_occurrence_mapping")
            if self.route.occurrences[index].edge_id != edge:
                raise InputUnavailable("route_occurrence_mapping")
        actual = {
            (o.index, s.stop_id, s.offset_m, s.dwell_working_s)
            for o in self.route.occurrences
            for s in o.stops
        }
        expected = {
            (self.occurrence_for(s.external_index), s.stop_id, s.offset_m, s.dwell_working_s)
            for s in plan.remaining_stops
        }
        if actual != expected:
            raise InputUnavailable("route_stop_plan_mismatch")
        for stop in plan.remaining_stops:
            occurrence = self.route.occurrences[self.occurrence_for(stop.external_index)]
            if stop.lane_id not in (occurrence.lane_id, *occurrence.alternative_lane_ids):
                raise InputUnavailable("route_stop_lane_mismatch")


@dataclass(frozen=True)
class AcceptedPacket:
    payload: Mapping[str, Any]
    wire_sha256: str
    observed_real_life_s: float
    received_real_life_s: float


def _fresh(stamp: float, now: float, maximum: float) -> bool:
    return math.isfinite(stamp) and -0.05 <= now - stamp <= maximum


def validate_current_context(
    clock: TrustedClock,
    context: SourceContext,
    current_context: SourceContext | None,
    policy: ForecastPolicy,
    now_real_life_s: float,
) -> None:
    """Bound delivery lag using the authenticated context at the actual source head."""
    if current_context is None:
        # Version 2.0 snapshots have no separate proof of the source's current plan.
        if abs(clock.working_s - context.working_s) > 1e-6:
            raise InputUnavailable("context_not_current")
        return
    if current_context.scope != clock.scope or current_context.scope != context.scope:
        raise InputUnavailable("source_scope_mismatch")
    if current_context.manifest_sha256 != clock.manifest_sha256:
        raise InputUnavailable("registration_mismatch")
    if abs(current_context.working_s - clock.working_s) > 1e-6:
        raise InputUnavailable("context_not_current")
    if not _fresh(
        current_context.observed_real_life_s, now_real_life_s, policy.source_max_age_real_life_s
    ):
        raise InputUnavailable("required_input_stale_or_future")
    if current_context.charging_state != "not_charging":
        raise InputUnavailable("charging_" + current_context.charging_state)
    if current_context.plan.revision != context.plan.revision:
        raise InputUnavailable("current_route_plan_changed")
    if not 0 <= clock.working_s - context.working_s <= policy.observation_lag_limit_working_s:
        raise InputUnavailable("observation_working_lag_exceeded")


def _signal(packet: Mapping[str, Any], identity: str, unit: str, low: float, high: float) -> float:
    values = [s for s in packet["samples"] if s["signal_id"] == identity]
    if len(values) != 1:
        raise InputUnavailable("missing_or_duplicate_" + identity)
    sample = values[0]
    if (
        sample.get("quality") != "good"
        or sample.get("unit") != unit
        or sample.get("provenance") != "simulated-observation"
    ):
        raise InputUnavailable("invalid_" + identity)
    try:
        return number(sample["value"], identity, low, high)
    except ValueError as exc:
        raise InputUnavailable("invalid_" + identity) from exc


def build_request(
    *,
    scope: SourceScope,
    clock: TrustedClock,
    packet: AcceptedPacket,
    context: SourceContext,
    prepared: PreparedPlan,
    approved_network_sha256: str,
    vehicle: VehicleProfile,
    conditions: tuple[Conditions, ...],
    policy: ForecastPolicy,
    now_real_life_s: float,
    generation: int,
    trigger: str,
    scenario: RouteScenario | None = None,
    current_context: SourceContext | None = None,
) -> ForecastRequest:
    """Admit one current packet. A history window is unnecessary for first ready."""
    number(now_real_life_s, "trusted read time", 0, 1e12)
    digest(packet.wire_sha256, "accepted wire digest")
    payload = packet.payload
    try:
        packet_scope = SourceScope(
            *(payload[k] for k in ("run_id", "asset_id", "source_id", "source_epoch", "session_id"))
        )
        if clock.scope != scope or context.scope != scope or packet_scope != scope:
            raise InputUnavailable("source_scope_mismatch")
        if payload["mode"] != "simulation" or payload["clock_domain"] != "simulation":
            raise InputUnavailable("live_source_required")
        if payload["schema_version"] not in ("3.0.0", "3.1.0", "3.2.0"):
            raise InputUnavailable("unsupported_observation_version")
        if clock.state != "running":
            raise InputUnavailable("source_" + clock.state)
        if any(
            not _fresh(t, now_real_life_s, policy.source_max_age_real_life_s)
            for t in (
                clock.observed_real_life_s,
                clock.received_real_life_s,
                packet.observed_real_life_s,
                packet.received_real_life_s,
                context.observed_real_life_s,
            )
        ):
            raise InputUnavailable("required_input_stale_or_future")
        if packet.received_real_life_s + 0.05 < packet.observed_real_life_s:
            raise InputUnavailable("observation_time_order")
        if (
            payload["manifest_sha256"] != clock.manifest_sha256
            or context.manifest_sha256 != clock.manifest_sha256
        ):
            raise InputUnavailable("registration_mismatch")
        if payload.get("annotations", {}).get("energy_context") != fingerprint(context):
            raise InputUnavailable("context_wire_binding")
        if payload["sim_time_s"] != context.working_s:
            raise InputUnavailable("context_working_time_mismatch")
        validate_current_context(clock, context, current_context, policy, now_real_life_s)
        if context.plan.network_sha256 != approved_network_sha256:
            raise InputUnavailable("unassigned_network")
        if context.charging_state != "not_charging":
            raise InputUnavailable("charging_" + context.charging_state)
        prepared.validate(context.plan)
        start = prepared.occurrence_for(context.external_index)
        end = (
            prepared.occurrence_for(context.external_index + 1)
            if context.external_index + 1 < len(context.plan.edges)
            else len(prepared.route.occurrences)
        )
        matches = [
            o
            for o in prepared.route.occurrences[start:end]
            if context.lane_id in (o.lane_id, *o.alternative_lane_ids)
        ]
        if len(matches) != 1:
            raise InputUnavailable("ambiguous_or_unprepared_lane")
        occurrence = matches[0]
        if context.lane_offset_m > reference_length(occurrence) + 1e-6:
            raise InputUnavailable("position_outside_lane")
        current_stop = (
            StopRef(occurrence.index, context.current_stop_id)
            if context.current_stop_id is not None
            else None
        )
        has_temperature = any(
            s["signal_id"] == "battery.pack.temperature" for s in payload["samples"]
        )
        needs_temperature = any(
            (
                vehicle.usable_fraction_by_temperature,
                vehicle.discharge_by_temperature,
                vehicle.regeneration_by_temperature,
            )
        )
        if needs_temperature and not has_temperature:
            raise InputUnavailable("battery_temperature_required")
        temperature = (
            _signal(payload, "battery.pack.temperature", "K", 200, 400) if has_temperature else None
        )
        state = LiveState(
            scope,
            payload["event_id"],
            packet.wire_sha256,
            context.working_s,
            packet.observed_real_life_s,
            packet.received_real_life_s,
            clock.state,
            prepared.route.revision,
            occurrence.index,
            context.lane_offset_m,
            _signal(payload, "battery.pack.soc", "1", 0, 1),
            _signal(payload, "motion.speed", "m/s", 0, 100),
            _signal(payload, "auxiliary.power", "W", 0, 1_000_000),
            context.charging_state,
            temperature,
            (),
            current_stop,
            context.remaining_dwell_working_s,
        )
        # Geometry admission also checks passed-but-unaccounted stops and gaps.
        remaining_route(prepared.route, state)
        return ForecastRequest(
            CONTRACT_VERSION,
            generation,
            now_real_life_s,
            trigger,
            state,
            prepared.route,
            vehicle,
            conditions,
            policy,
            prepared.project_scenario(scenario),
        )
    except InputUnavailable:
        raise
    except RouteUnavailable as exc:
        raise InputUnavailable(exc.reason) from exc
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise InputUnavailable("invalid_trusted_input") from exc
