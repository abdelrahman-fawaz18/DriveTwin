"""Operator submission and private simulator handoff for a registered command target."""

import hmac
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from evdt import command_authority as authority
from evdt import command_journal as journal
from evdt import mission_intent
from evdt.command_effect import EVIDENCE
from evdt.command_timing import CLOCK_HEADER
from evdt.commands import RESULT, WIRE_PROFILE
from evdt.contract_profile import Rejected, decode

app = FastAPI(title="EV-DT simulation commands", docs_url=None, redoc_url=None, openapi_url=None)
AUDITED_ROUTES = {
    "/v1/commands": "submit",
    "/v1/commands/{command_id}": "read",
    "/v1/commands/{command_id}/cancel": "submit",
    "/v1/commands/{command_id}/replace": "submit",
    "/v1/mission-intents": "submit",
    "/v1/mission-intents/{intent_id}": "read",
    "/v1/operator-timeline": "read",
    "/internal/v1/claim": "claim",
    "/internal/v1/result": "result",
    "/internal/v1/effect": "effect",
    "/internal/v1/refusal": "execute",
    "/v1/proposals/{proposer}": "submit",
    "/v1/authority/seal": "submit",
    "/v1/authority": "read",
    "/internal/v1/verify-fence": "claim",
}
STATUS = {
    "credential_required": 401,
    "payload_too_large": 413,
    "wrong_target": 403,
    "wrong_run": 403,
    "wrong_asset": 403,
    "unauthorized_issuer": 403,
    "command_conflict": 409,
    "command_not_found": 404,
    "command_not_pending": 409,
    "intent_not_found": 404,
    "intent_conflict": 409,
    "mission_unavailable": 409,
    "unknown_station": 422,
    "result_conflict": 409,
    "effect_conflict": 409,
    "attempt_not_found": 409,
    "attempt_refused": 409,
    "refusal_conflict": 409,
    "authority_disabled": 403,
    "authority_required": 409,
    "authority_closed": 409,
    "proposal_conflict": 409,
    "no_eligible_proposal": 409,
    "moving_charge_intent_conflict": 409,
    "moving_charge_intent_unavailable": 409,
    "moving_charge_already_granted": 409,
    "moving_charge_handoff_already_closed": 409,
    "moving_charge_acknowledgment_unavailable": 409,
    "moving_charge_acknowledgment_conflict": 409,
    "moving_charge_obsolete_plan": 409,
    "moving_charge_stale_progress": 409,
    "moving_charge_source_unavailable": 409,
    "moving_charge_battery_outside_domain": 409,
    "moving_charge_commit_point_passed": 409,
    "moving_charge_bay_unavailable": 409,
    "moving_charge_execution_outside_domain": 409,
    "moving_charge_automatic_profile_exclusive": 403,
    "moving_charge_auto_authority_changed": 409,
    "moving_charge_auto_override": 409,
    "authority_fence_refused": 409,
}
AUDIT_REASONS = set(STATUS) | {
    "invalid_json",
    "portable_json",
    "command_schema",
    "replay_mode",
    "unsupported_capability",
    "wall_validity",
    "simulation_validity",
    "executor_clock_schema",
    "result_schema",
    "effect_schema",
    "executor_refusal_schema",
    "authority_schema",
    "cancellation_schema",
    "mission_intent_schema",
    "moving_charge_schema",
    "moving_charge_intent_conflict",
    "moving_charge_intent_unavailable",
    "moving_charge_already_granted",
    "moving_charge_handoff_already_closed",
    "moving_charge_acknowledgment_unavailable",
    "moving_charge_acknowledgment_conflict",
    "moving_charge_obsolete_plan",
    "moving_charge_stale_progress",
    "moving_charge_source_unavailable",
    "moving_charge_battery_outside_domain",
    "moving_charge_commit_point_passed",
    "moving_charge_bay_unavailable",
    "moving_charge_execution_outside_domain",
    "held_charge_source_unavailable",
    "held_charge_observation_unavailable",
    "moving_charge_interruption_unavailable",
    "moving_charge_interruption_conflict",
    "moving_charge_replacement_unavailable",
    "moving_charge_replacement_conflict",
    "moving_charge_replacement_closed",
}


def credential(role: str, authorization: str | None) -> None:
    tokens = {
        name: Path(f"/run/secrets/command_{name}").read_bytes().strip()
        for name in ("operator", "executor", "observer")
    }
    for name in ("thermal", "energy", "moving_charge_policy"):
        path = Path(f"/run/secrets/command_{name}")
        if path.is_file():
            tokens[name] = path.read_bytes().strip()
    if min(map(len, tokens.values())) < 32 or len(set(tokens.values())) != len(tokens):
        raise HTTPException(503, "Separate command credentials are required")
    if role not in tokens or not hmac.compare_digest(
        (authorization or "").encode(), b"Bearer " + tokens[role]
    ):
        raise Rejected("credential_required")


def operator(authorization: Annotated[str | None, Header()] = None) -> None:
    credential("operator", authorization)


def executor(authorization: Annotated[str | None, Header()] = None) -> None:
    credential("executor", authorization)


def observer(authorization: Annotated[str | None, Header()] = None) -> None:
    credential("observer", authorization)


def proposal_credential(
    proposer: Literal["operator", "thermal", "energy"],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    credential(proposer, authorization)
    return proposer


async def bounded_body(request: Request) -> bytes:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > WIRE_PROFILE["max_payload_bytes"]:
            raise Rejected("payload_too_large")
        raw.extend(chunk)
    return bytes(raw)


@app.exception_handler(Rejected)
async def rejected(request: Request, error: Rejected) -> JSONResponse:
    code = str(error)
    code = code if code in AUDIT_REASONS else "request_rejected"
    operation = AUDITED_ROUTES.get(getattr(request.scope.get("route"), "path", None))
    if operation:
        await run_in_threadpool(journal.audit_request, operation, code)
    headers = {"WWW-Authenticate": "Bearer"} if code == "credential_required" else None
    return JSONResponse({"detail": code}, status_code=STATUS.get(code, 422), headers=headers)


@app.get("/health/ready")
def ready():
    target = journal.scope()
    with journal.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM commanding.targets WHERE (run_id,asset_id,issuer)=(%s,%s,%s)",
            (target.run_id, target.asset_id, target.issuer),
        ).fetchone()
    if row is None:
        raise HTTPException(503, "Command target is unavailable")
    return {"ready": True}


@app.post("/v1/commands", dependencies=[Depends(operator)])
def submit(raw: Annotated[bytes, Depends(bounded_body)]):
    created, value = journal.submit(raw)
    return JSONResponse(jsonable_encoder(value), status_code=201 if created else 200)


@app.post("/v1/proposals/{proposer}")
def propose(
    proposer: Annotated[str, Depends(proposal_credential)],
    raw: Annotated[bytes, Depends(bounded_body)],
):
    created, identity = journal.submit_proposal(proposer, raw)
    return JSONResponse({"command_id": identity}, status_code=201 if created else 200)


@app.post("/v1/authority/seal", dependencies=[Depends(operator)])
def seal(raw: Annotated[bytes, Depends(bounded_body)]):
    if decode(raw, WIRE_PROFILE) != {}:
        raise Rejected("authority_schema")
    created, value = journal.seal_decision()
    return JSONResponse(jsonable_encoder(value), status_code=201 if created else 200)


@app.get("/v1/authority", dependencies=[Depends(operator)])
def read_authority():
    with journal.connection() as conn:
        value = authority.decision(conn, journal.scope())
    if value is None:
        raise HTTPException(404, "Authority decision not found")
    return value


@app.get("/v1/commands/{command_id}", dependencies=[Depends(operator)])
def read(command_id: UUID):
    with journal.connection() as conn:
        row = journal.fetch(conn, journal.scope(), command_id)
        if row is None:
            raise HTTPException(404, "Command not found")
        return journal.view(row)


@app.post("/v1/commands/{command_id}/cancel", dependencies=[Depends(operator)])
def cancel(command_id: UUID, raw: Annotated[bytes, Depends(bounded_body)]):
    if decode(raw, WIRE_PROFILE) != {}:
        raise Rejected("cancellation_schema")
    return journal.change_pending(command_id)


@app.post("/v1/commands/{command_id}/replace", dependencies=[Depends(operator)])
def replace(command_id: UUID, raw: Annotated[bytes, Depends(bounded_body)]):
    return journal.change_pending(command_id, raw)


@app.post("/v1/mission-intents", dependencies=[Depends(operator)])
def submit_mission(raw: Annotated[bytes, Depends(bounded_body)]):
    created, value = journal.submit_mission(raw)
    return JSONResponse(jsonable_encoder(value), status_code=201 if created else 200)


@app.get("/v1/mission-intents/{intent_id}", dependencies=[Depends(operator)])
def read_mission(intent_id: UUID):
    with journal.connection() as conn:
        row = mission_intent.fetch(conn, journal.scope(), intent_id)
        if row is None:
            raise Rejected("intent_not_found")
        return mission_intent.view(row)


@app.get("/v1/operator-timeline", dependencies=[Depends(operator)])
def operator_timeline(run_id: UUID, asset_id: str):
    target = journal.scope()
    if (str(run_id), asset_id) != (target.run_id, target.asset_id):
        raise Rejected("wrong_target")
    scope = (target.run_id, target.asset_id)
    with journal.connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        commands = conn.execute(
            """SELECT command_id FROM commanding.requests WHERE (run_id,asset_id)=(%s,%s)
               ORDER BY accepted_at DESC,command_id DESC LIMIT 20""",
            scope,
        ).fetchall()
        intents = conn.execute(
            """SELECT intent_id FROM commanding.mission_intents WHERE (run_id,asset_id)=(%s,%s)
               ORDER BY recorded_at DESC,intent_id DESC LIMIT 20""",
            scope,
        ).fetchall()
        stations = conn.execute(
            """SELECT station_id FROM commanding.mission_stations WHERE (run_id,asset_id)=(%s,%s)
               ORDER BY station_id LIMIT 100""",
            scope,
        ).fetchall()
        entries = [journal.fetch(conn, target, row["command_id"]) for row in commands]
        return {
            "run_id": target.run_id,
            "asset_id": target.asset_id,
            "server_time": datetime.now(timezone.utc),
            "arbitrated": authority.enabled(conn, target),
            "authority": authority.decision(conn, target),
            "commands": [
                {**journal.view(row), "request": json.loads(row["wire"])} for row in entries
            ],
            "missions": [
                mission_intent.view(mission_intent.fetch(conn, target, row["intent_id"]))
                for row in intents
            ],
            "stations": [row["station_id"] for row in stations],
        }


@app.post("/internal/v1/claim", dependencies=[Depends(executor)])
def claim(raw: Annotated[bytes, Depends(bounded_body)]):
    value = decode(raw, WIRE_PROFILE)
    if (
        not isinstance(value, dict)
        or not {"sim_time_s", "state"} <= set(value) <= {"sim_time_s", "state", "fence_protocol"}
        or value["state"] not in ("running", "paused")
        or value.get("fence_protocol") not in (None, authority.FENCE_PROTOCOL)
    ):
        raise Rejected("executor_clock_schema")
    time_s = value["sim_time_s"]
    if (
        isinstance(time_s, bool)
        or not isinstance(time_s, (float, int))
        or not math.isfinite(time_s)
        or time_s < 0
    ):
        raise Rejected("executor_clock_schema")
    delivery = journal.claim(time_s, value["state"], value.get("fence_protocol"))
    if delivery is None:
        return Response(status_code=204)
    command, lease, generation = delivery
    headers = {CLOCK_HEADER: lease.encode()}
    if generation is not None:
        headers[authority.FENCE_HEADER] = str(generation)
    return Response(command, media_type="application/json", headers=headers)


@app.post("/internal/v1/verify-fence", dependencies=[Depends(executor)], status_code=204)
def verify_fence(raw: Annotated[bytes, Depends(bounded_body)]):
    value = decode(raw, WIRE_PROFILE)
    if (
        not isinstance(value, dict)
        or set(value) != {"command_id", "run_id", "asset_id", "generation"}
        or type(value["generation"]) is not int
        or value["generation"] < 0
    ):
        raise Rejected("authority_schema")
    try:
        identity = UUID(value["command_id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise Rejected("authority_schema") from error
    target = journal.scope()
    if (value["run_id"], value["asset_id"]) != (target.run_id, target.asset_id):
        raise Rejected("wrong_target")
    journal.verify_fence(identity, value["generation"])
    return Response(status_code=204)


@app.post("/internal/v1/refusal", dependencies=[Depends(executor)], status_code=204)
def refusal(raw: Annotated[bytes, Depends(bounded_body)]):
    value = decode(raw, WIRE_PROFILE)
    try:
        if set(value) != {"command_id", "reason"} or not isinstance(value["reason"], str):
            raise ValueError
        identity = UUID(value["command_id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise Rejected("executor_refusal_schema") from error
    journal.save_refusal(identity, value["reason"])
    return Response(status_code=204)


@app.post("/internal/v1/result", dependencies=[Depends(executor)])
def result(raw: Annotated[bytes, Depends(bounded_body)]):
    value = decode(raw, WIRE_PROFILE)
    if not RESULT.is_valid(value):
        raise Rejected("result_schema")
    return journal.save_result(value)


@app.post("/internal/v1/effect", dependencies=[Depends(observer)])
def effect(raw: Annotated[bytes, Depends(bounded_body)]):
    value = decode(raw, WIRE_PROFILE)
    if not EVIDENCE.is_valid(value):
        raise Rejected("effect_schema")
    return journal.save_effect(value)


if os.environ.get("EVDT_MOVING_CHARGE_COMMANDS") == "1":
    from evdt.moving_charge_api import router as moving_charge_router

    if os.environ.get("EVDT_MOVING_CHARGE_AUTOMATIC") == "1":
        from evdt import moving_charge_automatic_api  # noqa: F401

    if os.environ.get("EVDT_MOVING_CHARGE_RECOVERY") == "1":
        from evdt import moving_charge_recovery_api  # noqa: F401

    app.include_router(moving_charge_router)
