"""Five-signal SQL history reduction and frozen raw pagination; legacy reads stay intact."""

import json
import math
import uuid

from fastapi import HTTPException

from evdt.monitoring.analysis_views import scope
from evdt.monitoring.asset_catalog import for_policy
from evdt.monitoring.repository import connection

POINT_LIMIT = 5000
BUCKET_LIMIT = 5000

# All interpolation is named database parameters. No request value becomes SQL.
SELECTED = """WITH selected AS (
 SELECT h.*, requested.period_s,
   COALESCE(h.sample->>'quality','good') AS quality,
   (h.sample->>'value')::double precision AS numeric_value,
   LEAST(%(buckets)s-1,GREATEST(0,floor((h.sim_time_s-%(start)s)/%(width)s)))::integer AS bucket
 FROM m_sample_history h
 JOIN unnest(%(signals)s::text[],%(periods)s::double precision[])
   AS requested(signal_id,period_s) USING(signal_id)
 WHERE h.run_id=%(run)s AND h.asset_id=%(asset)s AND h.dashboard_revision<=%(revision)s
   AND h.sim_time_s BETWEEN %(start)s AND %(end)s
), previous AS (
 SELECT *,lag(sim_time_s) OVER w AS previous_time,
   lag(quality) OVER w AS previous_quality,lag(numeric_value) OVER w AS previous_value
 FROM selected WINDOW w AS (
   PARTITION BY signal_id,source_id,source_epoch ORDER BY sim_time_s,event_id)
), marked AS (
 SELECT *, CASE WHEN quality<>'good' OR numeric_value IS NULL
   OR previous_quality IS DISTINCT FROM 'good' OR previous_value IS NULL
   OR previous_time IS NULL
   OR sim_time_s-previous_time>period_s+GREATEST(1e-9,period_s*1e-6)
   THEN 1 ELSE 0 END AS split
 FROM previous
), segmented AS (
 SELECT *,sum(split) OVER (
   PARTITION BY signal_id,source_id,source_epoch ORDER BY sim_time_s,event_id
   ROWS UNBOUNDED PRECEDING) AS segment
 FROM marked
) """

POINTS_SQL = (
    SELECTED
    + """, ranked AS (
 SELECT *,
 row_number() OVER (PARTITION BY signal_id,source_id,source_epoch,segment,bucket
   ORDER BY sim_time_s,event_id) AS first_rank,
 row_number() OVER (PARTITION BY signal_id,source_id,source_epoch,segment,bucket
   ORDER BY sim_time_s DESC,event_id DESC) AS last_rank,
 row_number() OVER (PARTITION BY signal_id,source_id,source_epoch,segment,bucket
   ORDER BY numeric_value,sim_time_s,event_id) AS minimum_rank,
 row_number() OVER (PARTITION BY signal_id,source_id,source_epoch,segment,bucket
   ORDER BY numeric_value DESC,sim_time_s,event_id) AS maximum_rank
 FROM segmented WHERE quality='good' AND numeric_value IS NOT NULL
)
SELECT signal_id,event_id,sim_time_s,observed_at,received_at,source_id,source_epoch,stream_seq,sample,
 source_id||':'||source_epoch::text||':'||segment::text AS segment_id
FROM ranked WHERE first_rank=1 OR last_rank=1 OR minimum_rank=1 OR maximum_rank=1
ORDER BY signal_id,sim_time_s,event_id LIMIT %(point_limit)s
"""
)

SUMMARY_SQL = (
    SELECTED
    + """
SELECT signal_id,bucket,count(*) AS count,
 count(*) FILTER(WHERE quality='good' AND numeric_value IS NOT NULL) AS good,
 count(*) FILTER(WHERE quality='missing') AS missing,
 count(*) FILTER(WHERE quality='invalid') AS invalid,
 min(numeric_value) FILTER(WHERE quality='good') AS minimum,
 max(numeric_value) FILTER(WHERE quality='good') AS maximum,
 avg(numeric_value) FILTER(WHERE quality='good') AS mean,
 min(sim_time_s) FILTER(WHERE quality='good') AS first_good_s,
 max(sim_time_s) FILTER(WHERE quality='good') AS last_good_s,
 count(*) FILTER(WHERE split=1 AND previous_time IS NOT NULL) AS continuity_breaks
FROM segmented GROUP BY signal_id,bucket ORDER BY signal_id,bucket
"""
)


def validate_window(signals, start, end):
    if (
        not isinstance(signals, (list, tuple))
        or not 1 <= len(signals) <= 5
        or any(not isinstance(s, str) or not 1 <= len(s) <= 128 for s in signals)
        or len(set(signals)) != len(signals)
    ):
        raise HTTPException(422, "Choose one to five distinct registered signals")
    if (
        any(
            isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
            for t in (start, end)
        )
        or not 0 <= start <= end <= 1e12
        or end - start > 86400
    ):
        raise HTTPException(422, "Choose an ordered window of at most one simulation day")


def selected_scope(conn, asset, signals, start, end, as_of):
    run, revision = scope(conn, asset, as_of)
    definition = conn.execute(
        "SELECT policy,policy_sha256 FROM m_run_definitions WHERE run_id=%s", (run,)
    ).fetchone()
    catalog = for_policy(definition["policy"], asset)
    if any(signal not in catalog["signals"] for signal in signals):
        raise HTTPException(422, "Signal is not registered for this asset")
    owners = {
        signal: {"source_id": source["source_id"], **timing}
        for source in definition["policy"]["sources"]
        if source["asset_id"] == asset
        for signal, timing in source["expected"]["signals"].items()
        if signal in signals
    }
    if set(owners) != set(signals):
        raise HTTPException(409, "Registered signal cadence unavailable")
    return {
        "mode": "simulation",
        "run_id": run,
        "asset_id": asset,
        "as_of": as_of,
        "from_s": start,
        "to_s": end,
        "time_basis": "simulation seconds",
        "policy_sha256": definition["policy_sha256"],
        "catalog_version": catalog["catalog_version"],
        "signals": {signal: catalog["signals"][signal] for signal in signals},
        "timings": owners,
    }, revision


def parameters(metadata, revision, count, width):
    return {
        "run": metadata["run_id"],
        "asset": metadata["asset_id"],
        "revision": revision,
        "signals": list(metadata["signals"]),
        "periods": [metadata["timings"][signal]["period_s"] for signal in metadata["signals"]],
        "start": metadata["from_s"],
        "end": metadata["to_s"],
        "buckets": count,
        "width": width,
    }


def buckets(rows, signals, start, end, count, width):
    indexed = {(row["signal_id"], row["bucket"]): row for row in rows}
    result = []
    for signal in signals:
        for index in range(count):
            row = indexed.get(
                (signal, index),
                {
                    "count": 0,
                    "good": 0,
                    "missing": 0,
                    "invalid": 0,
                    "minimum": None,
                    "maximum": None,
                    "mean": None,
                    "first_good_s": None,
                    "last_good_s": None,
                    "continuity_breaks": 0,
                },
            )
            if row["count"] != row["good"] + row["missing"] + row["invalid"] or any(
                row[key] is not None and not math.isfinite(row[key])
                for key in ("minimum", "maximum", "mean")
            ):
                raise HTTPException(409, "Historical sample quality is inconsistent")
            result.append(
                {
                    **{
                        key: value
                        for key, value in row.items()
                        if key not in ("bucket", "signal_id")
                    },
                    "signal_id": signal,
                    "from_s": start + index * width,
                    "to_s": min(end, start + (index + 1) * width),
                }
            )
    return result


def plot(asset, signals, start, end, as_of, point_limit=POINT_LIMIT):
    validate_window(signals, start, end)
    if (
        isinstance(point_limit, bool)
        or not isinstance(point_limit, int)
        or not 4 * len(signals) <= point_limit <= POINT_LIMIT
    ):
        raise HTTPException(422, "Use a total point budget between four per signal and 5000")
    count = min(point_limit // (4 * len(signals)), max(1, math.ceil((end - start) / 1e-6)))
    width = (end - start) / count if end > start else 1.0
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5000ms'")
        metadata, revision = selected_scope(conn, asset, signals, start, end, as_of)
        params = parameters(metadata, revision, count, width)
        points = conn.execute(POINTS_SQL, {**params, "point_limit": point_limit + 1}).fetchall()
        if len(points) > point_limit:
            raise HTTPException(
                413,
                "Too many separate data segments for this point budget; choose fewer signals or a shorter window",
            )
        summary = buckets(
            conn.execute(SUMMARY_SQL, params).fetchall(), signals, start, end, count, width
        )
    totals = {
        key: sum(row[key] for row in summary)
        for key in ("count", "good", "missing", "invalid", "continuity_breaks")
    }
    return {
        "metadata": {
            **metadata,
            "plot_version": "history-plot/1.0.0",
            "resolution": "first/minimum/maximum/last per bucket and continuous segment",
            "point_limit": point_limit,
            "point_count": len(points),
            "bucket_s": width,
            "quality_counts": totals,
            "interpolated": False,
        },
        "items": points,
        "buckets": summary,
    }


def summary(asset, signals, start, end, as_of, bucket_s):
    validate_window(signals, start, end)
    if (
        isinstance(bucket_s, bool)
        or not isinstance(bucket_s, (float, int))
        or not math.isfinite(bucket_s)
        or not 0 < bucket_s <= 86400
        or (end - start) / bucket_s > BUCKET_LIMIT // len(signals)
    ):
        raise HTTPException(422, "Use positive buckets with at most 5000 total signal intervals")
    count = max(1, math.ceil((end - start) / bucket_s))
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5000ms'")
        metadata, revision = selected_scope(conn, asset, signals, start, end, as_of)
        rows = conn.execute(SUMMARY_SQL, parameters(metadata, revision, count, bucket_s)).fetchall()
        items = buckets(rows, signals, start, end, count, bucket_s)
    return {
        "metadata": {
            **metadata,
            "summary_version": "history-summary/1.0.0",
            "resolution": "database bucket summary",
            "bucket_s": bucket_s,
        },
        "items": items,
    }


def export_cursor(after, as_of, signals, start, end, limit):
    if after is None:
        return -1.0, "00000000-0000-0000-0000-000000000000", ""
    try:
        value = json.loads(after)
        if (
            not isinstance(value, list)
            or len(value) != 9
            or value[:6] != [as_of, "raw-export/1.0.0", signals, start, end, limit]
        ):
            raise ValueError
        lower, event, signal = value[6:]
        if (
            isinstance(lower, bool)
            or not isinstance(lower, (int, float))
            or not math.isfinite(lower)
            or not start <= lower <= end
            or not isinstance(event, str)
            or str(uuid.UUID(event)) != event
            or signal not in signals
        ):
            raise ValueError
        return float(lower), event, signal
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise HTTPException(422, "Export continuation does not match this frozen query") from None


def export_page(asset, signals, start, end, as_of, after=None, limit=1000):
    validate_window(signals, start, end)
    signals = sorted(signals)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise HTTPException(422, "Use a raw export page size between 1 and 1000")
    lower_time, lower_event, lower_signal = export_cursor(after, as_of, signals, start, end, limit)
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5000ms'")
        metadata, revision = selected_scope(conn, asset, signals, start, end, as_of)
        rows = conn.execute(
            """SELECT h.*,o.payload->>'manifest_sha256' AS manifest_sha256,
          o.payload->>'schema_version' AS schema_version,o.payload->>'catalog_version' AS catalog_version
          FROM m_sample_history h JOIN m_observations o USING(run_id,event_id)
          WHERE h.run_id=%s AND h.asset_id=%s AND h.signal_id=ANY(%s)
            AND h.dashboard_revision<=%s AND h.sim_time_s BETWEEN %s AND %s
            AND (h.sim_time_s,h.event_id,h.signal_id)>(%s,%s,%s)
          ORDER BY h.sim_time_s,h.event_id,h.signal_id LIMIT %s""",
            (
                metadata["run_id"],
                asset,
                signals,
                revision,
                start,
                end,
                lower_time,
                lower_event,
                lower_signal,
                limit + 1,
            ),
        ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    next_page = (
        json.dumps(
            [
                as_of,
                "raw-export/1.0.0",
                signals,
                start,
                end,
                limit,
                rows[-1]["sim_time_s"],
                str(rows[-1]["event_id"]),
                rows[-1]["signal_id"],
            ],
            separators=(",", ":"),
        )
        if more
        else None
    )
    return {
        "metadata": {
            **metadata,
            "export_version": "1.1.0",
            "resolution": "raw",
            "page_limit": limit,
            "row_count": len(rows),
        },
        "items": rows,
        "next": next_page,
    }
