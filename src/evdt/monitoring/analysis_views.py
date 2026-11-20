"""Scoped history/trajectory/export/event/health read models with explicit bounds."""

import csv
import io
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from evdt.monitoring.asset_catalog import for_asset
from evdt.monitoring.dashboard import parse_cursor
from evdt.monitoring.metrics import queue_diagnostics
from evdt.monitoring.repository import connection


def scope(conn, asset, as_of):
    run = os.environ["EVDT_RUN_ID"]
    revision = parse_cursor(as_of, run, asset)
    head = conn.execute(
        "SELECT revision FROM m_dashboard_heads WHERE run_id=%s AND asset_id=%s", (run, asset)
    ).fetchone()
    if revision > (head["revision"] if head else 0):
        raise HTTPException(409, "Snapshot revision is ahead of this run")
    return run, revision


def raw_query(asset, signals, start, end, as_of):
    if not signals or len(signals) > 4 or len(set(signals)) != len(signals):
        raise HTTPException(422, "Choose one to four distinct registered signals")
    if (
        not all(math.isfinite(v) for v in (start, end))
        or not 0 <= start <= end
        or end - start > 86400
    ):
        raise HTTPException(422, "Choose an ordered window of at most one simulation day")
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5000ms'")
        run, revision = scope(conn, asset, as_of)
        catalog = for_asset(conn, run, asset)
        if any(s not in catalog["signals"] for s in signals):
            raise HTTPException(422, "Signal is not registered for this asset")
        rows = conn.execute(
            """SELECT h.*,o.payload->>'manifest_sha256' AS manifest_sha256,
              o.payload->>'schema_version' AS schema_version,o.payload->>'catalog_version' AS catalog_version
              FROM m_sample_history h JOIN m_observations o USING(run_id,event_id)
              WHERE h.run_id=%s AND h.asset_id=%s AND h.signal_id=ANY(%s) AND h.dashboard_revision<=%s
                AND h.sim_time_s BETWEEN %s AND %s ORDER BY h.sim_time_s,h.event_id,h.signal_id LIMIT 10001""",
            (run, asset, signals, revision, start, end),
        ).fetchall()
        definition = conn.execute(
            "SELECT policy_sha256 FROM m_run_definitions WHERE run_id=%s", (run,)
        ).fetchone()
    if len(rows) > 10000:
        raise HTTPException(
            413,
            "Query exceeds 10000 raw scalar readings; shorten the window or choose fewer signals",
        )
    metadata = {
        "export_version": "1.0.0",
        "mode": "simulation",
        "run_id": run,
        "asset_id": asset,
        "as_of": as_of,
        "from_s": start,
        "to_s": end,
        "time_basis": "simulation seconds",
        "policy_sha256": definition["policy_sha256"],
        "resolution": "raw",
        "signals": {s: catalog["signals"][s] for s in signals},
        "row_limit": 10000,
    }
    return metadata, rows


def summarize(rows, start, end, bucket_s, signals):
    if not math.isfinite(bucket_s) or bucket_s <= 0 or (end - start) / bucket_s > 2000:
        raise HTTPException(422, "Use positive buckets with at most 2000 intervals")
    buckets = defaultdict(list)
    for row in rows:
        buckets[
            (
                row["signal_id"],
                min(
                    int((row["sim_time_s"] - start) // bucket_s),
                    max(0, math.ceil((end - start) / bucket_s) - 1),
                ),
            )
        ].append(row["sample"])
    results = []
    for signal in signals:
        for index in range(max(1, math.ceil((end - start) / bucket_s))):
            samples = buckets[(signal, index)]
            good = [
                s["value"]
                for s in samples
                if s.get("quality", "good") == "good" and s["value"] is not None
            ]
            results.append(
                {
                    "signal_id": signal,
                    "from_s": start + index * bucket_s,
                    "to_s": min(end, start + (index + 1) * bucket_s),
                    "good": len(good),
                    "missing": sum(s.get("quality") == "missing" for s in samples),
                    "invalid": sum(s.get("quality") == "invalid" for s in samples),
                    "minimum": min(good) if good else None,
                    "maximum": max(good) if good else None,
                    "mean": math.fsum(good) / len(good) if good else None,
                }
            )
    return results


def csv_text(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def export_csv(metadata, rows):
    fields = [
        "record_type",
        "metadata_json",
        "event_id",
        "signal_id",
        "unit",
        "sim_time_s",
        "observed_at",
        "received_at",
        "source_id",
        "source_epoch",
        "stream_seq",
        "quality",
        "value",
        "raw_value",
        "reason",
        "provenance",
        "manifest_sha256",
        "schema_version",
        "catalog_version",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerow(
        {
            "record_type": "metadata",
            "metadata_json": json.dumps(
                jsonable_encoder(metadata), allow_nan=False, separators=(",", ":")
            ),
        }
    )
    for row in jsonable_encoder(rows):
        sample = row["sample"]
        result = {k: row[k] for k in fields if k in row}
        result.update(
            record_type="sample",
            unit=metadata["signals"][row["signal_id"]]["unit"],
            **{k: sample.get(k) for k in ("quality", "value", "raw_value", "reason", "provenance")},
        )
        writer.writerow({k: csv_text(v) for k, v in result.items()})
    return output.getvalue()


def trajectory(asset, as_of, start, end, after, limit, *, since=None):
    if start > end or end - start > 86400:
        raise HTTPException(422, "Use an ordered window of at most one simulation day")
    lower_time, lower_event = -1.0, "00000000-0000-0000-0000-000000000000"
    query = [as_of, "trajectory", start, end, limit]
    if since is not None:
        query.extend(["since", since])
    if after:
        try:
            saved = json.loads(after)
            if len(saved) != len(query) + 2 or saved[:-2] != query:
                raise ValueError
            lower_time, lower_event = float(saved[-2]), saved[-1]
            import uuid

            uuid.UUID(lower_event)
            if not start <= lower_time <= end:
                raise ValueError
        except (ValueError, TypeError, IndexError, AttributeError):
            raise HTTPException(422, "Trajectory continuation does not match query") from None
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5000ms'")
        run, revision = scope(conn, asset, as_of)
        lower_revision = parse_cursor(since, run, asset) if since is not None else 0
        if lower_revision > revision:
            raise HTTPException(409, "Trajectory start cursor is ahead of the snapshot")
        rows = conn.execute(
            """SELECT event_id,source_id,source_epoch,stream_seq,sim_time_s,observed_at,received_at,payload->'samples' AS samples
            FROM m_observations o WHERE run_id=%s AND asset_id=%s
              AND dashboard_revision>%s AND dashboard_revision<=%s
              AND sim_time_s BETWEEN %s AND %s AND (sim_time_s,event_id)>(%s,%s)
              AND EXISTS(SELECT 1 FROM jsonb_array_elements(o.payload->'samples') s WHERE s->>'signal_id' IN ('location.latitude','location.longitude'))
            ORDER BY sim_time_s,event_id LIMIT %s""",
            (run, asset, lower_revision, revision, start, end, lower_time, lower_event, limit + 1),
        ).fetchall()
        policy = conn.execute(
            "SELECT policy FROM m_run_definitions WHERE run_id=%s", (run,)
        ).fetchone()["policy"]
    more = len(rows) > limit
    rows = rows[:limit]
    for row in rows:
        samples = {s["signal_id"]: s for s in row.pop("samples")}
        lat, lon = [samples.get(s) for s in ("location.latitude", "location.longitude")]
        valid = all(
            s and s.get("quality", "good") == "good" and isinstance(s["value"], (int, float))
            for s in (lat, lon)
        )
        row.update(
            coordinates=[lon["value"], lat["value"]] if valid else None,
            quality="good" if valid else "unavailable",
            reason=None if valid else "Incomplete, invalid, or missing coordinate pair",
        )
    periods = [
        spec["freshness_s"]
        for source in policy["sources"]
        if source["asset_id"] == asset
        for signal, spec in source["expected"]["signals"].items()
        if signal in ("location.latitude", "location.longitude")
    ]
    return {
        "mode": "simulation",
        "run_id": run,
        "asset_id": asset,
        "as_of": as_of,
        "items": rows,
        "max_gap_s": min(periods) if periods else 0,
        "next": json.dumps(
            [
                *query,
                rows[-1]["sim_time_s"],
                str(rows[-1]["event_id"]),
            ]
        )
        if more
        else None,
    }


def events(asset, as_of, before, limit):
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        run, revision = scope(conn, asset, as_of)
        rows = conn.execute(
            "SELECT revision,kind,occurred_at,detail FROM m_dashboard_events WHERE run_id=%s AND asset_id=%s AND revision<=%s AND revision<%s ORDER BY revision DESC LIMIT %s",
            (run, asset, revision, before or revision + 1, limit + 1),
        ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "run_id": run,
        "asset_id": asset,
        "as_of": as_of,
        "items": rows,
        "next": rows[-1]["revision"] if more else None,
        "coverage": "Events recorded since migration 004; browser connection events are page-local",
    }


def internal_metrics(service, asset=None):
    try:
        if service == "sender":
            import re

            service = json.loads(os.environ.get("SENDER_SERVICES", '{"bus-001":"sender"}')).get(
                asset
            )
            if not isinstance(service, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service):
                raise ValueError
        token = Path(os.environ["METRICS_TOKEN_FILE"]).read_text().strip()
        with urlopen(
            Request(f"http://{service}:9100/metrics", headers={"Authorization": "Bearer " + token}),
            timeout=1,
        ) as response:
            value = json.loads(response.read(16385))
        if (
            value["run_id"] != os.environ["EVDT_RUN_ID"]
            or (asset and value.get("asset_id") != asset)
            or (
                datetime.now(timezone.utc) - datetime.fromisoformat(value["observed_at"])
            ).total_seconds()
            > 5
        ):
            raise ValueError
        return {"available": True, **value}
    except (OSError, ValueError, KeyError):
        return {"available": False, "reason": "Metrics unavailable for this asset or service"}


def health(asset):
    run = os.environ["EVDT_RUN_ID"]
    with connection() as conn:
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        counts = conn.execute(
            "SELECT count(*) AS accepted_events,count(*) FILTER(WHERE received_at>=clock_timestamp()-interval '30 seconds') AS accepted_last_30_s FROM m_observations WHERE run_id=%s AND asset_id=%s",
            (run, asset),
        ).fetchone()
        rejected = conn.execute(
            "SELECT reason,count(*) AS count FROM m_scoped_rejections WHERE run_id=%s AND asset_id=%s GROUP BY reason ORDER BY reason",
            (run, asset),
        ).fetchall()
    sender = internal_metrics("sender", asset)
    return {
        "mode": "simulation",
        "run_id": run,
        "asset_id": asset,
        "as_of": now,
        "history_available": True,
        **counts,
        "ingest_events_per_s_30s": counts["accepted_last_30_s"] / 30,
        "rejections": rejected,
        "sender": sender,
        "receiver": internal_metrics("ingest"),
        **queue_diagnostics(sender),
    }
