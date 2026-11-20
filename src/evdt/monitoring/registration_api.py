"""Private producer-only metadata service, separate from the read-only API."""

import hmac
import json
import os
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from evdt.contract_profile import Rejected
from evdt.monitoring.formats import checked_manifest
from evdt.monitoring.registry import clock_event, register
from evdt.monitoring.run_policy import checked

app = FastAPI(title="EV-DT source registration", version="1.0.0", docs_url=None, redoc_url=None)


def producer(authorization: Annotated[str | None, Header()] = None):
    entries = json.loads(Path(os.environ["WRITERS_FILE"]).read_text())["writers"]
    for entry in entries:
        if hmac.compare_digest(
            (authorization or "").encode(), ("Bearer " + entry["token"]).encode()
        ):
            return entry
    raise HTTPException(401, "Producer credential required")


async def body(request):
    chunks, length = [], 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > 65536:
            raise HTTPException(413, "Metadata payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def apply(request, writer, *, clock=False):
    raw = await body(request)
    try:
        parsed = checked(raw, "source-clock.schema.json") if clock else checked_manifest(raw)
        identity = parsed if clock else parsed["identity"]
        if (uuid.UUID(identity["run_id"]), identity["asset_id"], identity["source_id"]) != (
            uuid.UUID(writer["run_id"]),
            writer["asset_id"],
            writer["source_id"],
        ):
            raise HTTPException(403, "Producer outside source scope")
        return await run_in_threadpool(clock_event if clock else register, raw)
    except Rejected as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/health/ready")
def ready():
    from evdt.monitoring.registry import policy_for
    from evdt.monitoring.repository import connection

    with connection() as conn:
        policy_for(conn, os.environ["EVDT_RUN_ID"])
    return {"ready": True}


@app.post("/v1/registrations")
async def registration(request: Request, writer=Depends(producer)):
    return await apply(request, writer)


@app.post("/v1/clock-events")
async def clocks(request: Request, writer=Depends(producer)):
    return await apply(request, writer, clock=True)


@app.post("/v2/energy/route-contexts")
async def route_context(request: Request, writer=Depends(producer)):
    from evdt.energy.route_aware.context import parse_context
    from evdt.monitoring.route_context import register_context

    raw = await body(request)
    try:
        context = parse_context(raw)
        if (uuid.UUID(context.scope.run_id), context.scope.asset_id, context.scope.source_id) != (
            uuid.UUID(writer["run_id"]),
            writer["asset_id"],
            writer["source_id"],
        ):
            raise HTTPException(403, "Producer outside source scope")
        return await run_in_threadpool(register_context, raw)
    except (ValueError, Rejected) as exc:
        raise HTTPException(409, "Route context admission refused") from exc


@app.post("/v2/energy/held-charge-observations")
async def held_charge_observation(request: Request, writer=Depends(producer)):
    from evdt.energy.moving_charge_recovery import HeldChargeStop
    from evdt.energy.route_aware.wire import canonical_bytes, decode_record
    from evdt.monitoring.held_charge_observation import register_held

    raw = await body(request)
    if len(raw) > 8192:
        raise HTTPException(413, "Held charging observation too large")
    try:
        held = decode_record(HeldChargeStop, json.loads(raw))
        if raw != canonical_bytes(held):
            raise ValueError("Expected one canonical held observation")
        if (held.scope.run_id, held.scope.asset_id, held.scope.source_id) != (
            str(uuid.UUID(writer["run_id"])),
            writer["asset_id"],
            writer["source_id"],
        ):
            raise HTTPException(403, "Producer outside source scope")
        return await run_in_threadpool(register_held, held)
    except (ValueError, Rejected, RecursionError) as exc:
        raise HTTPException(409, "Held charging observation admission refused") from exc
