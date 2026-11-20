"""Optional same-origin routes to the local operator API."""

import os
import re
from http.client import HTTPConnection
from http.client import HTTPException as UpstreamError
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/v1/operator/{run_id}/{asset_id}")
POST_PATH = re.compile(r"(?:commands(?:/[0-9a-f-]{36}/(?:cancel|replace))?|mission-intents)")
CHARGING_POST = frozenset(("approve", "veto"))
CHARGING_RECOVERY_POST = frozenset(("recovery/approve", "recovery/veto"))
MAX_BODY = 2048
MAX_REPLY = 256 * 1024


def validate_scope(run_id, asset_id):
    if (str(run_id), asset_id) != (os.environ["EVDT_RUN_ID"], os.environ["EVDT_OPERATOR_ASSET"]):
        raise HTTPException(403, "Operator target is outside this deployment")


def forward(method, path, credential, body=None):
    if not credential or len(credential) > 512 or not credential.startswith("Bearer "):
        raise HTTPException(401, "Operator credential required")
    upstream = HTTPConnection("command-api", 8000, timeout=5)
    try:
        upstream.request(
            method,
            path,
            body=body,
            headers={
                "Authorization": credential,
                "Content-Type": "application/json",
            },
        )
        reply = upstream.getresponse()
        data = reply.read(MAX_REPLY + 1)
        if len(data) > MAX_REPLY or not 200 <= reply.status < 500 or 300 <= reply.status < 400:
            raise HTTPException(502, "Operator service unavailable")
        return Response(data, status_code=reply.status, media_type="application/json")
    except (OSError, UpstreamError) as error:
        raise HTTPException(502, "Operator service unavailable") from error
    finally:
        upstream.close()


@router.get("/timeline")
def timeline(run_id: UUID, asset_id: str, request: Request):
    validate_scope(run_id, asset_id)
    query = urlencode({"run_id": str(run_id), "asset_id": asset_id})
    return forward("GET", "/v1/operator-timeline?" + query, request.headers.get("Authorization"))


@router.post("/{action:path}")
async def mutate(run_id: UUID, asset_id: str, action: str, request: Request):
    validate_scope(run_id, asset_id)
    if not POST_PATH.fullmatch(action):
        raise HTTPException(404, "Operator action unavailable")
    if (
        request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "Expected JSON operator action")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "Operator action exceeds its size limit")
        body.extend(chunk)
    return await run_in_threadpool(
        forward, "POST", "/v1/" + action, request.headers.get("Authorization"), bytes(body)
    )


charging_router = APIRouter(prefix="/v2/operator/{run_id}/{asset_id}/charging")


def validate_charging_scope(run_id, asset_id):
    if (str(run_id), asset_id) != (
        os.environ["EVDT_RUN_ID"],
        os.environ["EVDT_MOVING_CHARGE_OPERATOR_ASSET"],
    ):
        raise HTTPException(403, "Charging operator target is outside this deployment")


@charging_router.get("/status")
def charging_status(run_id: UUID, asset_id: str, request: Request):
    validate_charging_scope(run_id, asset_id)
    return forward("GET", "/v2/moving-charge/status", request.headers.get("Authorization"))


@charging_router.get("/recovery/status")
def charging_recovery_status(run_id: UUID, asset_id: str, request: Request):
    validate_charging_scope(run_id, asset_id)
    if os.environ.get("EVDT_MOVING_CHARGE_RECOVERY") != "1":
        raise HTTPException(404, "Charging replacement is unavailable")
    return forward("GET", "/v2/moving-charge/recovery/status", request.headers.get("Authorization"))


@charging_router.post("/{action:path}")
async def charging_action(run_id: UUID, asset_id: str, action: str, request: Request):
    validate_charging_scope(run_id, asset_id)
    recovery = os.environ.get("EVDT_MOVING_CHARGE_RECOVERY") == "1"
    automatic_override = (
        action == "automatic/override" and os.environ.get("EVDT_MOVING_CHARGE_AUTOMATIC") == "1"
    )
    if action not in CHARGING_POST and not (
        (recovery and action in CHARGING_RECOVERY_POST) or automatic_override
    ):
        raise HTTPException(404, "Charging operator action unavailable")
    if (
        request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "Expected JSON operator action")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "Operator action exceeds its size limit")
        body.extend(chunk)
    return await run_in_threadpool(
        forward,
        "POST",
        "/v2/moving-charge/" + action,
        request.headers.get("Authorization"),
        bytes(body),
    )
