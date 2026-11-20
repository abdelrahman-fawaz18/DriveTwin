"""Hand-checkable history semantics through actual broker, SQL, API and scoped roles."""

import argparse
import copy
import hashlib
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from evdt.monitoring.contracts import receipt_topic
from evdt.monitoring.probe_support import actor, api, canonical, require, wait_receipt
from evdt.monitoring.registry_probe import Clock, metadata
from evdt.monitoring.repository import connection

SIGNALS = [
    "motion.speed",
    "battery.pack.temperature",
    "battery.pack.soc",
    "battery.pack.power",
    "location.latitude",
]


def request(route, params, **kwargs):
    return api(route + "?" + urlencode(params, doseq=True), **kwargs)


def qualify(output):
    source = json.loads(Path("/source-output/source-report.json").read_bytes())
    require(source["passed"] and len(source["events"]) == 30, "Actual baseline source incomplete")
    run = os.environ["EVDT_RUN_ID"]
    deadline = time.monotonic() + 40
    while True:
        with connection() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM m_observations WHERE run_id=%s AND asset_id='bus-001'",
                (run,),
            ).fetchone()["n"]
        if count == 30:
            break
        require(time.monotonic() < deadline, "Baseline did not drain")
        time.sleep(0.1)
    manifest = json.loads(Path("/source-output/run-manifest.json").read_bytes())
    manifest["identity"].update(source_epoch=2, session_id=str(uuid.uuid4()))
    manifest.update(minimum_sim_time_s=31, source_epoch_start_sim_s=31)
    status, registered = metadata("/v1/registrations", manifest)
    require(status == 200, "Controlled history recipe refused")
    fingerprint = registered["manifest_sha256"]
    clock = Clock(manifest, fingerprint)
    base = source["events"][-1]["body"]
    originals = []

    with actor("adapter-bus-001", "/run/secrets/adapter_mqtt") as (client, messages, subscribed):
        client.subscribe(receipt_topic(run, "bus-001", "sumo-adapter"), qos=1)
        require(subscribed.wait(5), "Controlled receipt subscription missing")

        def send(t, speed, *, missing=False, invalid=False, hot=False):
            clock.declare("running", t)
            body = copy.deepcopy(base)
            body.update(
                manifest["identity"],
                event_id=str(uuid.uuid4()),
                manifest_sha256=fingerprint,
                stream_seq=len(originals),
                sim_time_s=t,
                observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            body["annotations"].update(
                manifest_sha256=fingerprint, fixture="controlled-history-semantics/1.0"
            )
            for sample in body["samples"]:
                if sample["signal_id"] in ("motion.speed", "battery.pack.temperature"):
                    sample.pop("reason", None)
                    sample.pop("raw_value", None)
                    sample.update(
                        quality="good",
                        value=speed
                        if sample["signal_id"] == "motion.speed"
                        else 380
                        if hot
                        else 315,
                    )
                    if sample["signal_id"] == "motion.speed":
                        if missing:
                            sample.update(value=None, quality="missing", reason="dropout")
                        if invalid:
                            sample.update(
                                value=None, quality="invalid", reason="sensor_fault", raw_value=9
                            )
            receipt = wait_receipt(client, messages, body, expected_status="accepted")
            originals.append(
                {
                    "body": body,
                    "wire_sha256": hashlib.sha256(canonical(body)).hexdigest(),
                    "receipt": json.loads(receipt),
                }
            )

        for t, speed in (
            (31, 0),
            (32, 1),
            (33, 20),
            (34, 2),
            (35, 0),
            (36, 3),
            (37, 0),
            (38, 4),
            (40, 5),
            (41, 6),
        ):
            send(t, speed, missing=t == 35, invalid=t == 37, hot=t == 33)
        _, snapshot = api("/v1/snapshot?asset_id=bus-001")
        params = dict(
            asset_id="bus-001", as_of=snapshot["cursor"], signals=SIGNALS, from_s=31, to_s=50
        )
        expected = {
            (event["body"]["event_id"], s["signal_id"]): s
            for event in originals
            for s in event["body"]["samples"]
            if s["signal_id"] in SIGNALS
        }
        status, plotted = request("/v1/plot", {**params, "point_limit": 40})
        require(status == 200, "Reduced five-signal plot failed")
        require(
            len(plotted["items"]) <= 40 and len(plotted["items"]) < 48,
            "No bounded reduction occurred",
        )
        require(plotted["metadata"]["quality_counts"]["count"] == 50, "Raw plot count differs")
        require(
            plotted["metadata"]["quality_counts"]["good"] == 48
            and plotted["metadata"]["quality_counts"]["missing"] == 1
            and plotted["metadata"]["quality_counts"]["invalid"] == 1,
            "Quality counts differ",
        )
        for point in plotted["items"]:
            require(
                point["sample"] == expected[(point["event_id"], point["signal_id"])],
                "Plot invented or altered a value",
            )
        speeds = [p for p in plotted["items"] if p["signal_id"] == "motion.speed"]
        require(
            any(p["sample"]["value"] == 0 for p in speeds)
            and any(p["sample"]["value"] == 20 for p in speeds),
            "Zero or short speed spike was lost",
        )
        require(
            any(
                p["sample"]["value"] == 380
                for p in plotted["items"]
                if p["signal_id"] == "battery.pack.temperature"
            ),
            "Thermal spike was lost",
        )
        by_time = {p["sim_time_s"]: p for p in speeds}
        require(
            by_time[34]["segment_id"] != by_time[36]["segment_id"]
            and by_time[36]["segment_id"] != by_time[38]["segment_id"]
            and by_time[38]["segment_id"] != by_time[40]["segment_id"],
            "Plot joined a quality or timing gap",
        )
        require(
            request("/v1/plot", {**params, "point_limit": 20})[0] == 413,
            "Fragmented data silently exceeded point budget",
        )
        status, aggregates = request("/v1/history-summary", {**params, "bucket_s": 20})
        require(status == 200 and len(aggregates["items"]) == 5, "Five-signal SQL summary failed")
        speed = next(row for row in aggregates["items"] if row["signal_id"] == "motion.speed")
        require(
            speed["good"] == 8
            and speed["missing"] == 1
            and speed["invalid"] == 1
            and speed["minimum"] == 0
            and speed["maximum"] == 20
            and math.isclose(speed["mean"], 41 / 8),
            "Hand-calculated SQL statistics differ",
        )

        status, page = request("/v1/export-page", {**params, "limit": 7})
        require(status == 200 and page["next"], "Raw pagination did not start")
        first_cursor = page["next"]
        send(42, 7)
        exported = []
        while True:
            exported.extend(page["items"])
            if page["next"] is None:
                break
            status, page = request("/v1/export-page", {**params, "limit": 7, "after": page["next"]})
            require(status == 200, "Frozen export page failed")
        require(
            len(exported) == 50 and len({(p["event_id"], p["signal_id"]) for p in exported}) == 50,
            "Frozen raw export gained, lost or duplicated records",
        )
        for row in exported:
            require(
                row["sample"] == expected[(row["event_id"], row["signal_id"])]
                and row["manifest_sha256"] == fingerprint
                and row["schema_version"] == "3.0.0"
                and row["catalog_version"] == "1.2.0",
                "Raw export lost original values or recipe/format provenance",
            )
        _, fresh = api("/v1/snapshot?asset_id=bus-001")
        status, fresh_page = request("/v1/export-page", {**params, "as_of": fresh["cursor"]})
        require(
            status == 200 and len(fresh_page["items"]) == 55,
            "New snapshot did not include later ingress",
        )
        require(
            request("/v1/export-page", {**params, "limit": 8, "after": first_cursor})[0] == 422,
            "Continuation borrowed a different page size",
        )
        require(
            request(
                "/v1/export-page",
                {**params, "signals": SIGNALS[:4], "limit": 7, "after": first_cursor},
            )[0]
            == 422,
            "Continuation borrowed a different signal set",
        )
        require(
            request(
                "/v1/export-page",
                {**params, "as_of": fresh["cursor"], "limit": 7, "after": first_cursor},
            )[0]
            == 422,
            "Continuation borrowed a different snapshot",
        )
        negatives = 3
        for route in ("/v1/plot", "/v1/history-summary", "/v1/export-page"):
            require(
                request(route, params, credential=None)[0] == 401, "History read without credential"
            )
            require(
                request(route, {**params, "asset_id": "bus-002"}, credential="bus-001")[0] == 403,
                "History asset scope escaped",
            )
            require(
                request(route, {**params, "signals": SIGNALS + SIGNALS[:1]})[0] == 422,
                "More than five signals accepted",
            )
            require(
                request(route, {**params, "signals": ["motion.speed"] * 2})[0] == 422,
                "Duplicate signals accepted",
            )
            require(
                request(route, {**params, "signals": ["unregistered"]})[0] == 422,
                "Unregistered signal accepted",
            )
            require(
                request(route, {**params, "as_of": f"{run}:bus-002:1"})[0] == 409,
                "Snapshot crossed asset scope",
            )
            negatives += 6
        status, empty = request("/v1/plot", {**params, "from_s": 100, "to_s": 101})
        require(
            status == 200
            and not empty["items"]
            and empty["metadata"]["quality_counts"]["count"] == 0
            and all(b["minimum"] is None for b in empty["buckets"]),
            "Empty query invented readings",
        )
        clock.declare("finished", 42)
    (output / "controlled-originals.json").write_text(json.dumps(originals, indent=2) + "\n")
    (output / "plot.json").write_text(json.dumps(plotted, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps(aggregates, indent=2) + "\n")
    (output / "export.json").write_text(json.dumps(exported, indent=2) + "\n")
    return {
        "passed": True,
        "scope": "small real SQL/API/control semantics; not W2 volume or query latency acceptance",
        "baseline_events": 30,
        "controlled_events": len(originals),
        "frozen_export_scalar_rows": 50,
        "fresh_export_scalar_rows": 55,
        "plotted_points": len(plotted["items"]),
        "point_budget": 40,
        "negative_http_checks": negatives,
        "spikes_zeros_quality_and_time_gaps_preserved": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print("HISTORY_BACKEND_RESULT=" + json.dumps(qualify(args.output)))


if __name__ == "__main__":
    main()
