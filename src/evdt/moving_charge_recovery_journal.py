"""Record a reconciled hold and issue a single replacement grant."""

from dataclasses import asdict
from datetime import timedelta
from uuid import UUID

from psycopg.types.json import Jsonb

from evdt import moving_charge_journal
from evdt.command_journal import LOCK
from evdt.contract_profile import Rejected
from evdt.energy.moving_charge_recovery import HeldChargeStop, ReplacementOf
from evdt.energy.route_aware.wire import decode_record, fingerprint
from evdt.monitoring.held_charge_observation import verify_held


def scope_of(record):
    return record.scope.run_id, record.scope.asset_id


def record_interruption(conn, command_id, held: HeldChargeStop):
    """The source observer must endorse the exact held position and battery."""
    scope = scope_of(held)
    original = moving_charge_journal._locked_intent(conn, scope, command_id)
    approval = moving_charge_journal.decode(original["payload"])
    if (
        original["decision"] != "granted"
        or held.interrupted_command_id != str(command_id)
        or approval.scope != held.scope
        or approval.manifest_sha256 != held.manifest_sha256
        or approval.assignment_sha256 != held.assignment_sha256
        or approval.bay_id != held.bay_id
        or approval.bay_external_index != held.external_index
        or held.working_s < approval.working_not_before
        or approval.detour_sha256 is not None
    ):
        raise Rejected("moving_charge_interruption_unavailable")
    acknowledged = conn.execute(
        """SELECT status FROM commanding.moving_charge_acknowledgments WHERE command_id=%s""",
        (command_id,),
    ).fetchone()
    if not acknowledged or acknowledged["status"] != "applied":
        raise Rejected("moving_charge_interruption_unavailable")
    digest = fingerprint(held)
    existing = conn.execute(
        """SELECT evidence_sha256 FROM commanding.moving_charge_interruptions
           WHERE command_id=%s AND run_id=%s AND asset_id=%s""",
        (command_id, *scope),
    ).fetchone()
    if existing is not None:
        if existing["evidence_sha256"] != digest:
            raise Rejected("moving_charge_interruption_conflict")
    else:
        verify_held(conn, held)
        conn.execute(
            """INSERT INTO commanding.moving_charge_interruptions
               (command_id,run_id,asset_id,evidence_sha256,observation)
               VALUES (%s,%s,%s,%s,%s)""",
            (command_id, *scope, digest, Jsonb(asdict(held))),
        )
    return ReplacementOf(str(command_id), digest)


def request(conn, command_id, scope, approval, verify_candidate):
    """Accept the server-reviewed replacement tied to the saved observation."""
    conn.execute(LOCK)
    link = approval.replacement_of
    if link is None or scope_of(approval) != scope or str(command_id) == link.command_id:
        raise Rejected("moving_charge_replacement_unavailable")
    existing = find(conn, scope, command_id)
    if existing is not None:
        previous = moving_charge_journal.decode(existing["payload"])
        if (
            previous.job_fence,
            previous.candidate_sha256,
            previous.replacement_of,
        ) != (approval.job_fence, approval.candidate_sha256, link):
            raise Rejected("moving_charge_replacement_conflict")
        return existing["decision"] or ("approved" if existing["fresh"] else "expired")
    held = interruption(conn, scope, link)
    verify_held(conn, held)
    verify_replacement(held, approval)
    if conn.execute(
        """SELECT 1 FROM commanding.moving_charge_replacements r
           LEFT JOIN commanding.moving_charge_replacement_decisions d USING(command_id)
           WHERE r.interrupted_command_id=%s
             AND (d.decision='granted' OR (d.decision IS NULL AND r.expires_at>clock_timestamp()))
           LIMIT 1""",
        (UUID(link.command_id),),
    ).fetchone():
        raise Rejected("moving_charge_replacement_conflict")
    verify_candidate(held, approval)
    clock = approval.lease
    expires = clock.anchor.utc + timedelta(
        microseconds=(clock.deadline_ns - clock.anchor.elapsed_ns) // 1000
    )
    if (
        conn.execute(
            """INSERT INTO commanding.moving_charge_replacements
           (command_id,interrupted_command_id,run_id,asset_id,evidence_sha256,payload,expires_at)
           SELECT %s,%s,%s,%s,%s,%s,%s WHERE %s > clock_timestamp() + interval '1 millisecond'
           ON CONFLICT DO NOTHING""",
            (
                command_id,
                UUID(link.command_id),
                *scope,
                link.evidence_sha256,
                Jsonb(moving_charge_journal.encode(approval)),
                expires,
                expires,
            ),
        ).rowcount
        != 1
    ):
        raise Rejected("moving_charge_replacement_unavailable")
    return "approved"


def find(conn, scope, command_id):
    return conn.execute(
        """SELECT r.payload,r.interrupted_command_id,d.decision,r.expires_at > clock_timestamp() AS fresh
           FROM commanding.moving_charge_replacements r
           LEFT JOIN commanding.moving_charge_replacement_decisions d USING(command_id)
           WHERE r.run_id=%s AND r.asset_id=%s AND r.command_id=%s""",
        (*scope, command_id),
    ).fetchone()


def interruption(conn, scope, link):
    row = conn.execute(
        """SELECT observation,evidence_sha256 FROM commanding.moving_charge_interruptions
           WHERE run_id=%s AND asset_id=%s AND command_id=%s""",
        (*scope, UUID(link.command_id)),
    ).fetchone()
    if row is None or row["evidence_sha256"] != link.evidence_sha256:
        raise Rejected("moving_charge_interruption_unavailable")
    held = decode_record(HeldChargeStop, row["observation"])
    if scope_of(held) != scope or fingerprint(held) != link.evidence_sha256:
        raise Rejected("moving_charge_interruption_conflict")
    return held


def verify_replacement(held, approval):
    if (
        approval.scope != held.scope
        or approval.manifest_sha256 != held.manifest_sha256
        or approval.assignment_sha256 != held.assignment_sha256
        or approval.route_revision != held.route_revision
        or approval.working_not_before != held.working_s
        or approval.bay_id == held.bay_id
        or approval.bay_external_index <= held.external_index
        or not approval.minimum_handoff_soc <= held.soc < approval.target_soc
    ):
        raise Rejected("moving_charge_replacement_unavailable")


def decide(conn, scope, command_id, *, veto, verify=None):
    conn.execute(LOCK)
    row = find(conn, scope, command_id)
    if row is None:
        raise Rejected("moving_charge_replacement_unavailable")
    if row["decision"] is not None:
        if veto and row["decision"] == "vetoed":
            return "vetoed"
        raise Rejected("moving_charge_replacement_closed")
    if not veto:
        approval = moving_charge_journal.decode(row["payload"])
        held = interruption(conn, scope, approval.replacement_of)
        verify_held(conn, held)
        verify_replacement(held, approval)
        if (
            not row["fresh"]
            or conn.execute(
                """SELECT 1 FROM commanding.moving_charge_replacement_decisions
               WHERE interrupted_command_id=%s AND decision='granted'""",
                (row["interrupted_command_id"],),
            ).fetchone()
        ):
            raise Rejected("moving_charge_replacement_closed")
        if verify is None:
            raise Rejected("moving_charge_replacement_unavailable")
        verify(held, approval)
    decision = "vetoed" if veto else "granted"
    conn.execute(
        """INSERT INTO commanding.moving_charge_replacement_decisions
           (command_id,interrupted_command_id,decision) VALUES (%s,%s,%s)""",
        (command_id, row["interrupted_command_id"], decision),
    )
    return decision if veto else moving_charge_journal.encode(approval)


def acknowledge(conn, scope, command_id, status):
    conn.execute(LOCK)
    row = find(conn, scope, command_id)
    if (
        row is None
        or row["decision"] != "granted"
        or status not in ("applied", "refused", "unknown")
    ):
        raise Rejected("moving_charge_replacement_unavailable")
    conn.execute(
        """INSERT INTO commanding.moving_charge_replacement_acknowledgments(command_id,status)
           VALUES (%s,%s) ON CONFLICT DO NOTHING""",
        (command_id, status),
    )
    saved = conn.execute(
        "SELECT status FROM commanding.moving_charge_replacement_acknowledgments WHERE command_id=%s",
        (command_id,),
    ).fetchone()
    if saved["status"] != status:
        raise Rejected("moving_charge_acknowledgment_conflict")
    return status
