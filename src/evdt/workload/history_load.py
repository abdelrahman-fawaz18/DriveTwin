"""Load source originals through normal admission, then independently reconcile history."""

import argparse
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract, observation_topic, read_receipt
from evdt.monitoring.probe_support import api, require
from evdt.monitoring.repository import connection, store
from evdt.workload.source_setup import ASSETS


def originals(directory):
    summary = json.loads((directory / "source-summary.json").read_bytes())
    require(summary["passed"] and summary["paced"] is False, "Incomplete history source")
    require(
        summary["registration_mode"] == "registered-before-generation",
        "Unregistered history source",
    )
    require(summary["identity"]["run_id"] == os.environ["EVDT_RUN_ID"], "History recording differs")
    digest, count = hashlib.sha256(), 0
    with (directory / "originals.jsonl").open("rb") as stream:
        while line := stream.readline(100000):
            require(len(line) < 100000 and line.endswith(b"\n"), "Unbounded/truncated history line")
            row = json.loads(line)
            raw = base64.b64decode(row["raw_base64"], validate=True)
            body = json.loads(raw)
            require(hashlib.sha256(raw).hexdigest() == row["wire_sha256"], "History bytes changed")
            require(
                all(body[k] == v for k, v in summary["identity"].items()),
                "History identity differs",
            )
            require(
                body["event_id"] == row["event_id"]
                and body["stream_seq"] == count
                and abs(body["sim_time_s"] - count - 1) < 1e-8
                and body["manifest_sha256"] == summary["manifest_sha256"],
                "History sequence/time/recipe differs",
            )
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
            count += 1
            yield raw, body
    require(count == summary["events"] == summary["expected_events"], "History count differs")
    require(
        digest.hexdigest() == summary["original_packets_sha256"],
        "History stream fingerprint differs",
    )


def load(directory):
    summary = json.loads((directory / "source-summary.json").read_bytes())
    asset, run = summary["identity"]["asset_id"], os.environ["EVDT_RUN_ID"]
    require(asset in ASSETS, "Unknown history asset")
    with connection() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM m_observations WHERE run_id=%s AND asset_id=%s", (run, asset)
        ).fetchone()["n"]
        require(n == 0, "History loader requires an empty asset recording")
    contract = monitoring_contract()
    count, scalar_rows = 0, 0
    started = time.monotonic()
    for raw, body in originals(directory):
        result = store(raw, observation_topic(run, asset, body["source_id"]), run, contract)
        require(result["outcome"] == "accepted", "Normal history storage refused an original")
        count += 1
        scalar_rows += len(body["samples"])
        if count % 1000 == 0:
            print(f"Validated/stored {asset}: {count} original events", flush=True)
    require(
        count == summary["events"] and scalar_rows == summary["scalar_rows"],
        "History load accounting differs",
    )
    return {
        "passed": True,
        "asset": asset,
        "events": count,
        "scalar_rows": scalar_rows,
        "elapsed_wall_s": time.monotonic() - started,
        "path": "original packet -> existing validator/admission/repository.store; no broker claim",
    }


def compare_stored(raw, body, row):
    require(
        bytes(row["raw_payload"]) == raw
        and row["wire_sha256"] == hashlib.sha256(raw).hexdigest()
        and row["payload"] == body
        and str(row["event_id"]) == body["event_id"]
        and row["stream_seq"] == body["stream_seq"]
        and row["sim_time_s"] == body["sim_time_s"],
        "Stored original differs",
    )


def verify(directory, assets, samples):
    run = os.environ["EVDT_RUN_ID"]
    checked, retries = {}, []
    for asset in assets:
        source = directory / asset
        summary = json.loads((source / "source-summary.json").read_bytes())
        require(
            summary["events"] == samples and summary["scalar_rows"] == 20 * samples,
            "History source scope differs",
        )
        first, last = None, None
        with connection() as conn:
            with conn.cursor(name="history_original_audit") as cursor:
                cursor.execute(
                    "SELECT o.event_id,o.stream_seq,o.sim_time_s,o.raw_payload,o.wire_sha256,o.payload,r.receipt FROM m_observations o LEFT JOIN m_receipts r USING(run_id,event_id,wire_sha256) WHERE o.run_id=%s AND o.asset_id=%s ORDER BY o.stream_seq",
                    (run, asset),
                )
                count = 0
                for raw, body in originals(source):
                    row = cursor.fetchone()
                    require(row is not None, "Stored original missing")
                    compare_stored(raw, body, row)
                    receipt = read_receipt(bytes(row["receipt"]))
                    require(
                        all(
                            receipt[k] == body[k]
                            for k in ("run_id", "asset_id", "source_id", "event_id")
                        )
                        and receipt["status"] == "accepted"
                        and receipt["reason"] is None
                        and receipt["wire_sha256"] == row["wire_sha256"],
                        "Original persistence receipt differs",
                    )
                    if first is None:
                        first = (raw, body)
                    last = body
                    count += 1
                require(
                    cursor.fetchone() is None and count == samples,
                    "Unexpected stored original count",
                )
            require(
                bytes(
                    conn.execute(
                        "SELECT raw_manifest FROM m_registrations WHERE run_id=%s AND asset_id=%s",
                        (run, asset),
                    ).fetchone()["raw_manifest"]
                )
                == (source / "plant/run-manifest.json").read_bytes(),
                "Registered recipe bytes differ",
            )
            scalar_count = conn.execute(
                "SELECT count(*) AS n FROM m_sample_history WHERE run_id=%s AND asset_id=%s",
                (run, asset),
            ).fetchone()["n"]
            require(scalar_count == 20 * samples, "Scalar history count differs")
            # Compare every scalar and copied provenance with its original JSON sample.
            mismatches = conn.execute(
                """SELECT count(*) AS n
              FROM m_observations o CROSS JOIN LATERAL jsonb_array_elements(o.payload->'samples') s
              LEFT JOIN m_sample_history h ON h.run_id=o.run_id AND h.event_id=o.event_id AND h.signal_id=s->>'signal_id'
              WHERE o.run_id=%s AND o.asset_id=%s AND (
                h.event_id IS NULL OR h.sample IS DISTINCT FROM s OR h.asset_id<>o.asset_id
                OR h.source_id<>o.source_id OR h.source_epoch<>o.source_epoch OR h.stream_seq<>o.stream_seq
                OR h.sim_time_s<>o.sim_time_s OR h.observed_at<>o.observed_at OR h.received_at<>o.received_at
                OR h.dashboard_revision<>o.dashboard_revision)""",
                (run, asset),
            ).fetchone()["n"]
            require(mismatches == 0, "Scalar history/provenance differs from original samples")
            current = conn.execute(
                "SELECT signal_id,event_id,sample,stream_seq FROM m_current WHERE run_id=%s AND asset_id=%s",
                (run, asset),
            ).fetchall()
            final_samples = {s["signal_id"]: s for s in last["samples"]}
            require(
                len(current) == 20
                and {r["signal_id"] for r in current} == set(final_samples)
                and all(
                    str(r["event_id"]) == last["event_id"]
                    and r["stream_seq"] == samples - 1
                    and r["sample"] == final_samples[r["signal_id"]]
                    for r in current
                ),
                "Current history values differ",
            )
            receipts = conn.execute(
                "SELECT count(*) AS n FROM m_receipts WHERE run_id=%s AND event_id IN (SELECT event_id FROM m_observations WHERE run_id=%s AND asset_id=%s)",
                (run, run, asset),
            ).fetchone()["n"]
            require(receipts == samples, "History persistence receipt count differs")
        require(
            api("/v1/catalog?asset_id=" + asset)[0] == 200
            and api("/v1/map-context?asset_id=" + asset)[0] == 200,
            "History catalog/map read failed",
        )
        require(
            asset == "bus-001"
            or api("/v1/snapshot?asset_id=" + asset, credential="bus-001")[0] == 403,
            "Scoped history read leaked",
        )
        retries.append((asset, first[0]))
        checked[asset] = {
            "events": count,
            "scalar_rows": scalar_count,
            "all_original_bytes_and_scalars_checked": True,
            "current_and_recipe_and_receipts_checked": True,
        }
    totals = counts(run)
    require(
        totals["events"] == samples * len(assets)
        and totals["scalars"] == samples * len(assets) * 20
        and totals["receipts"] == samples * len(assets)
        and totals["quarantine"] == 0,
        "Complete history database accounting differs",
    )
    from evdt.recovery.database import inventory

    print(
        "Checking complete data/schema/permission/sequence inventory across original retries",
        flush=True,
    )
    with connection() as conn:
        before = inventory(conn)
    contract = monitoring_contract()
    for asset, raw in retries:
        duplicate = store(raw, observation_topic(run, asset, "sumo-adapter"), run, contract)
        require(duplicate["outcome"] == "duplicate", "Original retry was not idempotent")
    with connection() as conn:
        require(
            inventory(conn) == before, "Original retries changed the complete durable inventory"
        )
    return {
        "passed": True,
        "by_asset": checked,
        "totals": totals,
        "database_W2_volume": len(assets) == 10 and samples == 7200,
        "live_delivery_claim": False,
        "complete_inventory_unchanged_after_retries": True,
    }


def counts(run):
    with connection() as conn:
        return {
            key: conn.execute(
                "SELECT count(*) AS n FROM " + table + " WHERE run_id=%s", (run,)
            ).fetchone()["n"]
            for key, table in [
                ("events", "m_observations"),
                ("scalars", "m_sample_history"),
                ("receipts", "m_receipts"),
                ("quarantine", "m_quarantine"),
            ]
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["load", "verify"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--assets", nargs="+", choices=ASSETS)
    parser.add_argument("--samples", type=int)
    args = parser.parse_args()
    if args.action == "verify" and (
        not args.assets
        or not args.samples
        or len(set(args.assets)) != len(args.assets)
        or not 1 <= args.samples <= 7200
    ):
        parser.error("Verification requires distinct assets and 1..7200 source events")
    result = (
        load(args.directory)
        if args.action == "load"
        else verify(args.directory, args.assets, args.samples)
    )
    print("HISTORY_LOAD_RESULT=" + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
