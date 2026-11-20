"""Project a verified hold into a separate, hypothetical departure calculation."""

from dataclasses import replace

from .moving_charge_recovery import RECOVERY_HOLD_REAL_LIFE_S, HeldChargeStop
from .route_aware import CONTRACT_VERSION
from .route_aware.admission import InputUnavailable, TrustedClock
from .route_aware.context import SourceContext, SourcePlan
from .route_aware.contracts import LiveState
from .route_aware.geometry import remaining_route
from .route_aware.wire import ForecastRequest, canonical_bytes, fingerprint


def prepare_replacement(held, clock, *, assignment, stations, provider, now):
    """Use only an accepted producer receipt and its authenticated paused clock."""
    if not isinstance(held, HeldChargeStop) or not isinstance(clock, TrustedClock):
        raise InputUnavailable("held_charge_receipt_missing")
    if (
        held.departure is None
        or clock.state != "paused"
        or clock.scope != held.scope
        or clock.working_s != held.working_s
        or clock.manifest_sha256 != held.manifest_sha256
        or not held.observed_real_life_s
        <= now
        < held.observed_real_life_s + RECOVERY_HOLD_REAL_LIFE_S
        or not 0 <= now - clock.received_real_life_s <= now - clock.observed_real_life_s <= 3
    ):
        raise InputUnavailable("held_charge_source_unavailable")
    if (
        not assignment.enabled
        or (assignment.run_id, assignment.asset_id, assignment.source_id)
        != (held.scope.run_id, held.scope.asset_id, held.scope.source_id)
        or (stations.run_id, stations.asset_id) != (held.scope.run_id, held.scope.asset_id)
        or stations.network_sha256 != assignment.network_sha256
        or fingerprint(stations) != held.assignment_sha256
    ):
        raise InputUnavailable("held_charge_assignment_changed")
    original = next((entry for entry in stations.bays if entry.bay.bay_id == held.bay_id), None)
    if (
        original is None
        or original.detour is not None
        or original.external_index != held.external_index
        or original.bay.lane_id != held.lane_id
        or not original.bay.start_offset_m
        <= held.lane_offset_m
        <= original.bay.end_offset_m + 0.001
    ):
        raise InputUnavailable("held_charge_bay_unverified")
    plan = SourcePlan(
        stations.network_sha256, stations.baseline_edges, held.departure.remaining_stops
    )
    if plan.revision != held.route_revision or assignment.scenario_route_revision not in (
        None,
        plan.revision,
    ):
        raise InputUnavailable("held_charge_trip_plan_changed")
    # This context describes a hypothetical release at zero speed from the held
    # position. Do not register it as a running clock, telemetry, or live result.
    context = SourceContext(
        "source-route-context/2.0.0",
        held.scope,
        held.manifest_sha256,
        held.working_s,
        clock.observed_real_life_s,
        plan,
        held.external_index,
        held.lane_id,
        held.lane_offset_m,
        "not_charging",
        None,
        None,
    )
    prepared = provider(assignment, context)
    occurrence = prepared.occurrence_for(held.external_index)
    road = prepared.route.occurrences[occurrence]
    if road.lane_id != held.lane_id:
        raise InputUnavailable("held_charge_lane_unverified")
    state = LiveState(
        held.scope,
        held.interrupted_command_id,
        fingerprint(held),
        held.working_s,
        clock.observed_real_life_s,
        clock.received_real_life_s,
        "running",
        plan.revision,
        occurrence,
        held.lane_offset_m,
        held.soc,
        0,
        held.departure.auxiliary_power_w,
        "not_charging",
        held.departure.battery_temperature_k,
    )
    request = ForecastRequest(
        CONTRACT_VERSION,
        1,
        now,
        "recovery",
        state,
        prepared.route,
        assignment.vehicle,
        assignment.conditions,
        assignment.policy,
        prepared.project_scenario(assignment.scenario),
        fingerprint(assignment),
    )
    if len(canonical_bytes(request)) > request.policy.max_request_bytes:
        raise InputUnavailable("held_charge_forecast_too_large")
    remaining_route(request.route, state)
    job = stations.prepare_job(
        request,
        context,
        prepared,
        assignment_sha256=held.assignment_sha256,
        now_real_life_s=now,
    )
    downstream = {
        entry.bay.bay_id
        for entry in stations.bays
        if entry.detour is None and entry.external_index > held.external_index
    }
    return replace(
        job, offers=tuple(offer for offer in job.offers if offer.stop.bay.bay_id in downstream)
    )
