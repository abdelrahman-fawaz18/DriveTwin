"""Read-only local qualification API, not an operational dashboard or control API."""

from typing import Annotated

import uvicorn
from fastapi import FastAPI, Query
from psycopg.types.json import Jsonb

from evdt.config import run_id
from evdt.contracts import Contract
from evdt.repository import connection

app = FastAPI(title="EV-DT Phase 0 data-flow qualification", version="0.1.0")


@app.get("/health/ready")
def ready():
    with connection() as conn:
        conn.execute("SELECT version FROM schema_migrations WHERE version=1").fetchone()
    return {"ready": True, "mode": "qualification"}


@app.get("/v1/catalog")
def catalog():
    return Contract().catalog


@app.get("/v1/observations")
def history(
    asset_id: str, limit: Annotated[int, Query(ge=1, le=500)] = 100, signal_id: str | None = None
):
    with connection() as conn:
        rows = conn.execute(
            """SELECT event_id, observed_at, received_at, persisted_at, payload
               FROM observations WHERE run_id=%s AND asset_id=%s
               AND (%s::jsonb IS NULL OR payload @> %s::jsonb)
               ORDER BY observed_at, event_id LIMIT %s""",
            (
                run_id(),
                asset_id,
                Jsonb({"samples": [{"signal_id": signal_id}]}) if signal_id else None,
                Jsonb({"samples": [{"signal_id": signal_id}]}) if signal_id else None,
                limit,
            ),
        ).fetchall()
    return {"items": rows, "limit": limit, "mode": "qualification", "run_id": run_id()}


@app.get("/qualification/status")
def status():
    with connection() as conn:
        counters = conn.execute(
            "SELECT * FROM ingest_counters WHERE run_id=%s", (run_id(),)
        ).fetchone()
        reasons = conn.execute(
            "SELECT reason,count(*) AS count FROM quarantine WHERE run_id=%s GROUP BY reason ORDER BY reason",
            (run_id(),),
        ).fetchall()
    return {"counters": counters, "reasons": reasons}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
