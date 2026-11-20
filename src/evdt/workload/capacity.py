"""Exercise production SQLite queue limits with valid exactly sized scalar packets."""

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from evdt.config import ROOT
from evdt.monitoring.contracts import monitoring_contract, read_receipt, receipt
from evdt.monitoring.spool import Identity, Spool, SpoolFull

STAMP = "2026-11-03T00:00:00Z"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def samples():
    # Explicit storage inputs, not a calibrated or independently stepped vehicle.
    spec = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())
    return [
        {
            "signal_id": s["signal_id"],
            "unit": s["unit"],
            "value": s["planning_value"],
            "quality": "good",
            "provenance": "simulated-observation",
        }
        for s in spec["signals"]
    ]


def annotations(spool, seq, values, target):
    pads = {f"capacity_p{i}": "" for i in range(8)}
    with spool.connection() as conn:
        state = conn.execute("SELECT * FROM metadata WHERE id=1").fetchone()
    body = {
        **spool.identity.__dict__,
        "event_id": str(uuid.UUID(int=1)),
        "schema_version": spool.schema_version,
        "catalog_version": spool.catalog_version,
        "session_id": state["session_id"],
        "source_epoch": state["source_epoch"],
        "stream_seq": state["next_seq"],
        "sim_time_s": seq,
        "mode": "simulation",
        "clock_domain": "simulation",
        "observed_at": STAMP,
        "samples": values,
        "manifest_sha256": "0" * 64,
        "annotations": pads,
    }
    delta = target - len(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    if delta < 0:
        raise ValueError("Target is smaller than the valid packet")
    # JSON encodes this character as six ASCII bytes. Each schema field allows 256 characters.
    for key in pads:
        count = min(256, delta // 6)
        pads[key] = "水" * count
        delta -= 6 * count
        extra = min(256 - count, delta if delta < 6 else 0)
        pads[key] += "p" * extra
        delta -= extra
    if delta:
        raise ValueError("Target exceeds bounded annotation capacity")
    return pads


def enqueue(spool, seq, target=8192):
    values = samples()
    event = spool.enqueue(
        values,
        seq,
        observed_at=STAMP,
        annotations=annotations(spool, seq, values, target),
        manifest_sha256="0" * 64,
    )
    require(len(event.raw) == target, "Packet size differs from declared capacity envelope")
    return event


def open_queue(path, identity):
    return Spool(
        path,
        identity,
        monitoring_contract().validate,
        read_receipt,
        schema_version="3.0.0",
        catalog_version="1.2.0",
    )


def inventory(spool):
    with spool.connection() as conn:
        require(
            conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Queue integrity failed"
        )
        state = dict(conn.execute("SELECT * FROM metadata WHERE id=1").fetchone())
        rows = list(
            conn.execute(
                "SELECT event_id,raw,wire_sha256 FROM pending ORDER BY source_epoch,stream_seq"
            )
        )
    for row in rows:
        require(
            hashlib.sha256(row["raw"]).hexdigest() == row["wire_sha256"], "Original packet changed"
        )
    return {
        "state": state,
        "hashes": [r["wire_sha256"] for r in rows],
        "bytes": sum(len(r["raw"]) for r in rows),
        "counts": spool.counts(),
    }


def fill(spool, report):
    spool.start_session()
    expected = []
    minimum = None
    for seq in range(4096):
        event = enqueue(spool, seq)
        expected.append(event.wire_sha256)
        if seq == 1799:
            minimum = {
                "events": 1800,
                "encoded_bytes": 1800 * 8192,
                "database_bytes": spool.path.stat().st_size,
            }
    original = inventory(spool)
    require(
        original["hashes"] == expected and original["bytes"] == 32 * 1024 * 1024,
        "Capacity accounting differs",
    )
    try:
        enqueue(spool, 4096)
    except SpoolFull:
        pass
    else:
        raise RuntimeError("Encoded limit did not refuse the next event")
    require(inventory(spool) == original, "Capacity refusal changed committed queue or sequence")
    require(spool.path.stat().st_size < 64 * 1024 * 1024, "Observed queue exceeds disk budget")
    report.write_text(
        json.dumps(
            {"identity": spool.identity.__dict__, "inventory": original, "minimum_horizon": minimum}
        )
        + "\n"
    )
    return {
        "passed": True,
        "scope": "one asset; burst insertion, not 30 wall minutes or fleet throughput",
        "minimum_horizon": minimum,
        "capacity_events": 4096,
        "encoded_bytes": original["bytes"],
        "database_bytes": spool.path.stat().st_size,
        "blocked_attempts": 1,
        "prior_bytes_and_sequence_preserved": True,
    }


def reopen(spool, report):
    saved = json.loads(report.read_bytes())
    original = inventory(spool)
    require(original == saved["inventory"], "Queue changed across container exit/reopen")
    for event in spool.pending(4):
        require(
            spool.acknowledge(receipt(json.loads(event.raw), event.raw, "accepted")),
            "Bound receipt failed",
        )
    for seq in range(4096, 4100):
        enqueue(spool, seq)
    final = inventory(spool)
    require(final["hashes"][:4092] == original["hashes"][4:], "Refill changed earlier packets")
    require(final["counts"] == {"pending": 4096, "accepted": 4}, "Refill accounting differs")
    return {
        "passed": True,
        "reopened_originals": 4096,
        "generated": 4100,
        "final_counts": final["counts"],
        "original_bytes_preserved": True,
        "receipt_scope": "schema-valid local receipt fixture; no server delivery claim",
    }


def disk_full(spool):
    reserve = spool.path.parent / "test-reserve"
    reserve.write_bytes(b"r" * 131072)
    spool.start_session()
    expected = []
    for seq in range(4096):
        try:
            event = enqueue(spool, seq)
        except sqlite3.OperationalError as exc:
            require(
                getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL,
                "Storage test failed for a reason other than a full filesystem",
            )
            break
        expected.append(event.wire_sha256)
    else:
        raise RuntimeError("Bounded test filesystem did not fill")
    require(
        0 < len(expected) < 1800, "Disk fault did not occur inside the declared encoded envelope"
    )
    reopened = open_queue(spool.path, spool.identity)
    before = inventory(reopened)
    require(
        before["hashes"] == expected and before["state"]["next_seq"] == len(expected),
        "Full filesystem changed committed bytes or allocated a sequence",
    )
    reserve.unlink()  # Release only this fixture's file, never unrelated storage.
    enqueue(reopened, len(expected))
    after = inventory(reopened)
    require(after["hashes"][:-1] == expected, "Storage recovery changed earlier packets")
    return {
        "passed": True,
        "sqlite_full": True,
        "pending_at_fault": len(expected),
        "blocked_attempts": 1,
        "prior_packets_preserved": True,
        "reopen_and_recovery": True,
        "scope": "controlled 4 MiB tmpfs; no host disk exhaustion or power-loss claim",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["fill", "reopen", "disk-full"])
    parser.add_argument("--path", type=Path, default=Path("/spool/capacity.db"))
    parser.add_argument("--report", type=Path, default=Path("/report/capacity.json"))
    args = parser.parse_args()
    identity = (
        Identity(**json.loads(args.report.read_bytes())["identity"])
        if args.action == "reopen"
        else Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    )
    spool = open_queue(args.path, identity)
    result = (
        fill(spool, args.report)
        if args.action == "fill"
        else reopen(spool, args.report)
        if args.action == "reopen"
        else disk_full(spool)
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
