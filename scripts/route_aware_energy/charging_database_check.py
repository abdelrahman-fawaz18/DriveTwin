"""Verify append-only moving charging approval against an owned PostgreSQL fixture."""

import os
import secrets
import time
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from evdt import (
    command_journal,
    moving_charge_automatic,
    moving_charge_journal,
    moving_charge_recovery_journal,
)
from evdt.command_timing import ClockLease, host_clock
from evdt.contract_profile import Rejected
from evdt.energy.moving_charge_handoff import ReviewedStop
from evdt.energy.moving_charge_recovery import HeldChargeStop
from evdt.energy.route_aware.wire import fingerprint
from evdt.monitoring.held_charge_observation import read_current_hold, register_held


def exercise(session, source, forecast_fence, assignment_sha256):
    """Cutoff and station payloads are declared for this authority-only check."""
    role_file = session.runtime / "command_db"
    with role_file.open("x") as handle:
        handle.write(secrets.token_urlsafe(36) + "\n")
    os.environ.update(
        COMMAND_ASSET_ID=source.asset_id,
        COMMAND_ISSUER="operator",
        COMMAND_DB_PASSWORD_FILE=str(role_file),
        EVDT_MOVING_CHARGE_COMMANDS="1",
    )
    command_journal.initialize()
    command_journal.initialize()
    candidate = "8" * 64
    job = forecast_fence + 10_000
    with session.connect() as conn:
        conn.execute(
            """INSERT INTO m_moving_charge_jobs
            (fence,forecast_fence,owner,assignment_sha256,request_sha256,status,
             completed_at,payload,validity,station_rechecked_until)
            VALUES(%s,%s,%s,%s,%s,'current',clock_timestamp(),%s,%s,
                   EXTRACT(EPOCH FROM clock_timestamp()) + 8)""",
            (
                job,
                forecast_fence,
                uuid4(),
                assignment_sha256,
                "b" * 64,
                Jsonb(
                    {
                        "version": "moving-charge-worker-result/1.0.0",
                        "input_sha256": "b" * 64,
                        "decision": {"preferred": {"candidate_sha256": candidate}},
                    }
                ),
                Jsonb({}),
            ),
        )
    with session.connect("forecast") as conn:
        renewed = conn.execute(
            """UPDATE m_moving_charge_jobs SET station_rechecked_until=station_rechecked_until+4
            WHERE fence=%s RETURNING fence""",
            (job,),
        ).fetchone()
        assert renewed["fence"] == job
        for statement in (
            """UPDATE m_moving_charge_jobs SET station_rechecked_until=station_rechecked_until-1
            WHERE fence=%s""",
            """UPDATE m_moving_charge_jobs SET station_rechecked_until=station_rechecked_until+1,
               payload=payload || '{"different_outcome":true}'::jsonb WHERE fence=%s""",
        ):
            try:
                with conn.transaction():
                    conn.execute(statement, (job,))
            except RaiseException:
                pass
            else:
                raise RuntimeError("Station review changed the completed charging record")
    clock = host_clock()
    approval = ReviewedStop(
        job,
        assignment_sha256,
        candidate,
        source,
        "a" * 64,
        "c" * 64,
        "bay",
        1,
        "fixture",
        1,
        0.15,
        0,
        4,
        ClockLease.until(clock, clock.utc + timedelta(seconds=25)),
    )
    scope = (source.run_id, source.asset_id)
    original, selected = uuid4(), uuid4()
    held = held_readback(approval, selected)
    register_fixture_hold(session, held)
    with session.connect("forecast") as conn:
        observed, paused = read_current_hold(conn, source.run_id, source.asset_id, source.source_id)
        assert observed == held
        assert paused.state == "paused" and paused.working_s == held.working_s
        job = conn.execute(
            """INSERT INTO m_moving_charge_recovery_jobs
               (fence,owner,run_id,asset_id,source_id,source_epoch,session_id,
                observation_sha256,request_sha256,assignment_sha256,status)
               VALUES(nextval('m_route_forecast_fence'),%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')
               RETURNING fence""",
            (
                uuid4(),
                source.run_id,
                source.asset_id,
                source.source_id,
                source.source_epoch,
                source.session_id,
                fingerprint(held),
                "d" * 64,
                assignment_sha256,
            ),
        ).fetchone()
        conn.execute(
            """UPDATE m_moving_charge_recovery_jobs SET status='current',
               completed_at=clock_timestamp(),payload=%s WHERE fence=%s""",
            (Jsonb({"input_sha256": "d" * 64}), job["fence"]),
        )
    previous = {name: os.environ[name] for name in ("DB_USER", "DB_PASSWORD_FILE")}
    os.environ.update(DB_USER="evdt_command", DB_PASSWORD_FILE=str(role_file))
    try:
        verify_automatic(scope, approval)
        return verify_handoff(session, scope, original, selected, approval, held)
    finally:
        os.environ.update(previous)


def verify_handoff(session, scope, original, selected, approval, held):
    def request(identity):
        with command_journal.connection() as conn:
            return moving_charge_journal.request(
                conn,
                command_id=identity,
                scope=scope,
                job_fence=approval.job_fence,
                candidate_sha256=approval.candidate_sha256,
                review=lambda: approval,
            )

    if request(original) != "approved" or request(original) != "approved":
        raise RuntimeError("Approval did not replay idempotently")
    with command_journal.connection() as conn:
        assert moving_charge_journal.veto(conn, scope, original) == "vetoed"
    if request(original) != "vetoed" or request(selected) != "approved":
        raise RuntimeError("Veto or replacement outcome differs from the journal")
    with command_journal.connection() as conn:
        offer = moving_charge_journal.grant(
            conn, scope, selected, lambda value: value.lease.verify(host_clock())
        )
    if offer != moving_charge_journal.encode(approval):
        raise RuntimeError("Grant changed the reviewed offer")
    for command in (original, selected):
        try:
            with command_journal.connection() as conn:
                moving_charge_journal.grant(conn, scope, command, lambda _: None)
        except Rejected as exc:
            if str(exc) != "moving_charge_handoff_already_closed":
                raise
        else:
            raise RuntimeError("Closed approval produced a grant")
    with command_journal.connection() as conn:
        with conn.transaction(force_rollback=True):
            verify_uncertain(conn, scope, selected, held)
    with command_journal.connection() as conn:
        assert moving_charge_journal.acknowledge(conn, scope, selected, "applied") == "applied"
    verify_replacement(session, scope, selected, approval, held)
    return True


def held_readback(approval, identity):
    return HeldChargeStop(
        approval.scope,
        str(identity),
        approval.manifest_sha256,
        approval.assignment_sha256,
        "e" * 64,
        "f" * 64,
        approval.bay_id,
        approval.bay_external_index,
        "bay_lane",
        36,
        150,
        0.12,
        time.time(),
    )


def register_fixture_hold(session, held):
    """Seed the source clock independently of the command-role exercise."""
    scope = held.scope
    key = (scope.run_id, scope.asset_id, scope.source_id)
    with session.connect() as admin:
        admin.execute(
            """INSERT INTO m_registrations
               (run_id,asset_id,source_id,source_epoch,session_id,manifest_sha256,
                minimum_sim_time_s,raw_manifest,manifest)
               VALUES(%s,%s,%s,%s,%s,%s,0,%s,%s)""",
            (
                *key,
                scope.source_epoch,
                scope.session_id,
                held.manifest_sha256,
                b"{}",
                Jsonb({"heartbeat_deadline_s": 5}),
            ),
        )
        admin.execute("INSERT INTO m_source_heads VALUES(%s,%s,%s,%s)", (*key, scope.source_epoch))
        admin.execute(
            """INSERT INTO m_source_clocks VALUES
               (%s,%s,%s,%s,1,'paused',%s,clock_timestamp(),clock_timestamp(),clock_timestamp())""",
            (*key, scope.source_epoch, held.working_s),
        )
    original = {name: os.environ[name] for name in ("DB_USER", "DB_PASSWORD_FILE")}
    try:
        os.environ.update(
            DB_USER="evdt_registry", DB_PASSWORD_FILE=str(session.credentials["registry"])
        )
        assert register_held(held)["duplicate"] is False
        assert register_held(held)["duplicate"] is True
        rejects(
            lambda: register_held(replace(held, soc=0.11)),
            "held_charge_observation_conflict",
        )
    finally:
        os.environ.update(original)


def rejects(action, reason):
    try:
        action()
    except Rejected as exc:
        if str(exc) == reason:
            return
        raise
    raise RuntimeError("Expected refusal: " + reason)


def verify_uncertain(conn, scope, selected, held):
    for _ in range(2):
        assert moving_charge_journal.acknowledge(conn, scope, selected, "unknown") == "unknown"
    rejects(
        lambda: moving_charge_journal.acknowledge(conn, scope, selected, "applied"),
        "moving_charge_acknowledgment_conflict",
    )
    rejects(
        lambda: moving_charge_recovery_journal.record_interruption(conn, selected, held),
        "moving_charge_interruption_unavailable",
    )


def verify_automatic(scope, approval):
    """Isolate authority scenarios from the operator trial on the same fixture."""
    for can_grant in (False, True):
        with command_journal.connection() as conn, conn.transaction(force_rollback=True):
            assert moving_charge_automatic.consider(conn, scope, lambda: approval) == (
                "waiting_for_confirmation"
            )
            assert moving_charge_automatic.consider(conn, scope, lambda: approval) == (
                "waiting_for_confirmation"
            )
            changed = replace(approval, candidate_sha256="7" * 64, working_not_before=1)
            assert moving_charge_automatic.consider(conn, scope, lambda: changed) == (
                "waiting_for_confirmation"
            )
            next_reading = replace(approval, working_not_before=1)
            assert moving_charge_automatic.consider(conn, scope, lambda: next_reading) == "approved"
            row = conn.execute(
                """SELECT command_id FROM commanding.moving_charge_auto_approvals
                   WHERE (run_id,asset_id)=(%s,%s)""",
                scope,
            ).fetchone()
            identity = row["command_id"]

            def grant():
                return moving_charge_journal.grant(
                    conn,
                    scope,
                    identity,
                    lambda _: moving_charge_automatic.require_grant(conn, scope, identity),
                )

            if can_grant:
                assert grant() == moving_charge_journal.encode(next_reading)
                assert moving_charge_automatic.apply_override(conn, scope) == "grant_already_issued"
                rejects(grant, "moving_charge_handoff_already_closed")
            else:
                assert moving_charge_automatic.apply_override(conn, scope) == "override_latched"
                rejects(grant, "moving_charge_auto_override")
            assert (
                moving_charge_automatic.consider(conn, scope, lambda: next_reading)
                == "override_latched"
            )
    with command_journal.connection() as conn:
        assert not moving_charge_automatic.overridden(conn, scope)


def verify_replacement(session, scope, previous_id, approval, held):
    def observe(current):
        assert current == held

    with command_journal.connection() as conn:
        rejects(
            lambda: moving_charge_recovery_journal.record_interruption(
                conn, previous_id, replace(held, soc=0.11)
            ),
            "held_charge_observation_unavailable",
        )
        link = moving_charge_recovery_journal.record_interruption(conn, previous_id, held)
        assert moving_charge_recovery_journal.record_interruption(conn, previous_id, held) == link
        rejects(
            lambda: moving_charge_recovery_journal.record_interruption(
                conn, previous_id, replace(held, soc=0.11)
            ),
            "moving_charge_interruption_conflict",
        )
    clock = host_clock()
    replacement = replace(
        approval,
        bay_id="replacement",
        bay_external_index=2,
        route_revision=held.route_revision,
        working_not_before=150,
        working_expires=151,
        lease=ClockLease.until(clock, clock.utc + timedelta(seconds=25)),
        minimum_handoff_soc=0.115,
        replacement_of=link,
    )

    def check(readback, candidate):
        observe(readback)
        assert candidate == replacement
        candidate.lease.verify(host_clock())

    first, second = uuid4(), uuid4()
    with command_journal.connection() as conn:
        rejects(
            lambda: moving_charge_recovery_journal.request(
                conn, first, scope, replace(replacement, minimum_handoff_soc=0.13), check
            ),
            "moving_charge_replacement_unavailable",
        )
        assert (
            moving_charge_recovery_journal.request(conn, first, scope, replacement, check)
            == "approved"
        )
        assert moving_charge_recovery_journal.decide(conn, scope, first, veto=True) == "vetoed"
        rejects(
            lambda: moving_charge_recovery_journal.decide(
                conn, scope, first, veto=False, verify=check
            ),
            "moving_charge_replacement_closed",
        )
        assert (
            moving_charge_recovery_journal.request(conn, second, scope, replacement, check)
            == "approved"
        )
    with session.connect() as admin:
        clock_key = (held.scope.run_id, held.scope.asset_id, held.scope.source_id)
        admin.execute(
            """UPDATE m_source_clocks SET state='running'
               WHERE (run_id,asset_id,source_id)=(%s,%s,%s)""",
            clock_key,
        )
    with command_journal.connection() as conn:
        rejects(
            lambda: moving_charge_recovery_journal.decide(
                conn, scope, second, veto=False, verify=check
            ),
            "held_charge_source_unavailable",
        )
    with session.connect() as admin:
        admin.execute(
            """UPDATE m_source_clocks
               SET state='paused',observed_at=clock_timestamp(),received_at=clock_timestamp()
               WHERE (run_id,asset_id,source_id)=(%s,%s,%s)""",
            clock_key,
        )
    # Use a new connection for every subsequent attempt. Lose the first grant reply.
    with command_journal.connection() as conn:
        rejects(
            lambda: moving_charge_recovery_journal.decide(
                conn,
                ("00000000-0000-0000-0000-000000000001", scope[1]),
                second,
                veto=False,
                verify=check,
            ),
            "moving_charge_replacement_unavailable",
        )
        assert moving_charge_recovery_journal.decide(
            conn, scope, second, veto=False, verify=check
        ) == moving_charge_journal.encode(replacement)
    with command_journal.connection() as conn:
        assert (
            moving_charge_recovery_journal.acknowledge(conn, scope, second, "applied") == "applied"
        )
        assert (
            moving_charge_recovery_journal.acknowledge(conn, scope, second, "applied") == "applied"
        )
        rejects(
            lambda: moving_charge_recovery_journal.acknowledge(conn, scope, second, "unknown"),
            "moving_charge_acknowledgment_conflict",
        )
    for veto in (True, False):
        with command_journal.connection() as conn:
            rejects(
                lambda: moving_charge_recovery_journal.decide(
                    conn, scope, second, veto=veto, verify=check
                ),
                "moving_charge_replacement_closed",
            )
            rejects(
                lambda: moving_charge_recovery_journal.request(
                    conn, uuid4(), scope, replacement, check
                ),
                "moving_charge_replacement_conflict",
            )
    return second
