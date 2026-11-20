"""Actual database/API map, export, event and operational boundary assertions."""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from evdt.monitoring.probe_support import api, require
from evdt.monitoring.repository import connection


def main():
    _, snap = api("/v1/snapshot?asset_id=bus-001")
    run = os.environ["EVDT_RUN_ID"]
    status, context = api("/v1/map-context?asset_id=bus-001")
    require(
        status == 200 and context["run_id"] == run and context["asset_id"] == "bus-001", "Map scope"
    )
    require(api("/v1/map-context?asset_id=bus-002")[0] == 404, "Unassigned map borrowed context")
    require(
        len([f for f in context["features"] if f["properties"]["layer"] == "route"]) == 1,
        "Planned route absent",
    )
    stations = [f for f in context["features"] if f["properties"]["layer"] == "stations"]
    require(
        len(stations) == 2
        and all(
            f["properties"]["hypothetical"] and f["properties"]["availability"] == "unverified"
            for f in stations
        ),
        "Station assumptions lost",
    )
    for f in context["features"]:
        if f["geometry"]["type"] == "Point":
            lon, lat = f["geometry"]["coordinates"]
            require(-74 < lon < -73 and 45 < lat < 46, "Coordinate order or conversion")
    with connection() as conn:
        row = conn.execute(
            "SELECT raw_context,sha256 FROM m_map_contexts WHERE run_id=%s AND asset_id='bus-001'",
            (run,),
        ).fetchone()
        require(
            hashlib.sha256(bytes(row["raw_context"])).hexdigest() == row["sha256"],
            "Immutable context bytes/hash differ",
        )
    query = dict(asset_id="bus-001", as_of=snap["cursor"], from_s=0, to_s=1000, limit=3)
    points = []
    while True:
        status, result = api("/v1/trajectory?" + urlencode(query))
        require(status == 200, "Trajectory page query")
        points.extend(result["items"])
        if not result["next"]:
            break
        query["after"] = result["next"]
    require(
        len(points) == 18 and len({p["event_id"] for p in points}) == 18,
        "Trajectory pagination count",
    )
    source = json.loads(Path("/source-output/source-report.json").read_text())
    expected = {
        e["body"]["event_id"]: {s["signal_id"]: s["value"] for s in e["body"]["samples"]}
        for e in source["events"]
    }
    for p in points:
        require(
            p["coordinates"]
            == [
                expected[p["event_id"]]["location.longitude"],
                expected[p["event_id"]]["location.latitude"],
            ],
            "Trajectory spliced unrelated coordinates",
        )
    params = dict(asset_id="bus-001", as_of=snap["cursor"], from_s=0, to_s=18)
    query = urlencode({**params, "signals": ["motion.speed", "battery.pack.power"]}, doseq=True)
    status, result = api("/v1/export?" + query)
    require(
        status == 200
        and len(result["items"]) == 36
        and result["metadata"]["signals"]["motion.speed"]["unit"] == "m/s",
        "Raw export metadata/accounting",
    )
    _, summary = api("/v1/summary?" + query + "&bucket_s=5")
    require(
        sum(p["good"] for p in summary["items"]) == 36 and len(summary["items"]) == 8,
        "Summary counts",
    )
    credential = json.loads(Path(os.environ["READERS_FILE"]).read_text())["readers"][0]["token"]
    with urlopen(
        Request(
            "http://api:8000/v1/export?" + query + "&format=csv",
            headers={"Authorization": "Bearer " + credential},
        ),
        timeout=5,
    ) as response:
        records = list(csv.DictReader(io.StringIO(response.read().decode())))
    require(
        records[0]["record_type"] == "metadata" and len(records) == 37,
        "CSV metadata/data accounting",
    )
    require(
        json.loads(records[0]["metadata_json"])["as_of"] == snap["cursor"],
        "CSV snapshot provenance",
    )
    _, events = api(
        "/v1/events?" + urlencode({"asset_id": "bus-001", "as_of": snap["cursor"], "limit": 2})
    )
    require(
        events["items"]
        and all(
            e["kind"] in ("registration", "source-state", "quality", "rejection")
            for e in events["items"]
        ),
        "Recorded events missing",
    )
    for route in (
        "/v1/map-context",
        "/v1/trajectory",
        "/v1/events",
        "/v1/system-health",
        "/v1/export",
        "/v1/summary",
    ):
        extra = (
            "" if route in ("/v1/map-context", "/v1/system-health") else "&as_of=" + snap["cursor"]
        )
        if route in ("/v1/export", "/v1/summary"):
            extra += "&signals=motion.speed"
        require(
            api(route + "?asset_id=bus-001" + extra, credential=None)[0] == 401,
            "Anonymous analysis route",
        )
        require(
            api(route + "?asset_id=bus-002" + extra, credential="bus-001")[0] == 403,
            "Analysis asset escape",
        )
    for suffix in ("&from_s=nan", "&to_s=90000", "&signals=unknown", "&signals=motion.speed"):
        require(api("/v1/export?" + query + suffix)[0] == 422, "Export bound ignored")
    require(api("/v1/summary?" + query + "&bucket_s=0")[0] == 422, "Summary bound ignored")
    _, health = api("/v1/system-health?asset_id=bus-001")
    require(
        health["accepted_events"] == 18
        and health["history_available"]
        and not health["sender"]["available"]
        and health["dropped_records"] is None,
        "Stopped sender fabricated diagnostics",
    )
    print(
        json.dumps(
            {
                "passed": True,
                "checks": [
                    "scoped immutable local map context",
                    "longitude/latitude conversion and hypothetical stations",
                    "18 coherent navigation points across six pages",
                    "36-row raw JSON and CSV exports with metadata",
                    "good-only summary accounting",
                    "recorded events",
                    "analysis authentication/query bounds",
                    "unavailable sender and unknown drops are explicit",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
