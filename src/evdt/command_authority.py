"""Deterministic precedence for one explicitly sealed speed-cap decision."""

from evdt.command_effect import APPLY_AT_S
from evdt.command_timing import ClockLease, HostClock
from evdt.commands import OperatorScope, check_validity, read_command
from evdt.contract_profile import Rejected

FENCE_HEADER = "X-EVDT-Command-Fence"
FENCE_PROTOCOL = "evdt-fence/1"
PROPOSERS = {"operator": "local-operator", "thermal": "thermal-service", "energy": "energy-service"}


def proposal_scope(target, proposer):
    return OperatorScope(target.run_id, target.asset_id, PROPOSERS[proposer])


def enabled(conn, target):
    return (
        conn.execute(
            "SELECT 1 FROM commanding.arbitration_targets WHERE (run_id,asset_id)=(%s,%s)",
            (target.run_id, target.asset_id),
        ).fetchone()
        is not None
    )


def decision(conn, target):
    return conn.execute(
        """SELECT generation,command_id,selected_proposer,assessments,decided_at
           FROM commanding.decisions WHERE (run_id,asset_id)=(%s,%s)""",
        (target.run_id, target.asset_id),
    ).fetchone()


def rank(target, proposals, current):
    """Assess every submitted proposal, selecting the first eligible role in precedence order."""
    by_role = {row["proposer"]: row for row in proposals}
    chosen = None
    assessments = []
    for role in PROPOSERS:
        row = by_role.get(role)
        if row is None:
            continue
        command = read_command(bytes(row["wire"]), proposal_scope(target, role))
        lease = ClockLease(
            HostClock(str(row["boot_id"]), row["anchor_ns"], row["anchored_utc"]),
            row["deadline_ns"],
        )
        try:
            lease.verify(current)
            check_validity(command, current.utc, APPLY_AT_S)
        except Rejected as error:
            reason = str(error)
        else:
            if chosen is None:
                chosen = command, lease, role
                reason = "selected"
            else:
                reason = "lower_precedence"
        assessments.append(
            {"proposer": role, "command_id": command["command_id"], "reason": reason}
        )
    return chosen, assessments
