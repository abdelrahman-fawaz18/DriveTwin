"""Actual database/API assertions for snapshot and stable history boundaries."""

import json
import os
import uuid
from urllib.parse import urlencode

import psycopg
from psycopg.types.json import Jsonb

from evdt.monitoring.dashboard import snapshot
from evdt.monitoring.probe_support import api, require
from evdt.monitoring.repository import connection


def main():
    run = os.environ["EVDT_RUN_ID"]
    status, snap = api("/v1/snapshot?asset_id=bus-001")
    require(status == 200 and len(snap["items"]) == 20, "Snapshot signal coverage")
    require(snap["cursor"].startswith(run + ":bus-001:"), "Snapshot cursor identity")
    heading = next(i for i in snap["items"] if i["signal_id"] == "motion.heading")
    require(
        heading["definition"]["unit"] == "rad"
        and 0 <= heading["observation"]["sample"]["value"] <= 6.283185307179587,
        "Stored heading is not in catalog radians",
    )
    for path in ("/v1/snapshot", "/v1/stream"):
        require(api(path + "?asset_id=bus-001", credential=None)[0] == 401, "Anonymous read")
        require(api(path + "?asset_id=bus-002", credential="bus-001")[0] == 403, "Asset escape")
    query = dict(
        asset_id="bus-001",
        signal_id="motion.speed",
        as_of=snap["cursor"],
        from_s=0,
        to_s=1000,
        limit=2,
    )
    points, pages = [], 0
    while True:
        code, page = api("/v1/history?" + urlencode(query))
        require(code == 200 and page["resolution"] == "raw", "History query failed")
        points.extend(page["items"])
        pages += 1
        if not page["next"]:
            break
        query["after"] = page["next"]
    require(len(points) == 18 and pages == 9, "Paginated history missing/duplicated rows")
    require(len({p["event_id"] for p in points}) == 18, "Duplicate history point")
    require(
        [p["sim_time_s"] for p in points] == sorted(p["sim_time_s"] for p in points),
        "History order",
    )
    for changed, code in [
        ({"asset_id": "bus-002"}, 403),
        ({"as_of": run + ":bus-002:1"}, 409),
        ({"as_of": run + ":bus-001:9007199254740991"}, 409),
        ({"signal_id": "unknown"}, 404),
        ({"to_s": 90000}, 422),
        ({"from_s": "nan"}, 422),
        ({"after": "broken"}, 422),
        ({"limit": 1001}, 422),
    ]:
        require(
            api(
                "/v1/history?" + urlencode({**query, "after": "", **changed}), credential="bus-001"
            )[0]
            == code,
            "History boundary escaped",
        )
    # Hold a real uncommitted observation/derived revision while another connection reads.
    # Roll it back: this is a transaction test, not a registered producer observation.
    with connection() as writer:
        row = writer.execute(
            "SELECT * FROM m_observations WHERE run_id=%s AND asset_id='bus-001' ORDER BY sim_time_s DESC LIMIT 1",
            (run,),
        ).fetchone()
        before = snapshot("bus-001")
        fake = dict(row["payload"], event_id=str(uuid.uuid4()), stream_seq=999999, sim_time_s=999)
        writer.execute(
            """INSERT INTO m_observations
            (run_id,event_id,asset_id,source_id,source_epoch,session_id,stream_seq,sim_time_s,
             observed_at,received_at,wire_sha256,raw_payload,payload)
            VALUES(%s,%s,'bus-001',%s,%s,%s,999999,999,%s,%s,%s,%s,%s)""",
            (
                run,
                fake["event_id"],
                row["source_id"],
                row["source_epoch"],
                row["session_id"],
                row["observed_at"],
                row["received_at"],
                row["wire_sha256"],
                row["raw_payload"],
                Jsonb(fake),
            ),
        )
        during = snapshot("bus-001")
        require(during["cursor"] == before["cursor"], "Uncommitted revision leaked")
        require(
            all(i["observation"]["sim_time_s"] < 999 for i in during["items"]),
            "Uncommitted value leaked",
        )
        # A second asset writer cannot allocate a later visible cursor before this one ends.
        with connection() as second:
            second.execute("SET LOCAL lock_timeout='150ms'")
            try:
                second.execute("SELECT m_dashboard_next(%s,'bus-001')", (run,))
            except psycopg.errors.LockNotAvailable:
                second.rollback()
            else:
                raise RuntimeError("Asset revision did not serialize concurrent writers")
        writer.rollback()
    require(snapshot("bus-001")["cursor"] == before["cursor"], "Rollback advanced cursor")
    with connection() as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM m_sample_history WHERE run_id=%s AND asset_id='bus-001'",
            (run,),
        ).fetchone()["n"]
    require(count == 360, "Projection rollback/accounting failed")
    print(
        json.dumps(
            {
                "passed": True,
                "checks": [
                    "consistent 20-signal snapshot",
                    "scoped authentication/cursors",
                    "18 raw readings across nine pages",
                    "query bounds and malformed continuation",
                    "uncommitted data invisible",
                    "concurrent writer serialization",
                    "rollback restores revision and 360-sample projection",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
