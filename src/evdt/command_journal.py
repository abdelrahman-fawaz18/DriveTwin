"""Append-only PostgreSQL records for the optional command service."""

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import BoundedSemaphore

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from evdt import command_authority as authority
from evdt import mission_intent
from evdt.command_effect import APPLY_AT_S, compare_speed
from evdt.command_timing import ClockLease, HostClock, host_clock
from evdt.commands import OperatorScope, check_validity, read_command
from evdt.config import ROOT, db_options, run_id
from evdt.contract_profile import Rejected

SLOT = BoundedSemaphore(1)
MIGRATIONS = (
    "001_journal.sql",
    "002_speed_evidence.sql",
    "003_timing_audit.sql",
    "004_authority.sql",
    "005_pending_changes.sql",
    "006_mission_intents.sql",
)
MOVING_CHARGE_MIGRATIONS = (
    "009_moving_charge_handoff.sql",
    "010_moving_charge_recovery.sql",
    "011_moving_charge_recovery_review.sql",
    "012_moving_charge_automatic.sql",
)
LOCK = "SELECT pg_advisory_xact_lock(37110404)"
TERMINAL_REASONS = (
    "simulation_deadline",
    "elapsed_deadline",
    "wall_deadline",
    "clock_epoch_changed",
    "missing_clock_lease",
)
EXECUTOR_REFUSALS = {
    "elapsed_deadline",
    "clock_epoch_changed",
    "clock_skew",
    "invalid_clock_lease",
    "wall_deadline",
    "wall_validity",
    "simulation_validity",
    "not_a_speed_reduction",
    "authority_fence_refused",
}


def scope() -> OperatorScope:
    return OperatorScope(run_id(), os.environ["COMMAND_ASSET_ID"], os.environ["COMMAND_ISSUER"])


def initialize() -> None:
    """Provision a command target only in an existing registered simulation run."""
    target = scope()
    password = Path(os.environ["COMMAND_DB_PASSWORD_FILE"]).read_text().strip()
    if not password:
        raise ValueError("Empty command database password")
    with psycopg.connect(**(db_options() | {"user": "evdt_admin"}), row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(37110403)")
        row = conn.execute(
            "SELECT policy FROM public.m_run_definitions WHERE run_id=%s", (target.run_id,)
        ).fetchone()
        if (
            row is None
            or row["policy"]["mode"] != "simulation"
            or not any(s["asset_id"] == target.asset_id for s in row["policy"]["sources"])
        ):
            raise ValueError("Command target must be registered in this simulation run")
        policy = row["policy"]
        role_exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname='evdt_command'").fetchone()
        verb = sql.SQL("ALTER") if role_exists else sql.SQL("CREATE")
        conn.execute(
            verb
            + sql.SQL(" ROLE evdt_command LOGIN CONNECTION LIMIT 1 PASSWORD {} ").format(
                sql.Literal(password)
            )
        )
        conn.execute("CREATE SCHEMA IF NOT EXISTS commanding")
        conn.execute("CREATE TABLE IF NOT EXISTS commanding.migrations(sha256 text PRIMARY KEY)")
        migrations = [(ROOT / "migrations/commands" / name).read_bytes() for name in MIGRATIONS]
        digests = [hashlib.sha256(raw).hexdigest() for raw in migrations]
        applied = {
            row["sha256"] for row in conn.execute("SELECT sha256 FROM commanding.migrations")
        }
        if applied != set(digests[: len(applied)]):
            raise RuntimeError("Applied command migration changed or is out of order")
        for migration, digest in zip(
            migrations[len(applied) :], digests[len(applied) :], strict=True
        ):
            conn.execute(migration.decode())
            conn.execute("INSERT INTO commanding.migrations VALUES (%s)", (digest,))
        if os.environ.get("EVDT_MOVING_CHARGE_COMMANDS") == "1":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS commanding.moving_charging_migration "
                "(sha256 text PRIMARY KEY)"
            )
            saved = {
                row["sha256"]
                for row in conn.execute("SELECT sha256 FROM commanding.moving_charging_migration")
            }
            moving = [
                (ROOT / "migrations/commands" / name).read_bytes()
                for name in MOVING_CHARGE_MIGRATIONS
            ]
            marks = [hashlib.sha256(raw).hexdigest() for raw in moving]
            if saved != set(marks[: len(saved)]):
                raise RuntimeError("Applied moving charging command migration changed")
            for raw, mark in zip(moving[len(saved) :], marks[len(saved) :], strict=True):
                conn.execute(raw.decode())
                conn.execute(
                    "INSERT INTO commanding.moving_charging_migration VALUES (%s)", (mark,)
                )
        conn.execute(
            "INSERT INTO commanding.targets VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (target.run_id, target.asset_id, target.issuer),
        )
        row = conn.execute(
            "SELECT issuer FROM commanding.targets WHERE run_id=%s AND asset_id=%s",
            (target.run_id, target.asset_id),
        ).fetchone()
        if row["issuer"] != target.issuer:
            raise ValueError("Command target assignment is immutable within a run")
        station_file = os.environ.get("COMMAND_STATIONS_FILE")
        if station_file:
            mission_intent.provision(conn, target, policy, Path(station_file))
        if os.environ.get("COMMAND_ARBITRATION") == "1":
            if target.issuer != "dispatch-coordinator":
                raise ValueError("Arbitration requires a separate dispatch coordinator identity")
            conn.execute(LOCK)
            if not authority.enabled(conn, target):
                if fetch(conn, target) is not None:
                    raise ValueError("Use a fresh run when enabling arbitration")
                conn.execute(
                    "INSERT INTO commanding.arbitration_targets VALUES (%s,%s)",
                    (target.run_id, target.asset_id),
                )


@contextmanager
def connection():
    if os.environ["DB_USER"] != "evdt_command":
        raise RuntimeError("Command service requires its dedicated database role")
    if not SLOT.acquire(timeout=5):
        raise TimeoutError("Command database admission deadline exceeded")
    try:
        with psycopg.connect(
            **(db_options() | {"user": "evdt_command"}),
            row_factory=dict_row,
            options="-c statement_timeout=3000 -c lock_timeout=2500",
        ) as conn:
            yield conn
    finally:
        SLOT.release()


def fetch(conn, target, command_id=None):
    return conn.execute(
        """SELECT r.command_id,r.wire,r.accepted_at,r.expires_at,a.sim_time_s,a.reserved_at,
                  x.payload,x.recorded_at,e.assessment,e.evidence,
                  d.boot_id,d.anchored_utc,d.anchor_ns,d.deadline_ns,c.recorded_at AS changed_at,
                  c.replacement_id,
                  EXISTS (SELECT 1 FROM commanding.rejections j WHERE j.command_id=r.command_id
                          AND j.operation='claim' AND j.reason=ANY(%s)) AS closed,
                  (SELECT reason FROM commanding.rejections j WHERE j.command_id=r.command_id
                   AND j.operation='execute' ORDER BY j.event_id LIMIT 1) AS refusal_reason
           FROM commanding.requests r
           LEFT JOIN commanding.attempts a USING (command_id)
           LEFT JOIN commanding.results x USING (command_id)
           LEFT JOIN commanding.effects e USING (command_id)
           LEFT JOIN commanding.deadlines d USING (command_id)
           LEFT JOIN commanding.pending_changes c USING (command_id)
           WHERE (r.run_id,r.asset_id,r.issuer)=(%s,%s,%s)
             AND (r.command_id=%s::uuid OR (%s::uuid IS NULL AND c.replacement_id IS NULL))""",
        (
            list(TERMINAL_REASONS),
            target.run_id,
            target.asset_id,
            target.issuer,
            command_id,
            command_id,
        ),
    ).fetchone()


def expired(row):
    current = host_clock()
    if row["closed"]:
        return True
    if row["boot_id"] is None:
        return current.utc >= row["expires_at"]
    return str(row["boot_id"]) != current.boot_id or current.elapsed_ns >= row["deadline_ns"]


def view(row):
    if row["payload"] is not None:
        state = row["payload"]["status"]
    elif row["refusal_reason"] is not None:
        state = "rejected_before_actuation"
    elif row["reserved_at"] is not None:
        state = "unknown_outcome"
    elif row["changed_at"] is not None:
        state = "superseded" if row["replacement_id"] is not None else "canceled"
    elif expired(row):
        state = "expired"
    else:
        state = "queued"
    return {
        "command_id": row["command_id"],
        "state": state,
        "accepted_at": row["accepted_at"],
        "attempt_sim_s": row["sim_time_s"],
        "reserved_at": row["reserved_at"],
        "result": row["payload"],
        "recorded_at": row["recorded_at"],
        "effect": row["assessment"],
        "refusal_reason": row["refusal_reason"],
        **(
            {"changed_at": row["changed_at"], "replacement_command_id": row["replacement_id"]}
            if row["changed_at"] is not None
            else {}
        ),
    }


def record_request(conn, target, command, raw, lease):
    identity = command["command_id"]
    inserted = conn.execute(
        """INSERT INTO commanding.requests
           (command_id,run_id,asset_id,issuer,wire,expires_at) VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT DO NOTHING RETURNING command_id""",
        (identity, target.run_id, target.asset_id, target.issuer, raw, command["expires_at"]),
    ).fetchone()
    if inserted is not None:
        conn.execute(
            "INSERT INTO commanding.deadlines VALUES (%s,%s,%s,%s,%s)",
            (
                identity,
                lease.anchor.boot_id,
                lease.anchor.utc,
                lease.anchor.elapsed_ns,
                lease.deadline_ns,
            ),
        )
    return inserted is not None


def submit(raw):
    target = scope()
    with connection() as conn:
        conn.execute(LOCK)
        if authority.enabled(conn, target):
            raise Rejected("authority_required")
        command = read_command(raw, target)
        identity = command["command_id"]
        previous = fetch(conn, target, identity)
        if previous is None and fetch(conn, target) is None:
            # Acceptance can queue a future simulation window. The executor owns its clock.
            current = host_clock()
            check_validity(command, current.utc, None)
            lease = ClockLease.until(current, datetime.fromisoformat(command["expires_at"]))
            if record_request(conn, target, command, raw, lease):
                return True, view(fetch(conn, target, identity))
            previous = fetch(conn, target, identity)
        if previous is None or bytes(previous["wire"]) != raw:
            raise Rejected("command_conflict")
        return False, view(previous)


def submit_mission(raw):
    with connection() as conn:
        return mission_intent.submit(conn, scope(), raw, host_clock().utc)


def change_pending(identity, replacement=None):
    """Close a pending request and optionally insert its successor under the handoff lock."""
    target = scope()
    with connection() as conn:
        conn.execute(LOCK)
        row = fetch(conn, target, identity)
        if row is None:
            raise Rejected("command_not_found")
        if replacement is not None and authority.enabled(conn, target):
            raise Rejected("authority_closed")
        command = read_command(replacement, target) if replacement is not None else None
        if row["changed_at"] is not None:
            saved = row["replacement_id"]
            if command is None and saved is None:
                return view(row)
            if command is not None and saved is not None and str(saved) == command["command_id"]:
                if bytes(fetch(conn, target, saved)["wire"]) == replacement:
                    return view(row)
            raise Rejected("command_conflict")
        if row["reserved_at"] is not None or expired(row):
            raise Rejected("command_not_pending")
        successor = None
        if command is not None:
            if row["boot_id"] is None:
                raise Rejected("command_not_pending")
            current = host_clock()
            try:
                ClockLease(
                    HostClock(str(row["boot_id"]), row["anchor_ns"], row["anchored_utc"]),
                    row["deadline_ns"],
                ).verify(current)
            except Rejected as error:
                raise Rejected("command_not_pending") from error
            check_validity(command, current.utc, None)
            lease = ClockLease.until(current, datetime.fromisoformat(command["expires_at"]))
            if not record_request(conn, target, command, replacement, lease):
                raise Rejected("command_conflict")
            successor = command["command_id"]
        conn.execute(
            "INSERT INTO commanding.pending_changes(command_id,replacement_id) VALUES (%s,%s)",
            (identity, successor),
        )
        return view(fetch(conn, target, identity))


def submit_proposal(proposer, raw):
    target = scope()
    command = read_command(raw, authority.proposal_scope(target, proposer))
    with connection() as conn:
        conn.execute(LOCK)
        if not authority.enabled(conn, target):
            raise Rejected("authority_disabled")
        key = (target.run_id, target.asset_id, proposer)
        previous = conn.execute(
            "SELECT wire FROM commanding.proposals WHERE (run_id,asset_id,proposer)=(%s,%s,%s)", key
        ).fetchone()
        if previous is not None:
            if bytes(previous["wire"]) != raw:
                raise Rejected("proposal_conflict")
            return False, command["command_id"]
        if authority.decision(conn, target) is not None:
            raise Rejected("authority_closed")
        current = host_clock()
        check_validity(command, current.utc, APPLY_AT_S)
        lease = ClockLease.until(current, datetime.fromisoformat(command["expires_at"]))
        inserted = conn.execute(
            """INSERT INTO commanding.proposals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING RETURNING command_id""",
            (
                *key,
                command["command_id"],
                raw,
                current.boot_id,
                current.utc,
                current.elapsed_ns,
                lease.deadline_ns,
            ),
        ).fetchone()
        if inserted is None:
            raise Rejected("proposal_conflict")
        return True, command["command_id"]


def seal_decision():
    target = scope()
    with connection() as conn:
        conn.execute(LOCK)
        if not authority.enabled(conn, target):
            raise Rejected("authority_disabled")
        saved = authority.decision(conn, target)
        if saved is not None:
            return False, saved
        proposals = conn.execute(
            "SELECT * FROM commanding.proposals WHERE (run_id,asset_id)=(%s,%s)",
            (target.run_id, target.asset_id),
        ).fetchall()
        chosen, assessments = authority.rank(target, proposals, host_clock())
        if chosen is None:
            raise Rejected("no_eligible_proposal")
        command, lease, proposer = chosen
        # Dispatch under the coordinator's own identity. The decision preserves the original proposer.
        command["issuer"] = target.issuer
        raw = json.dumps(
            command, allow_nan=False, ensure_ascii=False, separators=(",", ":")
        ).encode()
        if not record_request(conn, target, command, raw, lease):
            raise Rejected("command_conflict")
        conn.execute(
            """INSERT INTO commanding.decisions(run_id,asset_id,command_id,selected_proposer,assessments)
               VALUES (%s,%s,%s,%s,%s)""",
            (target.run_id, target.asset_id, command["command_id"], proposer, Jsonb(assessments)),
        )
        return True, authority.decision(conn, target)


def audit(conn, target, operation, reason, identity=None):
    conn.execute(
        """INSERT INTO commanding.rejections(run_id,asset_id,command_id,operation,reason)
           VALUES (%s,%s,%s,%s,%s)""",
        (target.run_id, target.asset_id, identity, operation, reason),
    )


def audit_request(operation, reason):
    with connection() as conn:
        audit(conn, scope(), operation, reason)


def claim(sim_time_s, state, fence_protocol=None):
    target = scope()
    with connection() as conn:
        conn.execute(LOCK)
        row = fetch(conn, target)
        if row is None:
            return None
        identity = row["command_id"]
        decision = None
        if authority.enabled(conn, target):
            decision = authority.decision(conn, target)
            if (
                fence_protocol != authority.FENCE_PROTOCOL
                or decision is None
                or decision["command_id"] != identity
            ):
                audit(conn, target, "claim", "authority_protocol_required", identity)
                return None
        if (
            row["reserved_at"] is not None
            or row["closed"]
            or row["changed_at"]
            or state == "paused"
        ):
            reason = (
                "already_reserved"
                if row["reserved_at"]
                else "already_closed"
                if row["closed"] or row["changed_at"]
                else "simulation_paused"
            )
            audit(conn, target, "claim", reason, identity)
            return None
        raw = bytes(row["wire"])
        command = read_command(raw, target)
        try:
            if row["boot_id"] is None:
                raise Rejected("missing_clock_lease")
            lease = ClockLease(
                HostClock(str(row["boot_id"]), row["anchor_ns"], row["anchored_utc"]),
                row["deadline_ns"],
            )
            current = host_clock()
            lease.verify(current)
            if current.utc >= row["expires_at"]:
                raise Rejected("wall_deadline")
            if sim_time_s >= command["expires_sim_s"]:
                raise Rejected("simulation_deadline")
            check_validity(command, current.utc, sim_time_s)
        except Rejected as error:
            audit(conn, target, "claim", str(error), identity)
            return None
        reserved = conn.execute(
            """INSERT INTO commanding.attempts(command_id,sim_time_s) VALUES (%s,%s)
               ON CONFLICT DO NOTHING RETURNING command_id""",
            (row["command_id"], sim_time_s),
        ).fetchone()
        if reserved is None:
            audit(conn, target, "claim", "already_reserved", identity)
            return None
        return raw, lease, decision["generation"] if decision is not None else None


def verify_fence(command_id, generation):
    target = scope()
    with connection() as conn:
        conn.execute(LOCK)
        decision = authority.decision(conn, target)
        if decision is None or (decision["command_id"], decision["generation"]) != (
            command_id,
            generation,
        ):
            raise Rejected("authority_fence_refused")
        row = fetch(conn, target, command_id)
        if (
            row is None
            or row["reserved_at"] is None
            or row["refusal_reason"] is not None
            or row["payload"] is not None
        ):
            raise Rejected("authority_fence_refused")


def save_refusal(identity, reason):
    if reason not in EXECUTOR_REFUSALS:
        raise Rejected("executor_refusal_schema")
    target = scope()
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(37110404)")
        row = fetch(conn, target, identity)
        if (
            row is None
            or row["reserved_at"] is None
            or row["payload"] is not None
            or row["assessment"] is not None
        ):
            raise Rejected("attempt_not_found")
        if row["refusal_reason"] is None:
            audit(conn, target, "execute", reason, row["command_id"])
        elif row["refusal_reason"] != reason:
            raise Rejected("refusal_conflict")


def save_result(payload):
    target = scope()
    if (payload["run_id"], payload["asset_id"]) != (target.run_id, target.asset_id):
        raise Rejected("wrong_target")
    identity = payload["command_id"]
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(37110404)")
        row = fetch(conn, target, identity)
        if row is None or row["sim_time_s"] != payload["attempt_sim_s"]:
            raise Rejected("attempt_not_found")
        if row["refusal_reason"] is not None:
            raise Rejected("attempt_refused")
        conn.execute(
            "INSERT INTO commanding.results(command_id,payload) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (identity, Jsonb(payload)),
        )
        saved = fetch(conn, target, identity)
        if saved["payload"] != payload:
            raise Rejected("result_conflict")
        return view(saved)


def save_effect(evidence):
    target = scope()
    if (evidence["run_id"], evidence["asset_id"]) != (target.run_id, target.asset_id):
        raise Rejected("wrong_target")
    identity = evidence["command_id"]
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(37110404)")
        row = fetch(conn, target, identity)
        if row is None or row["sim_time_s"] != APPLY_AT_S:
            raise Rejected("attempt_not_found")
        if row["refusal_reason"] is not None:
            raise Rejected("attempt_refused")
        command = read_command(bytes(row["wire"]), target)
        result = compare_speed(
            evidence["baseline"], evidence["commanded"], command["parameters"]["value"]
        )
        conn.execute(
            """INSERT INTO commanding.effects(command_id,evidence,assessment) VALUES (%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (identity, Jsonb(evidence), Jsonb(result)),
        )
        saved = fetch(conn, target, identity)
        if saved["evidence"] != evidence:
            raise Rejected("effect_conflict")
        return view(saved)


if __name__ == "__main__":
    initialize()
