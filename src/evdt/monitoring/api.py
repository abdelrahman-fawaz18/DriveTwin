"""Asset-scoped monitoring API with an optional operator gateway."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from evdt.monitoring import (
    analysis_views,
    dashboard,
    energy_views,
    history_queries,
    route_energy_views,
)
from evdt.monitoring.asset_catalog import for_policy
from evdt.monitoring.http_readers import dashboard_operator_asset, energy_reader, reader
from evdt.monitoring.repository import connection

app = FastAPI(title="EV-DT monitoring", version="0.1.0")
if os.environ.get("EVDT_OPERATOR_ASSET"):
    from evdt.monitoring.operator_routes import router

    app.include_router(router)
if os.environ.get("EVDT_MOVING_CHARGE_OPERATOR_ASSET"):
    from evdt.monitoring.operator_routes import charging_router

    app.include_router(charging_router)
LIVE_REFRESH_S = 0.25


@app.get("/v2/energy-input")
def route_energy_input(asset_id: str, assets=Depends(energy_reader)):
    authorize(asset_id, assets)
    return route_energy_views.input_view(asset_id)


@app.get("/v2/energy-forecasts")
def route_energy_forecasts(
    asset_id: str,
    before: Annotated[int | None, Query(ge=1, le=9007199254740991)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return route_energy_views.read(asset_id, before, limit)


@app.get("/v2/charging-advisory")
def charging_advisory(asset_id: str, request: Request, assets=Depends(reader)):
    from evdt.monitoring.charging_advisory_view import view

    authorize(asset_id, assets)
    result = view(asset_id)
    if result.get("operator_control"):
        result["can_respond"] = asset_id == dashboard_operator_asset(
            request.headers.get("Authorization"), "EVDT_MOVING_CHARGE_OPERATOR_ASSET"
        )
    return result


@app.get("/v2/energy-forecasts/{fence}")
def route_energy_forecast_record(fence: int, asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    if not 1 <= fence <= 9007199254740991:
        raise HTTPException(422, "Invalid forecast identity")
    return route_energy_views.read(asset_id, fence=fence)


@app.get("/v2/energy-forecasts/{fence}/map")
def route_energy_forecast_map(fence: int, asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    if not 1 <= fence <= 9007199254740991:
        raise HTTPException(422, "Invalid forecast identity")
    return route_energy_views.map_view(asset_id, fence)


@app.get("/v1/energy-input")
def energy_input(asset_id: str, assets=Depends(energy_reader)):
    authorize(asset_id, assets)
    return energy_views.input_view(asset_id)


@app.get("/v1/energy-forecasts")
def energy_forecasts(
    asset_id: str,
    before: Annotated[int | None, Query(ge=1, le=9007199254740991)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return energy_views.read(asset_id, before, limit)


@app.get("/v1/energy-forecasts/{record_id}")
def energy_forecast_record(record_id: str, asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    if len(record_id) != 64 or any(c not in "0123456789abcdef" for c in record_id):
        raise HTTPException(422, "Invalid forecast identity")
    return energy_views.read(asset_id, record_id=record_id)


@app.get("/v1/time")
def clock_exchange(
    asset_id: Annotated[str | None, Query(max_length=128)] = None,
    event_id: UUID | None = None,
    assets=Depends(reader),
):
    received = time.time()
    monotonic = time.monotonic()
    if (asset_id is None) != (event_id is None):
        raise HTTPException(422, "Use both asset and event for a DOM witness")
    if asset_id is not None:
        authorize(asset_id, assets)
    return {
        "version": "clock-exchange/1.1.0",
        "monotonic_resolution_s": time.get_clock_info("monotonic").resolution,
        "asset_id": asset_id,
        "event_id": str(event_id) if event_id else None,
        "received_utc_s": received,
        "received_monotonic_s": monotonic,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "sent_utc_s": time.time(),
        "sent_monotonic_s": time.monotonic(),
    }


@app.middleware("http")
async def browser_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    trip_launcher = " http://127.0.0.1:18760" if os.getenv("EVDT_LOCAL_TRIP_CONTROL") == "1" else ""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; "
        f"connect-src 'self'{trip_launcher}; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    return response


def authorize(asset, assets):
    if asset not in assets:
        raise HTTPException(403, "Asset outside reader scope")


@app.get("/health/ready")
def ready():
    with connection() as conn:
        conn.execute("SELECT name FROM m_migrations LIMIT 1").fetchone()
    return {"ready": True, "mode": "simulation"}


@app.get("/replay", include_in_schema=False)
def open_replay():
    target = os.environ.get("REPLAY_BROWSER_URL")
    if not target:
        raise HTTPException(503, "Replay service is not configured for this deployment")
    return RedirectResponse(target)


@app.get("/v1/catalog")
def catalog(request: Request, asset_id: str | None = None, assets=Depends(reader)):
    if asset_id is not None:
        authorize(asset_id, assets)
    with connection() as conn:
        policy = conn.execute(
            "SELECT policy FROM m_run_definitions WHERE run_id=%s", (os.environ["EVDT_RUN_ID"],)
        ).fetchone()["policy"]
    available = {s["asset_id"] for s in policy["sources"]} & set(assets)
    selected = {asset: for_policy(policy, asset) for asset in sorted(available)}
    if asset_id is not None:
        if asset_id not in selected:
            raise HTTPException(404, "Asset is not registered in this run")
        return selected[asset_id]
    versions = {c["catalog_version"] for c in selected.values()}
    value = (
        next(iter(selected.values()))
        if len(versions) == 1
        else {"catalog_version": "mixed", "signals": {}}
    )
    return {
        **value,
        "assets": {a: c["assets"][a] for a, c in selected.items()},
        "asset_catalogs": {a: c["catalog_version"] for a, c in selected.items()},
        **(
            {"vehicle_control_asset": operator_asset}
            if (
                operator_asset := dashboard_operator_asset(
                    request.headers.get("Authorization"), "EVDT_OPERATOR_ASSET"
                )
            )
            in selected
            else {}
        ),
    }


@app.get("/v1/observations")
def history(
    asset_id: str, limit: Annotated[int, Query(ge=1, le=1000)] = 100, assets=Depends(reader)
):
    authorize(asset_id, assets)
    with connection() as conn:
        rows = conn.execute(
            """SELECT event_id,asset_id,source_id,source_epoch,stream_seq,sim_time_s,
                            observed_at,received_at,persisted_at,payload FROM m_observations
                            WHERE run_id=%s AND asset_id=%s ORDER BY sim_time_s,event_id LIMIT %s""",
            (os.environ["EVDT_RUN_ID"], asset_id, limit),
        ).fetchall()
    return {"mode": "simulation", "run_id": os.environ["EVDT_RUN_ID"], "items": rows}


@app.get("/v1/current")
def current(asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    return dashboard.snapshot(asset_id)


@app.get("/v1/snapshot")
def dashboard_snapshot(asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    return live_snapshot(asset_id)


def live_snapshot(asset_id):
    completion_file = os.getenv("EVDT_MONITORING_COMPLETION_FILE")
    closed = completion_file and Path(completion_file).is_file()
    value = dashboard.snapshot(asset_id)
    if closed:
        value["monitoring_closed"] = True
    return value


@app.get("/v1/history")
def signal_history(
    asset_id: str,
    signal_id: str,
    as_of: str,
    from_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 0,
    to_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 86400,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    after: Annotated[str | None, Query(max_length=1000)] = None,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return dashboard.history(asset_id, signal_id, from_s, to_s, as_of, after, limit)


@app.get("/v1/stream")
def dashboard_stream(
    asset_id: str,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    if cursor is not None:
        prior = dashboard.parse_cursor(cursor, os.environ["EVDT_RUN_ID"], asset_id)
        current = dashboard.snapshot(asset_id)
        if prior > dashboard.parse_cursor(current["cursor"], os.environ["EVDT_RUN_ID"], asset_id):
            raise HTTPException(409, "Cursor is ahead of this run; replace snapshot")

    async def events():
        # Full replacement snapshots deliberately coalesce redraws, never durable history.
        # Read in a worker so a slow database cannot block the async server event loop.
        first = True
        while True:
            started = time.monotonic()
            value = await asyncio.to_thread(live_snapshot, asset_id)
            value["resynchronized"] = first and cursor is not None
            first = False
            body = json.dumps(jsonable_encoder(value), allow_nan=False, separators=(",", ":"))
            yield f"id: {value['cursor']}\nevent: snapshot\ndata: {body}\n\n"
            if value.get("monitoring_closed"):
                return
            # Include query/serialization time in the cadence, instead of adding it.
            # Snapshots can still coalesce under overload; qualification must detect it.
            await asyncio.sleep(max(0, LIVE_REFRESH_S - (time.monotonic() - started)))

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )


@app.get("/v1/map-context")
def map_context(asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    with connection() as conn:
        row = conn.execute(
            "SELECT raw_context,sha256 FROM m_map_contexts WHERE run_id=%s AND asset_id=%s",
            (os.environ["EVDT_RUN_ID"], asset_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "No mission map context assigned to this asset")
    return Response(
        bytes(row["raw_context"]),
        media_type="application/geo+json",
        headers={"X-Content-SHA256": row["sha256"]},
    )


@app.get("/v1/trajectory")
def trajectory(
    asset_id: str,
    as_of: Annotated[str, Query(max_length=200)],
    from_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 0,
    to_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 86400,
    after: Annotated[str | None, Query(max_length=1000)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    since: Annotated[str | None, Query(max_length=200)] = None,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return analysis_views.trajectory(asset_id, as_of, from_s, to_s, after, limit, since=since)


@app.get("/v1/events")
def events(
    asset_id: str,
    as_of: Annotated[str, Query(max_length=200)],
    before: Annotated[int | None, Query(ge=1, le=9007199254740991)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return analysis_views.events(asset_id, as_of, before, limit)


@app.get("/v1/system-health")
def system_health(asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    return analysis_views.health(asset_id)


@app.get("/v1/export")
def export(
    asset_id: str,
    as_of: Annotated[str, Query(max_length=200)],
    signals: Annotated[list[str], Query(min_length=1, max_length=4)],
    from_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 0,
    to_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 86400,
    format: Literal["json", "csv"] = "json",
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    metadata, rows = analysis_views.raw_query(asset_id, signals, from_s, to_s, as_of)
    body = (
        analysis_views.export_csv(metadata, rows)
        if format == "csv"
        else json.dumps(
            jsonable_encoder({"metadata": metadata, "items": rows}),
            allow_nan=False,
            separators=(",", ":"),
        )
    )
    return Response(
        body,
        media_type="text/csv" if format == "csv" else "application/json",
        headers={"Content-Disposition": f'attachment; filename="evdt-{asset_id}.{format}"'},
    )


@app.get("/v1/summary")
def summary(
    asset_id: str,
    as_of: Annotated[str, Query(max_length=200)],
    signals: Annotated[list[str], Query(min_length=1, max_length=4)],
    from_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 0,
    to_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 86400,
    bucket_s: Annotated[float, Query(gt=0, le=86400, allow_inf_nan=False)] = 60,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    metadata, rows = analysis_views.raw_query(asset_id, signals, from_s, to_s, as_of)
    return {
        "metadata": {**metadata, "resolution": "bucket summary", "bucket_s": bucket_s},
        "items": analysis_views.summarize(rows, from_s, to_s, bucket_s, signals),
    }


@app.get("/v1/plot")
def reduced_history(
    asset_id: str,
    as_of: Annotated[str, Query(max_length=200)],
    signals: Annotated[list[str], Query(min_length=1, max_length=5)],
    from_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 0,
    to_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 86400,
    point_limit: Annotated[int, Query(ge=4, le=5000)] = 5000,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return history_queries.plot(asset_id, signals, from_s, to_s, as_of, point_limit)


@app.get("/v1/history-summary")
def bounded_history_summary(
    asset_id: str,
    as_of: Annotated[str, Query(max_length=200)],
    signals: Annotated[list[str], Query(min_length=1, max_length=5)],
    from_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 0,
    to_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 86400,
    bucket_s: Annotated[float, Query(gt=0, le=86400, allow_inf_nan=False)] = 60,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return history_queries.summary(asset_id, signals, from_s, to_s, as_of, bucket_s)


@app.get("/v1/export-page")
def paginated_export(
    asset_id: str,
    as_of: Annotated[str, Query(max_length=200)],
    signals: Annotated[list[str], Query(min_length=1, max_length=5)],
    from_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 0,
    to_s: Annotated[float, Query(ge=0, le=1e12, allow_inf_nan=False)] = 86400,
    after: Annotated[str | None, Query(max_length=1800)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    assets=Depends(reader),
):
    authorize(asset_id, assets)
    return history_queries.export_page(asset_id, signals, from_s, to_s, as_of, after, limit)


@app.get("/v1/run")
def run_metadata(assets=Depends(reader)):
    with connection() as conn:
        definition = conn.execute(
            "SELECT policy_sha256,created_at FROM m_run_definitions WHERE run_id=%s",
            (os.environ["EVDT_RUN_ID"],),
        ).fetchone()
        registrations = conn.execute(
            "SELECT asset_id,source_id,source_epoch,session_id,manifest_sha256,minimum_sim_time_s,registered_at FROM m_registrations WHERE run_id=%s AND asset_id=ANY(%s) ORDER BY asset_id,source_id,source_epoch",
            (os.environ["EVDT_RUN_ID"], assets),
        ).fetchall()
    return {
        "run_id": os.environ["EVDT_RUN_ID"],
        "mode": "simulation",
        "definition": definition,
        "sources": registrations,
    }


@app.get("/v1/recipes/{fingerprint}")
def recipe(fingerprint: str, asset_id: str, assets=Depends(reader)):
    authorize(asset_id, assets)
    with connection() as conn:
        row = conn.execute(
            "SELECT raw_manifest FROM m_registrations WHERE run_id=%s AND asset_id=%s AND manifest_sha256=%s",
            (os.environ["EVDT_RUN_ID"], asset_id, fingerprint),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Recipe not found in authorized asset")
    return Response(content=bytes(row["raw_manifest"]), media_type="application/json")


FRONTEND = Path(os.environ.get("DASHBOARD_FILES", "/app/dashboard"))
if FRONTEND.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="dashboard-assets")

    @app.get("/", include_in_schema=False)
    def dashboard_page():
        return FileResponse(FRONTEND / "index.html")
