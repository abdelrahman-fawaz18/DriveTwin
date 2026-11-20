"""Reconcile the ten independent actual sources and scoped generic reads."""

import argparse
import json
import threading
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract, read_receipt
from evdt.monitoring.probe_support import actor, api, canonical, require
from evdt.monitoring.repository import connection
from evdt.monitoring.spool import Identity, Spool
from evdt.workload.source_setup import ASSETS


def status():
    values = {}
    with connection() as conn:
        for asset in ASSETS:
            values[asset] = conn.execute(
                "SELECT count(*) AS events FROM m_observations WHERE asset_id=%s", (asset,)
            ).fetchone()["events"]
    return values


def verify(count, assets=ASSETS):
    summaries = {}
    event_ids = set()
    for asset in assets:
        source = Path("/fleet/source") / asset
        report = json.loads((source / "source-report.json").read_bytes())
        require(
            report["passed"] and report["paced"] and len(report["events"]) == count,
            "Independent paced source incomplete",
        )
        with connection() as conn:
            rows = conn.execute(
                "SELECT event_id,raw_payload,wire_sha256 FROM m_observations WHERE asset_id=%s",
                (asset,),
            ).fetchall()
            require(len(rows) == count, "Missing or extra asset events")
            by_id = {str(row["event_id"]): row for row in rows}
            require(not event_ids & by_id.keys(), "Event identities reused across assets")
            event_ids.update(by_id)
            for event in report["events"]:
                require(
                    bytes(by_id[event["event_id"]]["raw_payload"]) == canonical(event["body"])
                    and by_id[event["event_id"]]["wire_sha256"] == event["wire_sha256"],
                    "Original asset bytes differ",
                )
            require(
                bytes(
                    conn.execute(
                        "SELECT raw_manifest FROM m_registrations WHERE asset_id=%s", (asset,)
                    ).fetchone()["raw_manifest"]
                )
                == (source / "run-manifest.json").read_bytes(),
                "Original source recipe changed",
            )
            scalars = conn.execute(
                "SELECT count(*) AS n FROM m_sample_history WHERE asset_id=%s", (asset,)
            ).fetchone()["n"]
        require(scalars == 20 * count, "Fleet scalar accounting differs")
        code, catalog = api("/v1/catalog?asset_id=" + asset)
        require(
            code == 200 and catalog["catalog_version"] == "1.4.0" and len(catalog["signals"]) == 20,
            "Fleet catalog selection failed",
        )
        code, snapshot = api("/v1/snapshot?asset_id=" + asset)
        require(code == 200 and len(snapshot["items"]) == 20, "Generic fleet snapshot failed")
        require(
            all(item["observation"]["stream_seq"] == count - 1 for item in snapshot["items"]),
            "Asset current values differ from final originals",
        )
        require(
            api("/v1/map-context?asset_id=" + asset)[0] == 200,
            "Sustained asset context unavailable",
        )
        if asset != "bus-001":
            require(
                api("/v1/snapshot?asset_id=" + asset, credential="bus-001")[0] == 403,
                "Scoped fleet credential leaked another asset",
            )
        identity = Identity(report["events"][0]["body"]["run_id"], asset, "sumo-adapter")
        spool = Spool(
            Path("/fleet/spool") / asset / "queue.db",
            identity,
            monitoring_contract().validate,
            read_receipt,
            schema_version="3.2.0",
            catalog_version="1.4.0",
        )
        require(
            spool.counts() == {"pending": 0, "accepted": count}, "Fleet queue did not reconcile"
        )
        summaries[asset] = {"events": count, "scalars": scalars, "pending": 0, "catalog": "1.4.0"}
    require(api("/v1/catalog?asset_id=bus-011")[0] == 403, "Unregistered asset was exposed")
    first = json.loads(Path("/fleet/source/bus-001/source-report.json").read_bytes())["events"][-1][
        "body"
    ]
    denied = threading.Event()
    reasons = []
    with actor("adapter-bus-010", "/run/secrets/adapter10_mqtt") as (client, messages, subscribed):

        def published(c, u, mid, reason, properties):
            reasons.append(reason.is_failure)
            denied.set()

        client.on_publish = published
        client.publish(
            f"evdt/simulation/{first['run_id']}/bus-001/sumo-adapter/observations",
            canonical(first),
            qos=1,
        )
        require(denied.wait(5) and reasons[-1], "Tenth publisher could publish for another asset")
    return {
        "passed": True,
        "assets": summaries,
        "unique_events": len(event_ids),
        "cross_asset_broker_publish_denied": True,
        "scope": f"short {len(assets)}-process source/transport/read rehearsal; not full W1 or calibrated interacting traffic",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "verify", "contexts"])
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--assets", nargs="+")
    args = parser.parse_args()
    if args.action == "contexts":
        from evdt.monitoring.map_context import provision

        with connection() as conn:
            for i in range(2, 11):
                provision(conn, Path(f"/run/secrets/map_context{i}"))
        print("All fleet contexts provisioned")
    else:
        print(
            json.dumps(
                status() if args.action == "status" else verify(args.count, args.assets or ASSETS)
            )
        )


if __name__ == "__main__":
    main()
