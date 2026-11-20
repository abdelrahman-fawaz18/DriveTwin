"""Reconcile two real source recordings through unchanged shared production paths."""

import copy
import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlencode

from evdt.monitoring.contracts import observation_topic, receipt_topic
from evdt.monitoring.probe_support import actor, api, canonical, require, wait_receipt
from evdt.monitoring.repository import connection


def main():
    run = os.environ["EVDT_RUN_ID"]
    reports = {}
    summaries = {}
    for asset, folder, version, signals in [
        ("bus-001", "/source-output", "1.2.0", 20),
        ("bus-002", "/source-output-2", "1.3.0", 21),
    ]:
        report = json.loads((Path(folder) / "source-report.json").read_text())
        reports[asset] = report
        require(report["passed"] and len(report["events"]) == 30, "Actual plant source incomplete")
        with connection() as conn:
            rows = conn.execute(
                "SELECT event_id,raw_payload,wire_sha256,payload FROM m_observations WHERE run_id=%s AND asset_id=%s",
                (run, asset),
            ).fetchall()
            require(len(rows) == 30, "Missing or extra accepted source event")
            by_id = {str(r["event_id"]): r for r in rows}
            for event in report["events"]:
                row = by_id[event["event_id"]]
                require(
                    bytes(row["raw_payload"]) == canonical(event["body"])
                    and row["wire_sha256"] == event["wire_sha256"],
                    "Original source packet bytes changed",
                )
            recipe = conn.execute(
                "SELECT raw_manifest FROM m_registrations WHERE run_id=%s AND asset_id=%s",
                (run, asset),
            ).fetchone()
            require(
                bytes(recipe["raw_manifest"]) == (Path(folder) / "run-manifest.json").read_bytes(),
                "Source recipe bytes changed",
            )
        status, snap = api("/v1/snapshot?asset_id=" + asset)
        require(
            status == 200 and len(snap["items"]) == signals,
            "Generic snapshot/catalog coverage incorrect",
        )
        selected = api("/v1/catalog?asset_id=" + asset)[1]
        require(
            selected["catalog_version"] == version and len(selected["signals"]) == signals,
            "Asset catalog version leaked",
        )
        context = api("/v1/map-context?asset_id=" + asset)[1]
        require(context["asset_id"] == asset, "Map context crossed assets")
        metadata, items = None, None
        if asset == "bus-002":
            query = urlencode(
                {"asset_id": asset, "as_of": snap["cursor"], "signals": "coolant.inlet.temperature"}
            )
            status, result = api("/v1/export?" + query)
            require(status == 200, "New scalar export failed")
            metadata, items = result["metadata"], result["items"]
            require(
                len(items) == 15
                and metadata["signals"]["coolant.inlet.temperature"]["unit"] == "K",
                "New scalar cadence/unit changed",
            )
            require(
                sum(r["sample"]["quality"] == "missing" for r in items) == 1
                and sum(r["sample"]["quality"] == "invalid" for r in items) == 1,
                "New scalar quality gaps missing",
            )
            require(all(r["sim_time_s"] % 2 == 0 for r in items), "Two-second cadence violated")
            require(
                any(r["sample"].get("raw_value") is not None for r in items),
                "Invalid diagnostic value lost",
            )
        summaries[asset] = {
            "events": 30,
            "scalars": sum(len(e["body"]["samples"]) for e in report["events"]),
            "signals": signals,
            "catalog_version": version,
            "energy_ledger": report["plant_summary"]["energy_ledger"],
            "new_signal_history": len(items) if items is not None else 0,
        }
    require(
        reports["bus-001"]["session_id"] != reports["bus-002"]["session_id"],
        "Plants share a source session",
    )
    last1 = reports["bus-001"]["events"][-1]["body"]
    last2 = reports["bus-002"]["events"][-1]["body"]

    def values(body):
        return {s["signal_id"]: s["value"] for s in body["samples"]}

    a, b = values(last1), values(last2)
    require(
        a["battery.pack.voltage"] == 650
        and b["battery.pack.voltage"] == 600
        and a["battery.pack.energy"] > b["battery.pack.energy"],
        "Independent energy/parameter sets were relabeled",
    )
    require(
        (a["location.latitude"], a["location.longitude"])
        != (b["location.latitude"], b["location.longitude"]),
        "Independent motion traces are identical",
    )
    require("coolant.inlet.temperature" not in a, "Held-back signal leaked into first asset")
    for own, other in [("bus-001", "bus-002"), ("bus-002", "bus-001")]:
        require(
            api("/v1/snapshot?asset_id=" + other, credential=own)[0] == 403,
            "Reader crossed asset scope",
        )
    snap = api("/v1/snapshot?asset_id=bus-001")[1]
    require(
        api(
            "/v1/history?"
            + urlencode(
                {
                    "asset_id": "bus-001",
                    "signal_id": "coolant.inlet.temperature",
                    "as_of": snap["cursor"],
                }
            )
        )[0]
        == 404,
        "Absent signal was advertised for first asset",
    )
    require(
        api(
            "/v1/history?"
            + urlencode(
                {
                    "asset_id": "bus-002",
                    "signal_id": "coolant.inlet.temperature",
                    "as_of": snap["cursor"],
                }
            )
        )[0]
        == 409,
        "Cross-asset cursor accepted",
    )
    rejections = []
    for asset, password, base, changed, reason in [
        ("bus-002", "adapter2_mqtt", last2, "unit", "wrong_unit"),
        ("bus-002", "adapter2_mqtt", last2, "format", "envelope_schema"),
        ("bus-001", "adapter_mqtt", last1, "signal", "unregistered_signal"),
    ]:
        packet = copy.deepcopy(base)
        packet.update(event_id=str(uuid.uuid4()), stream_seq=100)
        sensor = copy.deepcopy(
            next(s for s in last2["samples"] if s["signal_id"] == "coolant.inlet.temperature")
        )
        packet["samples"] = [sensor]
        if changed == "unit":
            packet["samples"][0]["unit"] = "degC"
        if changed == "format":
            packet["schema_version"] = "3.0.0"
        with actor("adapter-" + asset, "/run/secrets/" + password) as (
            client,
            messages,
            subscribed,
        ):
            client.subscribe(receipt_topic(run, asset, "sumo-adapter"), qos=1)
            require(subscribed.wait(5), "Negative receipt subscription failed")
            wait_receipt(
                client, messages, packet, expected_status="rejected", expected_reason=reason
            )
        rejections.append(reason)
    denied = threading.Event()
    reasons = []
    with actor("adapter-bus-001", "/run/secrets/adapter_mqtt") as (client, _, _):

        def published(c, u, mid, reason, properties):
            reasons.append(reason.is_failure)
            denied.set()

        client.on_publish = published
        client.publish(observation_topic(run, "bus-002", "sumo-adapter"), canonical(last2), qos=1)
        require(denied.wait(5) and reasons[-1], "First publisher could write the other asset")
    require(
        api("/v1/catalog")[1]["catalog_version"] == "mixed", "Mixed catalog discovery was ambiguous"
    )
    print(
        json.dumps(
            {
                "passed": True,
                "assets": summaries,
                "independent_motion_energy_and_sessions": True,
                "original_packet_and_recipe_bytes": True,
                "asset_and_cursor_isolation": True,
                "real_ingress_rejections": rejections,
                "cross_asset_broker_publish_denied": True,
                "scoped_catalog_history_export": True,
            }
        )
    )


if __name__ == "__main__":
    main()
