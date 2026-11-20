"""Private policy trigger and scoped operator override for the SUMO trial."""

from fastapi import Depends, Header
from pydantic import ValidationError

from evdt import command_journal, moving_charge_automatic
from evdt.charging_wire import Scope
from evdt.command_api import credential, operator
from evdt.commands import WIRE_PROFILE
from evdt.contract_profile import Rejected, decode
from evdt.monitoring.charging_advisory_view import present, read_state
from evdt.moving_charge_api import Body, assignment, review, router


def policy(authorization: str | None = Header(default=None)):
    credential("moving_charge_policy", authorization)


def target_scope(raw):
    try:
        value = Scope.model_validate(decode(raw, WIRE_PROFILE), strict=True)
    except ValidationError as exc:
        raise Rejected("moving_charge_schema") from exc
    target = command_journal.scope()
    if (value.run_id, value.asset_id) != (target.run_id, target.asset_id):
        raise Rejected("wrong_target")
    return value.run_id, value.asset_id


def eligible(conn, scope):
    current, forecast, job = read_state(conn, *scope)
    stations = assignment()
    shown = present(job, current, forecast, stations.policy.margin_soc)
    displayed = shown["estimate"]
    if (
        displayed is None
        or shown["status"] != "provisional_plan"
        or stations.policy.margin_soc < 0.01
    ):
        return None
    if displayed.get("off_route") or displayed.get("fallback_only"):
        return None
    # This reviewer owns the full source, station, clock and reserve checks.
    try:
        return review(
            conn,
            scope,
            displayed["job_fence"],
            displayed["candidate_sha256"],
            snapshot=(current, forecast, job),
        )
    except Rejected:
        return None


@router.post("/internal/v2/moving-charge/automatic/evaluate", dependencies=[Depends(policy)])
def evaluate(raw: Body):
    scope = target_scope(raw)
    with command_journal.connection() as conn:
        state = moving_charge_automatic.consider(conn, scope, lambda: eligible(conn, scope))
    return {"state": state}


@router.post("/v2/moving-charge/automatic/override", dependencies=[Depends(operator)])
def override(raw: Body):
    scope = target_scope(raw)
    with command_journal.connection() as conn:
        state = moving_charge_automatic.apply_override(conn, scope)
    return {"state": state}
