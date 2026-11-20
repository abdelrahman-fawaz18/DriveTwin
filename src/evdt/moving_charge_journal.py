"""Durable operator approval and at-most-one grant for the local SUMO profile."""

from dataclasses import asdict, fields
from datetime import timedelta
from uuid import UUID

from psycopg.types.json import Jsonb

from evdt.command_journal import LOCK
from evdt.command_timing import ClockLease
from evdt.contract_profile import Rejected
from evdt.energy.moving_charge_handoff import ReviewedStop
from evdt.energy.moving_charge_recovery import ReplacementOf
from evdt.energy.route_aware.contracts import SourceScope
from evdt.energy.route_aware.wire import decode_record

OPTIONAL_FIELDS = {
    "detour_sha256",
    "minimum_handoff_soc",
    "replacement_of",
    "queue_opens_working_s",
}


def encode(approval):
    value = {**asdict(approval), "lease": approval.lease.encode()}
    for name in OPTIONAL_FIELDS:
        if value[name] is None:
            del value[name]
    return value


def decode(payload):
    names = {field.name for field in fields(ReviewedStop)}
    if not names - OPTIONAL_FIELDS <= set(payload) <= names:
        raise ValueError("Stored approval does not match the moving charging contract")
    return ReviewedStop(
        **{
            **payload,
            "scope": decode_record(SourceScope, payload["scope"]),
            "lease": ClockLease.decode(payload["lease"]),
            **(
                {"replacement_of": decode_record(ReplacementOf, payload["replacement_of"])}
                if "replacement_of" in payload
                else {}
            ),
        }
    )


def request(conn, *, command_id: UUID, scope: tuple[str, str], job_fence, candidate_sha256, review):
    """The authorized operator service supplies the already-reviewed approval."""
    conn.execute(LOCK)
    row = conn.execute(
        """SELECT i.command_id,i.payload,f.decision,i.expires_at > clock_timestamp() AS fresh
           FROM commanding.moving_charge_intents i
           LEFT JOIN commanding.moving_charge_handoffs f USING(command_id)
           WHERE i.run_id=%s AND i.asset_id=%s AND i.command_id=%s""",
        (*scope, command_id),
    ).fetchone()
    if row:
        existing = row["payload"]
        if existing["job_fence"] == job_fence and (
            existing["candidate_sha256"] == candidate_sha256
        ):
            return row["decision"] or ("approved" if row["fresh"] else "expired")
        raise Rejected("moving_charge_intent_conflict")
    if conn.execute(
        """SELECT 1 FROM commanding.moving_charge_intents i
           LEFT JOIN commanding.moving_charge_handoffs f USING(command_id)
           WHERE i.run_id=%s AND i.asset_id=%s
           AND (f.decision='granted' OR (f.decision IS NULL AND i.expires_at > clock_timestamp()))
           LIMIT 1""",
        scope,
    ).fetchone():
        raise Rejected("moving_charge_intent_conflict")
    approval = review()
    if (
        approval.replacement_of is not None
        or (approval.scope.run_id, approval.scope.asset_id) != scope
        or (approval.job_fence, approval.candidate_sha256) != (job_fence, candidate_sha256)
    ):
        raise Rejected("moving_charge_intent_unavailable")
    payload = encode(approval)
    expiration = approval.lease.anchor.utc + timedelta(
        microseconds=(approval.lease.deadline_ns - approval.lease.anchor.elapsed_ns) // 1000
    )
    inserted = conn.execute(
        """INSERT INTO commanding.moving_charge_intents
           (command_id,run_id,asset_id,job_fence,candidate_sha256,payload,expires_at)
           SELECT %s,r.run_id,r.asset_id,c.fence,%s,%s,%s
           FROM public.m_moving_charge_jobs c
           JOIN public.m_route_energy_results r ON r.fence=c.forecast_fence
           WHERE c.fence=%s AND r.run_id=%s AND r.asset_id=%s
             AND %s > clock_timestamp() + interval '1 millisecond'
             AND c.assignment_sha256=%s AND c.status='current'
             AND c.fence=(SELECT c2.fence FROM public.m_moving_charge_jobs c2
              JOIN public.m_route_energy_results r2 ON r2.fence=c2.forecast_fence
              WHERE r2.run_id=r.run_id AND r2.asset_id=r.asset_id
              ORDER BY c2.forecast_fence DESC,c2.fence DESC LIMIT 1)
             AND %s IN (c.payload->'decision'->'preferred'->>'candidate_sha256',
                         c.payload->'decision'->'fallback'->>'candidate_sha256')
           ON CONFLICT DO NOTHING""",
        (
            command_id,
            approval.candidate_sha256,
            Jsonb(payload),
            expiration,
            approval.job_fence,
            approval.scope.run_id,
            approval.scope.asset_id,
            expiration,
            approval.assignment_sha256,
            approval.candidate_sha256,
        ),
    ).rowcount
    if inserted != 1:
        raise Rejected("moving_charge_intent_unavailable")
    return "approved"


def _locked_intent(conn, scope, command_id):
    conn.execute(LOCK)
    row = conn.execute(
        """SELECT i.payload,f.decision FROM commanding.moving_charge_intents i
           LEFT JOIN commanding.moving_charge_handoffs f USING(command_id)
           WHERE i.run_id=%s AND i.asset_id=%s AND i.command_id=%s""",
        (*scope, command_id),
    ).fetchone()
    if row is None:
        raise Rejected("moving_charge_intent_unavailable")
    return row


def veto(conn, scope, command_id):
    row = _locked_intent(conn, scope, command_id)
    if row["decision"] == "granted":
        raise Rejected("moving_charge_already_granted")
    if row["decision"] is None:
        conn.execute(
            """INSERT INTO commanding.moving_charge_handoffs(command_id,run_id,asset_id,decision)
               VALUES (%s,%s,%s,'vetoed')""",
            (command_id, *scope),
        )
    return "vetoed"


def grant(conn, scope, command_id, verify):
    """Recheck trusted inputs under the existing command transaction lock."""
    row = _locked_intent(conn, scope, command_id)
    if row["decision"] is not None:
        raise Rejected("moving_charge_handoff_already_closed")
    if conn.execute(
        """SELECT 1 FROM commanding.moving_charge_handoffs
           WHERE run_id=%s AND asset_id=%s AND decision='granted'""",
        scope,
    ).fetchone():
        raise Rejected("moving_charge_already_granted")
    approval = decode(row["payload"])
    if approval.replacement_of is not None:
        raise Rejected("moving_charge_intent_unavailable")
    verify(approval)
    conn.execute(
        """INSERT INTO commanding.moving_charge_handoffs(command_id,run_id,asset_id,decision)
           VALUES (%s,%s,%s,'granted')""",
        (command_id, *scope),
    )
    # A successful response may be sent only after this transaction commits.
    return encode(approval)


def acknowledge(conn, scope, command_id, status):
    row = _locked_intent(conn, scope, command_id)
    if row["decision"] != "granted" or status not in ("applied", "refused", "unknown"):
        raise Rejected("moving_charge_acknowledgment_unavailable")
    conn.execute(
        """INSERT INTO commanding.moving_charge_acknowledgments(command_id,status)
           VALUES (%s,%s) ON CONFLICT (command_id) DO NOTHING""",
        (command_id, status),
    )
    saved = conn.execute(
        "SELECT status FROM commanding.moving_charge_acknowledgments WHERE command_id=%s",
        (command_id,),
    ).fetchone()
    if saved["status"] != status:
        raise Rejected("moving_charge_acknowledgment_conflict")
    return status
