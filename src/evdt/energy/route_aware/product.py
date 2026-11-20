"""Decode an authenticated monitoring snapshot into the numerical boundary."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from .admission import (
    AcceptedPacket,
    InputUnavailable,
    PreparedPlan,
    TrustedClock,
    _signal,
    build_request,
)
from .assignment import Assignment
from .context import SourceContext
from .geometry import RouteUnavailable
from .residuals import EnergyObservation
from .wire import ForecastRequest, decode_record, fingerprint


def prepare_input(
    value: Mapping[str, Any],
    *,
    run_id: str,
    asset_id: str,
    prepare_route: Callable[[Assignment, SourceContext], PreparedPlan],
) -> tuple[Assignment, ForecastRequest]:
    """Call only for a response from the scoped authenticated input endpoint."""
    expected = {
        "version",
        "run_id",
        "asset_id",
        "as_of_real_life_s",
        "assignment",
        "assignment_sha256",
        "clock",
        "packet",
        "context",
        "reason",
    }
    version = value.get("version")
    if version == "trusted-route-energy-input/2.1.0":
        expected.add("current_context")
    if set(value) != expected or version not in (
        "trusted-route-energy-input/2.0.0",
        "trusted-route-energy-input/2.1.0",
    ):
        raise InputUnavailable("unsupported_trusted_input")
    if (value["run_id"], value["asset_id"]) != (run_id, asset_id):
        raise InputUnavailable("trusted_input_scope_mismatch")
    if value["reason"] is not None:
        raise InputUnavailable(value["reason"])
    assignment = decode_record(Assignment, value["assignment"])
    if (assignment.run_id, assignment.asset_id) != (run_id, asset_id) or not assignment.enabled:
        raise InputUnavailable("assignment_scope_or_state")
    if fingerprint(assignment) != value["assignment_sha256"]:
        raise InputUnavailable("assignment_integrity_failure")
    clock = decode_record(TrustedClock, value["clock"])
    context = decode_record(SourceContext, value["context"])
    if assignment.scenario_route_revision is not None and (
        assignment.scenario_route_revision != context.plan.revision
    ):
        raise InputUnavailable("scenario_requires_updated_route_assignment")
    if clock.scope.source_id != assignment.source_id:
        raise InputUnavailable("unassigned_source")
    packet = value["packet"]
    if not isinstance(packet, dict) or set(packet) != {
        "payload",
        "wire_sha256",
        "observed_real_life_s",
        "received_real_life_s",
    }:
        raise InputUnavailable("invalid_trusted_packet")
    try:
        prepared = prepare_route(assignment, context)
    except RouteUnavailable as exc:
        raise InputUnavailable(exc.reason) from exc
    request = build_request(
        scope=clock.scope,
        clock=clock,
        packet=AcceptedPacket(**packet),
        context=context,
        prepared=prepared,
        approved_network_sha256=assignment.network_sha256,
        vehicle=assignment.vehicle,
        conditions=assignment.conditions,
        policy=assignment.policy,
        now_real_life_s=value["as_of_real_life_s"],
        generation=1,
        trigger="first_ready",
        scenario=assignment.scenario,
        current_context=(
            decode_record(SourceContext, value["current_context"])
            if version == "trusted-route-energy-input/2.1.0"
            else None
        ),
    )
    return assignment, replace(request, assignment_sha256=fingerprint(assignment))


def observed_energy(value: Mapping[str, Any], request: ForecastRequest) -> EnergyObservation:
    """Extract only from the same snapshot already admitted by prepare_input."""
    packet = value["packet"]
    payload = packet["payload"]
    if (
        payload["event_id"] != request.state.event_id
        or packet["wire_sha256"] != request.state.wire_sha256
    ):
        raise InputUnavailable("comparison_packet_binding")
    return EnergyObservation(
        request.state.scope,
        request.state.event_id,
        request.state.wire_sha256,
        request.route.revision,
        request.state.working_s,
        packet["received_real_life_s"],
        _signal(payload, "battery.pack.energy", "J", 0, 1e13),
        request.state.charging_state,
        "good",
    )
