"""Operator-reviewed replacement of one failed on-route SUMO charger."""

from dataclasses import replace
from datetime import timedelta

from fastapi import Depends

from evdt import command_journal
from evdt import moving_charge_recovery_journal as journal
from evdt.charging_wire import Digest
from evdt.command_api import executor, operator
from evdt.command_timing import ClockLease, host_clock
from evdt.contract_profile import Rejected
from evdt.energy.moving_charge_handoff import ReviewedStop
from evdt.energy.moving_charge_recovery import RECOVERY_HOLD_REAL_LIFE_S, ReplacementOf
from evdt.energy.route_aware.wire import fingerprint
from evdt.monitoring.charging_stop import MOVING_CHARGE_STOP_LIMIT_S
from evdt.monitoring.held_charge_observation import read_current_hold
from evdt.monitoring.route_energy_views import latest_assignment
from evdt.moving_charge_api import (
    Acknowledgment,
    Approval,
    Body,
    Command,
    ExecutorClaim,
    approach_handoff_floor,
    assignment,
    router,
    validated,
)


class Interruption(Command):
    observation_sha256: Digest


def hold(conn, scope):
    observed = read_current_hold(conn, *scope, "sumo-adapter")
    if observed is None:
        raise Rejected("held_charge_source_unavailable")
    held, clock = observed
    link = ReplacementOf(held.interrupted_command_id, fingerprint(held))
    if journal.interruption(conn, scope, link) != held:
        raise Rejected("held_charge_observation_unavailable")
    return held, clock, link


def current_result(conn, scope, held, cutoff):
    configured = latest_assignment(conn, *scope)
    stations = assignment()
    if (
        configured is None
        or not configured.enabled
        or configured.source_id != held.scope.source_id
        or fingerprint(stations) != held.assignment_sha256
        or cutoff >= held.observed_real_life_s + RECOVERY_HOLD_REAL_LIFE_S
    ):
        return None
    row = conn.execute(
        """SELECT fence,status,request_sha256,payload FROM m_moving_charge_recovery_jobs
           WHERE (run_id,asset_id,source_id,source_epoch,session_id)=(%s,%s,%s,%s,%s)
           AND observation_sha256=%s AND assignment_sha256=%s""",
        (
            *scope,
            held.scope.source_id,
            held.scope.source_epoch,
            held.scope.session_id,
            fingerprint(held),
            fingerprint(configured),
        ),
    ).fetchone()
    if (
        row is None
        or row["status"] != "current"
        or row["payload"]["input_sha256"] != row["request_sha256"]
    ):
        return None
    return configured, row


def reviews(conn, scope, held, heartbeat, link, clock):
    result = current_result(conn, scope, held, clock.utc.timestamp())
    if result is None:
        return ()
    configured, row = result
    stations = assignment()
    states = {
        status.bay_id: status
        for status in stations.scenario.observe(
            run_id=scope[0],
            working_s=held.working_s,
            observed_real_life_s=heartbeat.observed_real_life_s,
            received_real_life_s=heartbeat.received_real_life_s,
            expected_sha256=stations.scenario.sha256,
        )
    }
    selected = []
    decision = row["payload"]["decision"]
    for chosen in (decision["preferred"], decision["fallback"]):
        if chosen is None:
            continue
        entry = next((bay for bay in stations.bays if bay.bay.bay_id == chosen["bay_id"]), None)
        if entry is None or entry.detour is not None or entry.external_index <= held.external_index:
            continue
        status = states[entry.bay.bay_id]
        transfer = chosen["transfer"]
        approach = chosen["approach"]["forecast"]["summary"]
        onward = chosen["onward"]["forecast"]["summary"]
        minimum = min(approach["minimum_soc"], transfer["plug_in_soc"], onward["minimum_soc"])
        planning_floor = configured.vehicle.reserve_soc + stations.policy.margin_soc
        floor = approach_handoff_floor(chosen, planning_floor)
        if (
            status.state != "available"
            or status.offered_power_w != entry.bay.power_w
            or not 0
            <= clock.utc.timestamp() - heartbeat.received_real_life_s
            <= clock.utc.timestamp() - heartbeat.observed_real_life_s
            <= 3
            or not held.working_s
            < approach["arrival_working_s"]
            < transfer["end_working_s"]
            < status.open_until_working_s
            or transfer["end_working_s"] - approach["arrival_working_s"]
            >= MOVING_CHARGE_STOP_LIMIT_S
            or status.open_until_working_s < held.working_s + MOVING_CHARGE_STOP_LIMIT_S
            or minimum < planning_floor
            or not floor <= held.soc < transfer["departure_soc"]
        ):
            continue
        expiry = timedelta(
            seconds=min(
                5, held.observed_real_life_s + RECOVERY_HOLD_REAL_LIFE_S - clock.utc.timestamp()
            )
        )
        if expiry.total_seconds() < 0.1:
            continue
        reviewed = ReviewedStop(
            row["fence"],
            held.assignment_sha256,
            chosen["candidate_sha256"],
            held.scope,
            held.manifest_sha256,
            held.route_revision,
            entry.bay.bay_id,
            entry.external_index,
            stations.scenario.scenario_id,
            status.revision,
            transfer["departure_soc"],
            held.working_s,
            min(held.working_s + 1, approach["arrival_working_s"]),
            ClockLease.until(clock, clock.utc + expiry),
            minimum_handoff_soc=floor,
            replacement_of=link,
        )
        selected.append((reviewed, minimum))
    return tuple(selected)


def selected_review(conn, scope, held, heartbeat, link, fence, digest):
    selected = next(
        (
            value
            for value, *_ in reviews(conn, scope, held, heartbeat, link, host_clock())
            if (value.job_fence, value.candidate_sha256) == (fence, digest)
        ),
        None,
    )
    if selected is None:
        raise Rejected("moving_charge_replacement_unavailable")
    return selected


@router.post("/internal/v2/moving-charge/recovery/interrupt", dependencies=[Depends(executor)])
def interrupt(raw: Body):
    value, scope, identity = validated(raw, Interruption)
    with command_journal.connection() as conn:
        observed = read_current_hold(conn, *scope, "sumo-adapter")
        if observed is None or fingerprint(observed[0]) != value.observation_sha256:
            raise Rejected("held_charge_observation_unavailable")
        link = journal.record_interruption(conn, identity, observed[0])
    return {"observation_sha256": link.evidence_sha256}


@router.post("/v2/moving-charge/recovery/approve", dependencies=[Depends(operator)])
def approve(raw: Body):
    value, scope, identity = validated(raw, Approval)
    with command_journal.connection() as conn:
        held, heartbeat, link = hold(conn, scope)
        approval = selected_review(
            conn, scope, held, heartbeat, link, value.job_fence, value.candidate_sha256
        )

        def verify(readback, candidate):
            if readback != held or candidate != approval:
                raise Rejected("moving_charge_replacement_conflict")

        state = journal.request(conn, identity, scope, approval, verify)
    return {"command_id": str(identity), "state": state}


@router.post("/v2/moving-charge/recovery/veto", dependencies=[Depends(operator)])
def veto(raw: Body):
    _, scope, identity = validated(raw, Command)
    with command_journal.connection() as conn:
        state = journal.decide(conn, scope, identity, veto=True)
    return {"command_id": str(identity), "state": state}


@router.post("/internal/v2/moving-charge/recovery/grant", dependencies=[Depends(executor)])
def grant(raw: Body):
    value, scope, identity = validated(raw, ExecutorClaim)
    with command_journal.connection() as conn:
        held, heartbeat, link = hold(conn, scope)
        if held.working_s != value.sim_s:
            raise Rejected("held_charge_source_unavailable")

        def verify(readback, approval):
            approval.lease.verify(host_clock())
            direct = selected_review(
                conn,
                scope,
                readback,
                heartbeat,
                link,
                approval.job_fence,
                approval.candidate_sha256,
            )
            if direct != replace(approval, lease=direct.lease):
                raise Rejected("moving_charge_replacement_conflict")

        return journal.decide(conn, scope, identity, veto=False, verify=verify)


@router.post("/internal/v2/moving-charge/recovery/ack", dependencies=[Depends(executor)])
def acknowledge(raw: Body):
    value, scope, identity = validated(raw, Acknowledgment)
    with command_journal.connection() as conn:
        state = journal.acknowledge(conn, scope, identity, value.status)
    return {"command_id": str(identity), "state": state}


def latest(conn, scope):
    return conn.execute(
        """SELECT r.command_id,r.interrupted_command_id,r.expires_at>clock_timestamp() AS fresh,
           d.decision,a.status FROM commanding.moving_charge_replacements r
           LEFT JOIN commanding.moving_charge_replacement_decisions d USING(command_id)
           LEFT JOIN commanding.moving_charge_replacement_acknowledgments a USING(command_id)
           WHERE (r.run_id,r.asset_id)=(%s,%s) ORDER BY r.recorded_at DESC LIMIT 1""",
        scope,
    ).fetchone()


@router.get("/internal/v2/moving-charge/recovery/pending", dependencies=[Depends(executor)])
def pending():
    target = command_journal.scope()
    with command_journal.connection() as conn:
        row = latest(conn, (target.run_id, target.asset_id))
    return {
        "command_id": str(row["command_id"])
        if row and row["fresh"] and row["decision"] is None
        else None
    }


@router.get("/v2/moving-charge/recovery/status", dependencies=[Depends(operator)])
def status():
    target = command_journal.scope()
    scope = (target.run_id, target.asset_id)
    with command_journal.connection() as conn:
        row = latest(conn, scope)
        try:
            held, heartbeat, link = hold(conn, scope)
        except Rejected:
            held = None
        if held is not None:
            clock = host_clock()
            options = reviews(conn, scope, held, heartbeat, link, clock)
            phase = (
                "choices"
                if options
                else (
                    "no_plan"
                    if current_result(conn, scope, held, clock.utc.timestamp())
                    else "waiting"
                )
            )
        else:
            options = ()
            phase = None
    return {
        "command_id": str(row["command_id"]) if row else None,
        "state": (row["status"] or row["decision"] or ("approved" if row["fresh"] else "expired"))
        if row
        else ("idle" if held is not None else "unavailable"),
        "held": {"bay": held.bay_id, "working_s": held.working_s, "soc": held.soc}
        if held
        else None,
        "phase": phase,
        "choices": [
            {
                "bay": value.bay_id,
                "digest": value.candidate_sha256,
                "fence": value.job_fence,
                "target_soc": value.target_soc,
                "minimum_soc": minimum,
            }
            for value, minimum in options
        ],
    }
