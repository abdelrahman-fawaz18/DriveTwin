"""State and authority for an explicit SUMO automatic qualification profile."""

from uuid import uuid4

from evdt import moving_charge_journal
from evdt.command_journal import LOCK
from evdt.contract_profile import Rejected


def overridden(conn, scope):
    return (
        conn.execute(
            "SELECT 1 FROM commanding.moving_charge_auto_overrides WHERE (run_id,asset_id)=(%s,%s)",
            scope,
        ).fetchone()
        is not None
    )


def apply_override(conn, scope):
    conn.execute(LOCK)
    conn.execute(
        """INSERT INTO commanding.moving_charge_auto_overrides(run_id,asset_id)
           VALUES (%s,%s) ON CONFLICT DO NOTHING""",
        scope,
    )
    already_granted = (
        conn.execute(
            """SELECT 1 FROM commanding.moving_charge_handoffs
           WHERE (run_id,asset_id)=(%s,%s) AND decision='granted'""",
            scope,
        ).fetchone()
        is not None
    )
    return "grant_already_issued" if already_granted else "override_latched"


def consider(conn, scope, read_candidate):
    """Only the trusted service chooses the candidate. Require two working-time readings."""
    conn.execute(LOCK)
    if overridden(conn, scope):
        return "override_latched"
    # A pending or completed intent belongs to the normal one-use journal.
    if (
        conn.execute(
            """SELECT 1 FROM commanding.moving_charge_intents i
           LEFT JOIN commanding.moving_charge_handoffs f USING(command_id)
           WHERE (i.run_id,i.asset_id)=(%s,%s)
             AND (f.decision='granted' OR (f.decision IS NULL AND i.expires_at>clock_timestamp()))""",
            scope,
        ).fetchone()
        is not None
    ):
        return "command_present"
    approval = read_candidate()
    if approval is None:
        return "no_eligible_offer"
    fence, candidate = approval.job_fence, approval.candidate_sha256
    conn.execute(
        """INSERT INTO commanding.moving_charge_auto_previews
           (run_id,asset_id,job_fence,candidate_sha256,first_working_s)
           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (*scope, fence, candidate, approval.working_not_before),
    )
    row = conn.execute(
        """SELECT candidate_sha256,first_working_s,
           first_observed_at >= clock_timestamp() - interval '5 seconds' AS fresh
           FROM commanding.moving_charge_auto_previews WHERE (run_id,asset_id,job_fence)=(%s,%s,%s)""",
        (*scope, fence),
    ).fetchone()
    if not (
        row["candidate_sha256"] == candidate
        and row["fresh"]
        and 1 <= approval.working_not_before - row["first_working_s"] <= 3
    ):
        return "waiting_for_confirmation"
    identity = uuid4()
    state = moving_charge_journal.request(
        conn,
        command_id=identity,
        scope=scope,
        job_fence=fence,
        candidate_sha256=candidate,
        review=lambda: approval,
    )
    if state != "approved":
        raise Rejected("moving_charge_auto_authority_changed")
    conn.execute(
        """INSERT INTO commanding.moving_charge_auto_approvals
           (command_id,run_id,asset_id,job_fence,confirmed_working_s) VALUES (%s,%s,%s,%s,%s)""",
        (identity, *scope, fence, approval.working_not_before),
    )
    return state


def require_grant(conn, scope, command_id):
    """Call from the journal's locked grant transaction."""
    if overridden(conn, scope):
        raise Rejected("moving_charge_auto_override")
    if (
        conn.execute(
            """SELECT 1 FROM commanding.moving_charge_auto_approvals
           WHERE command_id=%s AND (run_id,asset_id)=(%s,%s)""",
            (command_id, *scope),
        ).fetchone()
        is None
    ):
        raise Rejected("moving_charge_auto_authority_changed")
