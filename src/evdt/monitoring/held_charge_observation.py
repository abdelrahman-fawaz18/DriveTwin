"""Admit one exact held-bus reading through the registered producer boundary."""

from dataclasses import asdict
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from evdt.contract_profile import Rejected
from evdt.energy.moving_charge_recovery import HeldChargeStop
from evdt.energy.route_aware.admission import TrustedClock
from evdt.energy.route_aware.wire import decode_record, fingerprint
from evdt.monitoring.registry import binding, source_lock
from evdt.monitoring.repository import connection


def held_key(held):
    scope = held.scope
    return (scope.run_id, scope.asset_id, scope.source_id, scope.source_epoch, scope.session_id)


def register_held(held):
    identity = {**asdict(held.scope), "manifest_sha256": held.manifest_sha256}
    key = held_key(held)
    content = fingerprint(held)
    with connection() as conn:
        source_lock(conn, identity)
        registration = binding(conn, identity)
        previous = conn.execute(
            """SELECT observation_sha256 FROM m_moving_charge_held_observations
               WHERE (run_id,asset_id,source_id,source_epoch,session_id)=(%s,%s,%s,%s,%s)""",
            key,
        ).fetchone()
        if previous:
            if previous["observation_sha256"] != content:
                raise Rejected("held_charge_observation_conflict")
            return {"observation_sha256": content, "duplicate": True}
        clock = current_hold(conn, held)
        if (
            clock is None
            or not -0.05
            <= datetime.now(timezone.utc).timestamp() - held.observed_real_life_s
            <= registration["manifest"]["heartbeat_deadline_s"]
        ):
            raise Rejected("held_charge_source_unavailable")
        conn.execute(
            """INSERT INTO m_moving_charge_held_observations
               (run_id,asset_id,source_id,source_epoch,session_id,observation_sha256,
                interrupted_command_id,working_s,observation)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (*key, content, held.interrupted_command_id, held.working_s, Jsonb(asdict(held))),
        )
    return {"observation_sha256": content, "duplicate": False}


def current_hold(conn, held):
    """Check the registered session is still paused at this exact working time."""
    return conn.execute(
        """SELECT c.received_at FROM m_source_clocks c
           JOIN m_source_heads h USING(run_id,asset_id,source_id,source_epoch)
           JOIN m_registrations r USING(run_id,asset_id,source_id,source_epoch)
           WHERE (c.run_id,c.asset_id,c.source_id,c.source_epoch,r.session_id)=(%s,%s,%s,%s,%s)
             AND r.manifest_sha256=%s AND c.state='paused' AND c.sim_time_s=%s
             AND c.received_at BETWEEN clock_timestamp() - interval '3 seconds'
                                   AND clock_timestamp() + interval '50 milliseconds'
             AND c.observed_at BETWEEN clock_timestamp() - interval '3 seconds'
                                   AND clock_timestamp() + interval '50 milliseconds'""",
        (*held_key(held), held.manifest_sha256, held.working_s),
    ).fetchone()


def read_current_hold(conn, run_id, asset_id, source_id):
    """Read the receipt and current clock together under the forecast role."""
    row = conn.execute(
        """SELECT h.observation,h.observation_sha256,c.observed_at,c.received_at
           FROM m_moving_charge_held_observations h
           JOIN m_source_heads s USING(run_id,asset_id,source_id,source_epoch)
           JOIN m_registrations r USING(run_id,asset_id,source_id,source_epoch,session_id)
           JOIN m_source_clocks c USING(run_id,asset_id,source_id,source_epoch)
           WHERE (h.run_id,h.asset_id,h.source_id)=(%s,%s,%s)
             AND c.state='paused' AND c.sim_time_s=h.working_s
             AND c.received_at BETWEEN clock_timestamp() - interval '3 seconds'
                                   AND clock_timestamp() + interval '50 milliseconds'
             AND c.observed_at BETWEEN clock_timestamp() - interval '3 seconds'
                                   AND clock_timestamp() + interval '50 milliseconds'
             AND r.manifest_sha256=h.observation->>'manifest_sha256'""",
        (run_id, asset_id, source_id),
    ).fetchone()
    if row is None:
        return None
    held = decode_record(HeldChargeStop, row["observation"])
    if fingerprint(held) != row["observation_sha256"]:
        raise ValueError("Stored held observation failed its integrity check")
    return held, TrustedClock(
        held.scope,
        "paused",
        held.working_s,
        row["observed_at"].timestamp(),
        row["received_at"].timestamp(),
        held.manifest_sha256,
    )


def verify_held(conn, held):
    """Use the producer's receipt and current clock, independently of the executor."""
    source_lock(conn, asdict(held.scope))
    if current_hold(conn, held) is None:
        raise Rejected("held_charge_source_unavailable")
    row = conn.execute(
        """SELECT observation_sha256 FROM m_moving_charge_held_observations
           WHERE (run_id,asset_id,source_id,source_epoch,session_id)=(%s,%s,%s,%s,%s)""",
        held_key(held),
    ).fetchone()
    if row is None or row["observation_sha256"] != fingerprint(held):
        raise Rejected("held_charge_observation_unavailable")
