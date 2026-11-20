"""Observe a real source/outbox/receiver, then verify exact original outage recovery."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from evdt.monitoring.contracts import receipt_topic
from evdt.monitoring.probe_support import actor, api, require
from evdt.monitoring.repository import connection
from evdt.monitoring.transport import open_spool


def state():
    spool = open_spool()
    with spool.connection() as conn:
        row = conn.execute(
            "SELECT next_seq,accepted_count,rejected_count FROM metadata WHERE id=1"
        ).fetchone()
        generated, accepted, rejected = tuple(row)
    with connection() as conn:
        db = conn.execute(
            "SELECT count(*) AS events,coalesce(max(stream_seq),-1) AS latest FROM m_observations WHERE asset_id='bus-001'"
        ).fetchone()
    status, snap = api("/v1/snapshot?asset_id=bus-001")
    require(status == 200, "Snapshot unavailable during outage test")
    seqs = [item["observation"]["stream_seq"] for item in snap["items"] if item["observation"]]
    return {
        "monotonic_s": time.monotonic(),
        "utc": datetime.now(timezone.utc).isoformat(),
        "generated": generated,
        "accepted": accepted,
        "rejected": rejected,
        "pending": spool.counts()["pending"],
        "database": db,
        "current_min_seq": min(seqs) if seqs else -1,
        "current_max_seq": max(seqs) if seqs else -1,
        "spool_file_bytes": spool.path.stat().st_size,
    }


def atomic(path, body):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(body) + "\n")
    for attempt in range(20):
        try:
            pending.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.02)


def monitor(folder):
    start = time.monotonic()
    previous = -1
    count = 0
    with (folder / "samples.jsonl").open("x") as stream:
        while not (folder / "finish").exists():
            require(time.monotonic() - start < 900, "Outage monitor exceeded its owned lifetime")
            value = state()
            require(
                value["current_min_seq"] >= previous,
                "Current measurements regressed during backlog replay",
            )
            previous = value["current_min_seq"]
            stream.write(json.dumps(value) + "\n")
            stream.flush()
            atomic(folder / "latest.json", value)
            count += 1
            time.sleep(max(0, start + count - time.monotonic()))
    atomic(
        folder / "monitor-result.json",
        {"passed": True, "samples": count, "current_nonregressing": True, "period_s": 1},
    )


def deliver(client, messages, spool, pending):
    client.publish(spool.identity.topic, pending.raw, qos=1).wait_for_publish(timeout=5)
    end = time.monotonic() + 12
    while time.monotonic() < end:
        raw = next(
            (m for m in list(messages) if json.loads(m)["event_id"] == pending.event_id), None
        )
        if raw:
            require(
                json.loads(raw)["status"] == "accepted" and spool.acknowledge(raw),
                "Original packet receipt rejected",
            )
            return
        time.sleep(0.03)
    raise RuntimeError("No durable recovery receipt")


def reorder():
    spool = open_spool()
    pending = spool.pending(1024)
    require(len(pending) >= 300, "Full five-minute backlog was not present")
    with actor("adapter-bus-001", "/run/secrets/adapter_mqtt") as (client, messages, subscribed):
        identity = spool.identity
        client.subscribe(
            receipt_topic(identity.run_id, identity.asset_id, identity.source_id), qos=1
        )
        require(subscribed.wait(5), "Receipt subscription failed")
        newest = pending[-1]
        deliver(client, messages, spool, newest)
        promoted = json.loads(newest.raw)["stream_seq"]
        require(
            state()["current_min_seq"] == promoted,
            "Newest actual backlog event did not advance current",
        )
        deliver(client, messages, spool, pending[0])
        require(
            state()["current_min_seq"] == promoted, "Older actual backlog event regressed current"
        )
    return {
        "passed": True,
        "promoted_seq": promoted,
        "older_seq": json.loads(pending[0].raw)["stream_seq"],
        "original_packets_only": True,
        "older_preserved_in_history": True,
    }


def verify(expected):
    report = json.loads(Path("/source-output/source-report.json").read_bytes())
    require(
        report["passed"] and len(report["events"]) == expected and report["paced"],
        "Real paced source incomplete",
    )
    with connection() as conn:
        rows = conn.execute(
            "SELECT event_id,raw_payload,wire_sha256,payload FROM m_observations WHERE asset_id='bus-001'"
        ).fetchall()
        require(len(rows) == expected, "Accepted event accounting differs")
        by_id = {str(row["event_id"]): row for row in rows}
        for event in report["events"]:
            row = by_id[event["event_id"]]
            original = json.dumps(event["body"], sort_keys=True, separators=(",", ":")).encode()
            require(
                bytes(row["raw_payload"]) == original
                and hashlib.sha256(original).hexdigest()
                == row["wire_sha256"]
                == event["wire_sha256"],
                "Original generated packet bytes differ",
            )
        scalars = conn.execute(
            "SELECT count(*) AS n FROM m_sample_history WHERE asset_id='bus-001'"
        ).fetchone()["n"]
        require(scalars == 20 * expected, "Scalar accounting differs")
        require(
            conn.execute("SELECT count(*) AS n FROM m_quarantine").fetchone()["n"] == 0,
            "Unexpected quarantine",
        )
        current = conn.execute(
            "SELECT stream_seq FROM m_current WHERE asset_id='bus-001'"
        ).fetchall()
        require(
            len(current) == 20 and all(r["stream_seq"] == expected - 1 for r in current),
            "Final current does not match last original event",
        )
        require(
            bytes(
                conn.execute(
                    "SELECT raw_manifest FROM m_registrations WHERE asset_id='bus-001'"
                ).fetchone()["raw_manifest"]
            )
            == Path("/source-output/run-manifest.json").read_bytes(),
            "Recipe changed",
        )
    spool = open_spool()
    counts = spool.counts()
    expected_counts = {"pending": 0, "accepted": expected}
    if spool.capacity_policy is not None:
        expected_counts.update(
            generated=expected,
            queued=0,
            rejected=0,
            dropped=0,
            evicted_unconfirmed=0,
            evicted_lifetime=0,
        )
    require(counts == expected_counts, "Final durable queue accounting differs")
    return {
        "passed": True,
        "generated": expected,
        "accepted": expected,
        "scalars": scalars,
        "quarantined": 0,
        "dropped": 0,
        "pending": 0,
        "original_packets_and_recipe_equal": True,
        "current_last_seq": expected - 1,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["monitor", "reorder", "verify", "state"])
    parser.add_argument("--folder", type=Path, default=Path("/control"))
    parser.add_argument("--events", type=int, default=480)
    args = parser.parse_args()
    if args.action == "monitor":
        monitor(args.folder)
    elif args.action == "state":
        print(json.dumps(state()))
    else:
        print(json.dumps(reorder() if args.action == "reorder" else verify(args.events)))


if __name__ == "__main__":
    main()
