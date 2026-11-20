"""Independent replay application: bounded source reads and browser-local playback."""

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from evdt.monitoring import replay_repository as repository
from evdt.monitoring.http_readers import reader
from evdt.monitoring.repository import connection

app = FastAPI(title="EV-DT isolated recording replay", version="1.0.0")


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=64)
    source_run_id: str = Field(min_length=36, max_length=36)
    from_s: float = Field(ge=0, le=1e12, allow_inf_nan=False)
    to_s: float = Field(ge=0, le=1e12, allow_inf_nan=False)


@app.middleware("http")
async def isolation_headers(request: Request, call_next):
    if request.method == "POST" and (
        not request.headers.get("content-length", "").isdigit()
        or int(request.headers["content-length"]) > 4096
    ):
        return Response("Replay request too large", status_code=413)
    response = await call_next(request)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        }
    )
    return response


@app.get("/health/ready")
def ready():
    with connection() as conn:
        conn.execute("SELECT run_id FROM m_replay_definitions LIMIT 1").fetchone()
    return {"ready": True, "mode": "replay", "dispatch": False}


@app.get("/v1/replay/sources")
def sources(assets=Depends(reader)):
    return repository.sources(assets)


@app.post("/v1/replay/sessions")
def create_session(request: SessionRequest, assets=Depends(reader)):
    if request.asset_id not in assets:
        raise HTTPException(403, "Asset outside replay reader scope")
    raw = repository.make_package(
        request.asset_id, request.from_s, request.to_s, request.source_run_id
    )
    return Response(
        raw, media_type="application/json", headers={"X-Content-SHA256": repository.digest(raw)}
    )


@app.get("/monitoring", include_in_schema=False)
def open_monitoring():
    target = os.environ.get("MONITORING_BROWSER_URL")
    if not target:
        raise HTTPException(503, "Monitoring navigation is not configured")
    return RedirectResponse(target)


FRONTEND = Path(os.environ.get("DASHBOARD_FILES", "/app/dashboard"))
if FRONTEND.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="replay-assets")

    @app.get("/", include_in_schema=False)
    def replay_page():
        return FileResponse(FRONTEND / "replay.html")
