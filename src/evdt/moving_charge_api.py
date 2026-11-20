"""Optional operator approval and private executor handoff for simulated on-route charging."""

import json
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, ValidationError

from evdt import command_journal, moving_charge_journal
from evdt.charging_wire import Digest, Scope, Seconds, UUIDText
from evdt.command_api import bounded_body, executor, operator
from evdt.command_timing import ClockLease, host_clock
from evdt.commands import WIRE_PROFILE
from evdt.contract_profile import Rejected, decode
from evdt.energy.moving_charge_assignment import StationAssignment
from evdt.energy.moving_charge_handoff import ReviewedStop, revalidate
from evdt.energy.route_aware.admission import InputUnavailable, _signal
from evdt.energy.route_aware.context import SourceContext
from evdt.energy.route_aware.wire import decode_record, fingerprint
from evdt.monitoring.charging_advisory_view import present, read_state
from evdt.monitoring.charging_stop import MOVING_CHARGE_STOP_LIMIT_S

router = APIRouter()
Body = Annotated[bytes, Depends(bounded_body)]


class Approval(Scope):
    command_id: UUIDText
    job_fence: Annotated[int, Field(ge=1, le=9_007_199_254_740_991)]
    candidate_sha256: Digest


class Command(Scope):
    command_id: UUIDText


class Acknowledgment(Command):
    status: Literal["applied", "refused", "unknown"]


class ExecutorClaim(Command):
    sim_s: Seconds


@lru_cache(maxsize=1)
def assignment():
    path = Path(os.environ["EVDT_MOVING_CHARGE_ASSIGNMENT"])
    if path.stat().st_size > 512 * 1024:
        raise RuntimeError("Moving charging assignment exceeds the configured bound")
    return decode_record(StationAssignment, json.loads(path.read_bytes()))


def validated(raw, model):
    try:
        value = model.model_validate(decode(raw, WIRE_PROFILE), strict=True)
    except ValidationError as exc:
        raise Rejected("moving_charge_schema") from exc
    target = command_journal.scope()
    if (value.run_id, value.asset_id) != (target.run_id, target.asset_id):
        raise Rejected("wrong_target")
    return value, (value.run_id, value.asset_id), UUID(value.command_id)


def context_receipt(conn, context, now_real_life_s):
    # The heartbeat receipt can precede creation of this route observation.
    row = conn.execute(
        """SELECT received_at FROM m_route_contexts
           WHERE run_id=%s AND context_sha256=%s""",
        (context.scope.run_id, fingerprint(context)),
    ).fetchone()
    if row is None:
        raise Rejected("moving_charge_stale_progress")
    received = row["received_at"].timestamp()
    age = now_real_life_s - context.observed_real_life_s
    if not 0 <= now_real_life_s - received <= age <= 3:
        raise Rejected("moving_charge_stale_progress")
    return received


def approach_handoff_floor(candidate, planning_floor):
    """Keep the full forecast approach and wait consumption as a conservative allowance."""
    initial = candidate["approach"]["initial"]["soc"]
    minimum = min(
        candidate["approach"]["forecast"]["summary"]["minimum_soc"],
        candidate["transfer"]["plug_in_soc"],
    )
    # Charge lost after the estimate gets no credit for distance already driven.
    return planning_floor + max(0.0, initial - minimum)


def review(conn, scope, fence, candidate_id, *, approved=None, working_s=None, snapshot=None):
    current, forecast, job = read_state(conn, *scope) if snapshot is None else snapshot
    stations = assignment()
    station_hash = fingerprint(stations)
    shown = present(job, current, forecast, stations.policy.margin_soc)
    if (
        shown["status"] != "provisional_plan"
        or not job
        or job["fence"] != fence
        or job["assignment_sha256"] != station_hash
        or (stations.run_id, stations.asset_id) != scope
    ):
        raise Rejected("moving_charge_intent_unavailable")
    decision = job["payload"]["decision"]
    admissible = {shown["estimate"]["bay_id"], shown["estimate"]["fallback_bay_id"]}
    chosen = next(
        (
            item
            for item in (decision["preferred"], decision["fallback"])
            if item is not None
            and item["candidate_sha256"] == candidate_id
            and item["bay_id"] in admissible
        ),
        None,
    )
    if chosen is None:
        raise Rejected("moving_charge_intent_unavailable")
    context = decode_record(SourceContext, current["current_context"] or current["context"])
    packet_context = decode_record(SourceContext, current["context"])
    bay = next((row for row in stations.bays if row.bay.bay_id == chosen["bay_id"]), None)
    if bay is None:
        raise Rejected("moving_charge_intent_unavailable")
    clock = host_clock()
    packet = current["packet"]
    # Source context is registered synchronously. The matching telemetry packet
    # reaches PostgreSQL through the sender and can still be in transit.
    if not (
        packet["payload"]["annotations"].get("energy_context") == fingerprint(packet_context)
        and packet["payload"]["manifest_sha256"] == context.manifest_sha256
        and packet["payload"]["sim_time_s"] == packet_context.working_s
        and 0 <= context.working_s - packet["payload"]["sim_time_s"] <= 1.2
        and 0 <= clock.utc.timestamp() - packet["received_real_life_s"] <= 3
    ):
        raise Rejected("moving_charge_stale_progress")
    if context.plan.edges != stations.baseline_edges:
        raise Rejected("moving_charge_obsolete_plan")
    detour_sha256 = fingerprint(bay.detour) if bay.detour else None
    if bay.detour is not None and current["assignment"].get("scenario") is not None:
        raise Rejected("moving_charge_obsolete_plan")
    station_statuses = stations.scenario.observe(
        run_id=scope[0],
        working_s=context.working_s,
        observed_real_life_s=context.observed_real_life_s,
        received_real_life_s=context_receipt(conn, context, clock.utc.timestamp()),
        expected_sha256=stations.scenario.sha256,
    )
    status = next(item for item in station_statuses if item.bay_id == bay.bay.bay_id)
    transfer = chosen["transfer"]
    approach_arrival = chosen["approach"]["forecast"]["summary"]["arrival_working_s"]
    if (
        transfer["end_working_s"] >= status.open_until_working_s
        or status.observed_working_s >= approach_arrival
    ):
        raise Rejected("moving_charge_bay_unavailable")
    queue_opening = None
    if (
        os.environ.get("EVDT_MOVING_CHARGE_QUEUE_TRIAL") == "1"
        and status.state == "occupied"
        and status.available_from_working_s is not None
        and detour_sha256 is None
        and approach_arrival < status.available_from_working_s
        and abs(
            transfer["start_working_s"]
            - (status.available_from_working_s + bay.bay.setup_working_s)
        )
        <= 1e-6
    ):
        queue_opening = status.available_from_working_s
    if (
        (status.state != "available" and queue_opening is None)
        or status.offered_power_w != bay.bay.power_w
        or transfer["end_working_s"] - approach_arrival >= MOVING_CHARGE_STOP_LIMIT_S
        or status.open_until_working_s < context.working_s + MOVING_CHARGE_STOP_LIMIT_S
    ):
        raise Rejected("moving_charge_execution_outside_domain")
    if approved is None:
        expires = min(
            clock.utc + timedelta(seconds=3),
            datetime.fromtimestamp(job["validity"]["real_life_expires"], timezone.utc),
        )
        end_working_s = min(
            context.working_s + 3,
            job["validity"]["working_expires"],
            approach_arrival,
        )
        if end_working_s <= context.working_s:
            raise Rejected("moving_charge_stale_progress")
        handoff_floor = approach_handoff_floor(
            chosen, current["assignment"]["vehicle"]["reserve_soc"] + stations.policy.margin_soc
        )
        if handoff_floor >= transfer["departure_soc"]:
            raise Rejected("moving_charge_battery_outside_domain")
        approved = ReviewedStop(
            fence,
            station_hash,
            candidate_id,
            context.scope,
            context.manifest_sha256,
            context.plan.revision,
            bay.bay.bay_id,
            bay.external_index,
            stations.scenario.scenario_id,
            status.revision,
            transfer["departure_soc"],
            context.working_s,
            end_working_s,
            ClockLease.until(clock, expires),
            detour_sha256,
            handoff_floor,
            queue_opens_working_s=queue_opening,
        )
    elif approved.detour_sha256 != detour_sha256 or approved.queue_opens_working_s != queue_opening:
        raise Rejected("moving_charge_obsolete_plan")
    try:
        soc = _signal(packet["payload"], "battery.pack.soc", "1", 0, 1)
    except InputUnavailable as exc:
        raise Rejected("moving_charge_stale_progress") from exc
    revalidate(
        approved,
        context,
        status,
        clock=clock,
        working_s=context.working_s if working_s is None else working_s,
        assignment_sha256=station_hash,
        job_fence=fence,
        candidate_sha256=candidate_id,
        source_state=current["clock"]["state"],
        reserve_soc=current["assignment"]["vehicle"]["reserve_soc"],
        soc=soc,
    )
    return approved


@router.post("/v2/moving-charge/approve", dependencies=[Depends(operator)])
def approve(raw: Body):
    if os.environ.get("EVDT_MOVING_CHARGE_AUTOMATIC") == "1":
        raise Rejected("moving_charge_automatic_profile_exclusive")
    value, scope, identity = validated(raw, Approval)
    with command_journal.connection() as conn:
        state = moving_charge_journal.request(
            conn,
            command_id=identity,
            scope=scope,
            job_fence=value.job_fence,
            candidate_sha256=value.candidate_sha256,
            review=lambda: review(conn, scope, value.job_fence, value.candidate_sha256),
        )
    return {"command_id": identity, "state": state}


@router.post("/v2/moving-charge/veto", dependencies=[Depends(operator)])
def veto(raw: Body):
    _, scope, identity = validated(raw, Command)
    with command_journal.connection() as conn:
        state = moving_charge_journal.veto(conn, scope, identity)
    return {"command_id": identity, "state": state}


@router.post("/internal/v2/moving-charge/grant", dependencies=[Depends(executor)])
def grant(raw: Body):
    value, scope, identity = validated(raw, ExecutorClaim)
    with command_journal.connection() as conn:

        def verify(approval):
            if os.environ.get("EVDT_MOVING_CHARGE_AUTOMATIC") == "1":
                from evdt.moving_charge_automatic import require_grant

                require_grant(conn, scope, identity)
            return review(
                conn,
                scope,
                approval.job_fence,
                approval.candidate_sha256,
                approved=approval,
                working_s=value.sim_s,
            )

        offer = moving_charge_journal.grant(
            conn,
            scope,
            identity,
            verify,
        )
    return offer


@router.post("/internal/v2/moving-charge/ack", dependencies=[Depends(executor)])
def acknowledge(raw: Body):
    value, scope, identity = validated(raw, Acknowledgment)
    with command_journal.connection() as conn:
        state = moving_charge_journal.acknowledge(conn, scope, identity, value.status)
    return {"command_id": identity, "state": state}


@router.get("/internal/v2/moving-charge/pending", dependencies=[Depends(executor)])
def pending():
    target = command_journal.scope()
    with command_journal.connection() as conn:
        if os.environ.get("EVDT_MOVING_CHARGE_AUTOMATIC") == "1":
            from evdt.moving_charge_automatic import overridden

            if overridden(conn, (target.run_id, target.asset_id)):
                return {"command_id": None}
        row = conn.execute(
            """SELECT command_id FROM commanding.moving_charge_intents i
            WHERE i.run_id=%s AND i.asset_id=%s
            AND i.expires_at > clock_timestamp()
            AND NOT EXISTS (
                SELECT 1 FROM commanding.moving_charge_handoffs f WHERE f.command_id=i.command_id
            )
            ORDER BY i.recorded_at DESC LIMIT 1""",
            (target.run_id, target.asset_id),
        ).fetchone()
    return {"command_id": str(row["command_id"]) if row else None}


@router.get("/v2/moving-charge/status", dependencies=[Depends(operator)])
def status():
    target = command_journal.scope()
    with command_journal.connection() as conn:
        row = conn.execute(
            """SELECT i.command_id,f.decision,a.status,i.expires_at > clock_timestamp() AS fresh
            FROM commanding.moving_charge_intents i
            LEFT JOIN commanding.moving_charge_handoffs f USING(command_id)
            LEFT JOIN commanding.moving_charge_acknowledgments a USING(command_id)
            WHERE i.run_id=%s AND i.asset_id=%s
            ORDER BY i.recorded_at DESC LIMIT 1""",
            (target.run_id, target.asset_id),
        ).fetchone()
        automatic = os.environ.get("EVDT_MOVING_CHARGE_AUTOMATIC") == "1"
        if automatic:
            from evdt.moving_charge_automatic import overridden

            blocked = overridden(conn, (target.run_id, target.asset_id))
    return {
        "command_id": str(row["command_id"]) if row else None,
        "state": (row["status"] or row["decision"] or ("approved" if row["fresh"] else "expired"))
        if row
        else "idle",
        **({"override_latched": blocked} if automatic else {}),
    }
