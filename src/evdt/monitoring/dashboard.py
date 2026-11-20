"""Consistent read models and scoped, bounded history for the product dashboard."""

import json
import os
import re
import uuid

from fastapi import HTTPException

from evdt.monitoring.asset_catalog import for_asset, for_policy
from evdt.monitoring.repository import connection
from evdt.monitoring.run_policy import presentation

MAX_REVISION = 9007199254740991


def cursor(run, asset, revision):
    return f"{run}:{asset}:{revision}"


def parse_cursor(value, run, asset):
    try:
        stored_run, stored_asset, number = value.split(":")
        revision = int(number)
        if stored_run != run or stored_asset != asset or not 0 <= revision <= MAX_REVISION:
            raise ValueError
        if str(revision) != number:
            raise ValueError
        return revision
    except (ValueError, AttributeError):
        raise HTTPException(409, "Cursor scope changed; replace with a new snapshot") from None


def read_items(conn, run, asset, now):
    rows = conn.execute(
        """SELECT c.*, o.received_at, CASE WHEN c.signal_id='motion.distance'
          THEN o.payload->'annotations'->>'trip_progress' END AS trip_progress_raw
          FROM m_current c JOIN m_observations o USING(run_id,event_id)
          WHERE c.run_id=%s AND c.asset_id=%s""",
        (run, asset),
    ).fetchall()
    clocks = conn.execute(
        "SELECT * FROM m_source_clocks WHERE run_id=%s AND asset_id=%s", (run, asset)
    ).fetchall()
    policy = conn.execute(
        "SELECT policy FROM m_run_definitions WHERE run_id=%s", (run,)
    ).fetchone()["policy"]
    catalog = for_policy(policy, asset)
    owners = {
        signal: source
        for source in policy["sources"]
        if source["asset_id"] == asset
        for signal in source["expected"]["signals"]
    }
    by_source = {row["source_id"]: row for row in clocks}
    by_signal = {row["signal_id"]: row for row in rows}
    result = []
    progress = None
    for name, spec in catalog["signals"].items():
        row, owner = by_signal.get(name), owners.get(name)
        if owner is None:
            continue
        timing = owner["expected"]["signals"][name]
        state = presentation(
            row["sample"] if row else None,
            row["sim_time_s"] if row else None,
            row["observed_at"] if row else None,
            by_source.get(owner["source_id"]),
            timing,
            now,
            owner["expected"]["heartbeat_deadline_s"],
        )
        if row:
            raw = row.pop("trip_progress_raw", None)
            if (
                name == "motion.distance"
                and state["quality"] == "good"
                and state["freshness"] in ("fresh", "paused")
            ):
                progress = parse_trip_progress(raw)
        result.append(
            {
                "signal_id": name,
                **state,
                "source_id": owner["source_id"],
                "registered_timing": timing,
                "observation": row,
                "definition": {
                    **spec,
                    "sample_period_s": timing["period_s"],
                    "freshness_s": timing["freshness_s"],
                },
            }
        )
    return result, progress


def parse_trip_progress(raw):
    if not isinstance(raw, str) or len(raw) > 256:
        return None
    try:
        progress = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(progress, dict) or set(progress) != {
        "remaining_m",
        "next_stop_id",
        "at_passenger_stop",
    }:
        return None
    remaining, stop_id = progress["remaining_m"], progress["next_stop_id"]
    if (
        remaining is not None
        and (type(remaining) not in (int, float) or not 0 <= remaining <= 10_000_000)
        or stop_id is not None
        and (not isinstance(stop_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", stop_id))
        or type(progress["at_passenger_stop"]) is not bool
        or (progress["at_passenger_stop"] and stop_id is None)
    ):
        return None
    return progress


def snapshot(asset):
    run = os.environ["EVDT_RUN_ID"]
    with connection() as conn:
        # The head and every read model use the SAME committed database view.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        head = conn.execute(
            "SELECT revision FROM m_dashboard_heads WHERE run_id=%s AND asset_id=%s", (run, asset)
        ).fetchone()
        items, progress = read_items(conn, run, asset, now)
    return {
        "version": "1.0.0",
        "mode": "simulation",
        "run_id": run,
        "asset_id": asset,
        "cursor": cursor(run, asset, head["revision"] if head else 0),
        "as_of": now.isoformat(),
        "items": items,
        **({"trip_progress": progress} if progress is not None else {}),
    }


def history(asset, signal, start, end, as_of, after, limit):
    run = os.environ["EVDT_RUN_ID"]
    if start > end or end - start > 86400:
        raise HTTPException(422, "Use an ordered window of at most 86400 simulation seconds")
    revision = parse_cursor(as_of, run, asset)
    lower_time, lower_event = -1.0, uuid.UUID(int=0)
    if after:
        try:
            saved = json.loads(after)
            if saved[:5] != [as_of, signal, start, end, limit] or len(saved) != 7:
                raise ValueError
            lower_time, lower_event = float(saved[5]), uuid.UUID(saved[6])
            if not start <= lower_time <= end:
                raise ValueError
        except (ValueError, TypeError, IndexError):
            raise HTTPException(422, "History continuation does not match this query") from None
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if signal not in for_asset(conn, run, asset)["signals"]:
            raise HTTPException(404, "Signal is not registered for this asset")
        head = conn.execute(
            "SELECT revision FROM m_dashboard_heads WHERE run_id=%s AND asset_id=%s", (run, asset)
        ).fetchone()
        if revision > (head["revision"] if head else 0):
            raise HTTPException(409, "Snapshot revision is ahead of this run")
        rows = conn.execute(
            """SELECT event_id,sim_time_s,observed_at,received_at,source_id,source_epoch,
                      stream_seq,sample FROM m_sample_history
               WHERE run_id=%s AND asset_id=%s AND signal_id=%s AND dashboard_revision<=%s
                 AND sim_time_s BETWEEN %s AND %s AND (sim_time_s,event_id)>(%s,%s)
               ORDER BY sim_time_s,event_id LIMIT %s""",
            (run, asset, signal, revision, start, end, lower_time, lower_event, limit + 1),
        ).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    continuation = (
        json.dumps(
            [as_of, signal, start, end, limit, rows[-1]["sim_time_s"], str(rows[-1]["event_id"])]
        )
        if more
        else None
    )
    return {
        "mode": "simulation",
        "run_id": run,
        "asset_id": asset,
        "signal_id": signal,
        "as_of": as_of,
        "from_s": start,
        "to_s": end,
        "resolution": "raw",
        "items": rows,
        "next": continuation,
    }
