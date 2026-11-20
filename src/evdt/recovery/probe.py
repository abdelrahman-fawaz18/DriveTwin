"""Real service/queue recovery assertions; original producer packets stay byte-exact."""

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.request import Request, urlopen

import psycopg

from evdt.monitoring.contracts import receipt_topic
from evdt.monitoring.probe_support import actor, api, require
from evdt.monitoring.repository import connection
from evdt.monitoring.transport import open_spool

ASSETS = [
    ("bus-001", "/spool", "1.2.0", "adapter_mqtt"),
    ("bus-002", "/spool-2", "1.3.0", "adapter2_mqtt"),
]


def queue_inventory(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        require(
            conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
            "SQLite integrity failed",
        )
        result = {}
        for name in ("metadata", "queue_format", "pending", "terminal"):
            rows = conn.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            normalized = [
                [{"bytes": v.hex()} if isinstance(v, bytes) else v for v in r] for r in rows
            ]
            result[name] = {
                "rows": len(rows),
                "sha256": hashlib.sha256(
                    json.dumps(normalized, sort_keys=True).encode()
                ).hexdigest(),
            }
        return result


def snapshot(root, restore=False):
    result = {}
    for asset, folder, _, _ in ASSETS:
        saved, live = root / "queues" / (asset + ".sqlite"), Path(folder) / "queue.db"
        source, dest = (saved, live) if restore else (live, saved)
        require(not dest.exists(), "Refusing to overwrite queue")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with (
            sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src,
            sqlite3.connect(dest) as dst,
        ):
            src.backup(dst)
        require(queue_inventory(source) == queue_inventory(dest), "Queue backup mismatch")
        result[asset] = queue_inventory(dest)
        if not restore:
            folder = Path("/source-output" if asset == "bus-001" else "/source-output-2")
            report = json.loads((folder / "source-report.json").read_bytes())
            require(
                report["passed"] and len(report["events"]) == 30,
                "Actual generated recording incomplete",
            )
            recordings = root / "recordings"
            recordings.mkdir(exist_ok=True)
            (recordings / (asset + ".json")).write_text(
                json.dumps({"wire_sha256": sorted(e["wire_sha256"] for e in report["events"])})
            )
    return result


def partial(limit):
    counts = {}
    for asset, folder, version, password in ASSETS:
        os.environ.update(ASSET_ID=asset, SPOOL_FILE=folder + "/queue.db", CATALOG_VERSION=version)
        spool = open_spool()
        with actor("adapter-" + asset, "/run/secrets/" + password) as (
            client,
            messages,
            subscribed,
        ):
            identity = spool.identity
            client.subscribe(receipt_topic(identity.run_id, asset, identity.source_id), qos=1)
            require(subscribed.wait(5), "Receipt subscription failed")
            for pending in spool.pending(limit):
                client.publish(identity.topic, pending.raw, qos=1).wait_for_publish(timeout=5)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    receipt = next(
                        (
                            raw
                            for raw in list(messages)
                            if json.loads(raw)["event_id"] == pending.event_id
                        ),
                        None,
                    )
                    if receipt:
                        require(
                            json.loads(receipt)["status"] == "accepted",
                            "Source observation rejected",
                        )
                        require(spool.acknowledge(receipt), "Receipt binding failed")
                        break
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Durable receipt timed out")
        counts[asset] = spool.counts()
        require(counts[asset] == {"pending": 5, "accepted": 25}, "Unexpected sealed backlog")
    return counts


def reads(expected_events):
    result = {}
    readers = json.loads(Path(os.environ["READERS_FILE"]).read_bytes())["readers"]
    replay_readers = json.loads(Path("/run/secrets/replay_readers").read_bytes())["readers"]
    with connection() as conn:
        for asset, _, version, _ in ASSETS:
            packets = conn.execute(
                "SELECT raw_payload,wire_sha256,payload FROM m_observations WHERE asset_id=%s ORDER BY stream_seq",
                (asset,),
            ).fetchall()
            require(len(packets) == expected_events, "Accepted event count differs")
            status, catalog = api("/v1/catalog?asset_id=" + asset)
            require(
                status == 200 and catalog["catalog_version"] == version, "Catalog recovery failed"
            )
            status, snap = api("/v1/snapshot?asset_id=" + asset)
            require(
                status == 200 and len(snap["items"]) == (20 if asset == "bus-001" else 21),
                "Snapshot recovery failed",
            )
            signals = list(catalog["signals"])
            exports = []
            for start in range(0, len(signals), 4):
                selected = "".join("&signals=" + name for name in signals[start : start + 4])
                status, page = api(
                    "/v1/export?asset_id=" + asset + "&as_of=" + snap["cursor"] + selected
                )
                require(status == 200, "Raw historical export failed")
                exports.append(page)
            scalar_count = sum(len(page["items"]) for page in exports)
            require(
                scalar_count == sum(len(p["payload"]["samples"]) for p in packets),
                "Scalar count differs",
            )
            status, context = api("/v1/map-context?asset_id=" + asset)
            require(status == 200 and context["asset_id"] == asset, "Map context recovery failed")
            request = Request(
                "http://replay:8000/v1/replay/sessions",
                data=json.dumps(
                    {
                        "asset_id": asset,
                        "source_run_id": os.environ["EVDT_RUN_ID"],
                        "from_s": 0,
                        "to_s": 30,
                    }
                ).encode(),
                headers={
                    "Authorization": "Bearer " + replay_readers[0]["token"],
                    "Content-Type": "application/json",
                },
            )
            with urlopen(request, timeout=10) as response:
                package = json.load(response)
            original = {p["wire_sha256"]: bytes(p["raw_payload"]) for p in packets}
            require(len(package["packets"]) == expected_events, "Replay count differs")
            for packet in package["packets"]:
                raw = base64.b64decode(packet["raw_base64"], validate=True)
                require(
                    raw == original[hashlib.sha256(raw).hexdigest()], "Replay original bytes differ"
                )
            # Scope remains enforced with rotated credentials.
            scoped = next(r for r in readers if r["label"] != "all" and asset not in r["assets"])
            require(scoped["token"] != readers[0]["token"], "Reader tokens must differ")
            require(
                api("/v1/snapshot?asset_id=" + asset, credential=scoped["label"])[0] == 403,
                "Restored scope leaked",
            )
            result[asset] = {
                "events": len(packets),
                "scalars": scalar_count,
                "catalog": catalog,
                "exports": exports,
                "context": context,
                "packet_hashes": sorted(original),
                "current_observations": {
                    item["signal_id"]: item["observation"] for item in snap["items"]
                },
            }
    return result


def permissions():
    outcomes = {}
    for role, file in [("evdt_read", "read_db"), ("evdt_replay", "replay_db")]:
        password = Path("/run/secrets", file).read_text().strip()
        with psycopg.connect(host="db", dbname="evdt", user=role, password=password) as conn:
            conn.execute(
                "SELECT * FROM "
                + ("m_current" if role == "evdt_read" else "m_replay_packets")
                + " LIMIT 1"
            )
        for statement in (
            "UPDATE m_current SET sim_time_s=0 WHERE false",
            "CREATE TABLE forbidden(n int)",
        ):
            try:
                with psycopg.connect(
                    host="db", dbname="evdt", user=role, password=password
                ) as conn:
                    conn.execute(statement)
            except psycopg.errors.InsufficientPrivilege:
                continue
            raise RuntimeError("Restored restricted role allowed a mutation")
        outcomes[role] = "SELECT works; UPDATE/CREATE denied"
    old_password = Path("/old-read-password").read_text().strip()
    try:
        with psycopg.connect(
            host="db", dbname="evdt", user="evdt_read", password=old_password, connect_timeout=5
        ):
            pass
    except psycopg.OperationalError as exc:
        require(
            "password authentication failed" in str(exc),
            "Old-password check failed for another reason",
        )
        outcomes["old_database_password"] = "denied"
    else:
        raise RuntimeError("Old database password accepted")
    return outcomes


def retry():
    with connection() as conn:
        for asset, _, _, password in ASSETS:
            row = conn.execute(
                "SELECT raw_payload,wire_sha256,event_id FROM m_observations WHERE asset_id=%s ORDER BY stream_seq LIMIT 1",
                (asset,),
            ).fetchone()
            with actor("adapter-" + asset, "/run/secrets/" + password) as (
                client,
                messages,
                subscribed,
            ):
                run = os.environ["EVDT_RUN_ID"]
                client.subscribe(receipt_topic(run, asset, "sumo-adapter"), qos=1)
                require(subscribed.wait(5), "Retry subscription failed")
                client.publish(
                    f"evdt/simulation/{run}/{asset}/sumo-adapter/observations",
                    bytes(row["raw_payload"]),
                    qos=1,
                ).wait_for_publish(timeout=5)
                end = time.monotonic() + 10
                while time.monotonic() < end:
                    if any(
                        json.loads(m)["event_id"] == str(row["event_id"])
                        and json.loads(m)["wire_sha256"] == row["wire_sha256"]
                        and json.loads(m)["status"] == "accepted"
                        for m in list(messages)
                    ):
                        break
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Retry did not return original durable receipt")
    return {"retried_originals": 2}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["partial", "snapshot", "load", "reads", "queues", "permissions", "retry"]
    )
    parser.add_argument("--root", type=Path, default=Path("/recovery"))
    parser.add_argument("--events", type=int, default=25)
    args = parser.parse_args()
    if args.action == "partial":
        result = partial(25)
    elif args.action in ("snapshot", "load"):
        result = snapshot(args.root, restore=args.action == "load")
    elif args.action == "reads":
        result = reads(args.events)
    elif args.action == "permissions":
        result = permissions()
    elif args.action == "retry":
        result = retry()
    else:
        result = {}
        for asset, folder, version, _ in ASSETS:
            os.environ.update(
                ASSET_ID=asset, SPOOL_FILE=folder + "/queue.db", CATALOG_VERSION=version
            )
            result[asset] = open_spool().counts()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
