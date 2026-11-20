"""Normal SQLite queue on a physically bounded filesystem; synthetic storage inputs."""

import argparse
import hashlib
import json
import os
import sqlite3
import struct
from pathlib import Path

from evdt.config import ROOT
from evdt.monitoring.contracts import monitoring_contract, read_receipt
from evdt.monitoring.critical_fault import FaultLatch
from evdt.monitoring.spool import Identity, Pending, Spool
from evdt.monitoring.spool_capacity import DropOldestPolicy
from evdt.workload.capacity import STAMP, annotations


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def inventory(spool):
    digest = hashlib.sha256()
    with spool.connection() as conn:
        require(
            conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Queue integrity failed"
        )
        definitions = list(
            conn.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )
        for row in definitions:
            raw = json.dumps(tuple(row), separators=(",", ":"), allow_nan=False).encode()
            digest.update(struct.pack("!Q", len(raw)))
            digest.update(raw)
        for table in (
            "metadata",
            "queue_format",
            "pending",
            "terminal",
            "queue_capacity",
            "queue_evictions",
        ):
            digest.update(table.encode() + b"\0")
            for row in conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid'):
                for field in row:
                    raw = (
                        field
                        if isinstance(field, bytes)
                        else json.dumps(
                            field, sort_keys=True, separators=(",", ":"), allow_nan=False
                        ).encode()
                    )
                    digest.update(b"b" if isinstance(field, bytes) else b"j")
                    digest.update(struct.pack("!Q", len(raw)))
                    digest.update(raw)
        state = dict(conn.execute("SELECT * FROM metadata WHERE id=1").fetchone())
        disk_pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    return {
        "data_schema_sha256": digest.hexdigest(),
        "metadata": state,
        "counts": spool.counts(),
        "database_bytes": spool.path.stat().st_size,
        "page_size": page_size,
        "pages": disk_pages,
        "freelist_pages": free_pages,
    }


def original_record(event, seq, samples, identity, schema, catalog):
    body = json.loads(event.raw)
    require(len(event.raw) == 8192, "Storage packet is not the frozen worst size")
    require(
        json.dumps(body["samples"], sort_keys=True, allow_nan=False)
        == json.dumps(samples, sort_keys=True, allow_nan=False)
        and body["sim_time_s"] == seq
        and body["stream_seq"] == seq
        and body["observed_at"] == STAMP
        and body["source_epoch"] == 1
        and body["mode"] == "simulation"
        and body["clock_domain"] == "simulation"
        and body["manifest_sha256"] == "0" * 64
        and body["schema_version"] == schema
        and body["catalog_version"] == catalog,
        "Original packet fields differ from the explicit storage fixture",
    )
    require(
        all(body[key] == value for key, value in identity.__dict__.items()),
        "Original storage identity differs",
    )
    require(
        hashlib.sha256(event.raw).hexdigest() == event.wire_sha256,
        "Original queue fingerprint differs",
    )
    return {
        "event_id": event.event_id,
        "wire_sha256": event.wire_sha256,
        "source_epoch": body["source_epoch"],
        "session_id": body["session_id"],
        "stream_seq": seq,
        "sim_time_s": seq,
        "observed_at": STAMP,
    }


def append(spool, seq, control):
    values = control["samples"]
    event = spool.enqueue(
        values,
        seq,
        observed_at=STAMP,
        annotations=annotations(spool, seq, values, control["profile"]["packet_bytes"]),
        manifest_sha256="0" * 64,
    )
    return original_record(
        event,
        seq,
        values,
        spool.identity,
        control["profile"]["schema_version"],
        control["profile"]["catalog_version"],
    )


def verify(spool, control, events, *, confirmed=False):
    profile = control["profile"]
    require(len({row["event_id"] for row in events}) == len(events), "Original event ID reused")
    require(
        [row["stream_seq"] for row in events] == list(range(len(events))),
        "Original witness sequence incomplete",
    )
    require(
        len({row["session_id"] for row in events}) == 1
        and all(row["source_epoch"] == 1 for row in events),
        "An uninterrupted fixture changed producer session",
    )
    excess = max(0, len(events) - profile["encoded_capacity_events"])
    expected = events[excess:]
    with spool.connection() as conn:
        rows = conn.execute("SELECT * FROM pending ORDER BY source_epoch,stream_seq")
        count = 0
        for row, original in zip(rows, expected, strict=True):
            require(
                row["event_id"] == original["event_id"]
                and row["wire_sha256"] == original["wire_sha256"]
                and hashlib.sha256(row["raw"]).hexdigest() == original["wire_sha256"]
                and row["source_epoch"] == 1
                and row["stream_seq"] == original["stream_seq"],
                "Reopened original bytes/identity/order differ",
            )
            body = json.loads(row["raw"])
            require(
                body["session_id"] == original["session_id"], "Original producer session differs"
            )
            original_record(
                Pending(row["event_id"], bytes(row["raw"]), row["wire_sha256"]),
                row["stream_seq"],
                control["samples"],
                spool.identity,
                profile["schema_version"],
                profile["catalog_version"],
            )
            spool.validate(bytes(row["raw"]), spool.identity.topic, spool.identity.run_id)
            count += 1
        require(count == len(expected), "Queued original count differs")
    losses = spool.eviction_audit()
    require(len(losses) == excess, "Eviction evidence count differs")
    for lost, original in zip(losses, events[:excess], strict=True):
        require(
            all(
                lost[key] == original[key]
                for key in (
                    "event_id",
                    "wire_sha256",
                    "source_epoch",
                    "stream_seq",
                    "sim_time_s",
                    "observed_at",
                )
            ),
            "Eviction ID/hash/order/original time differs",
        )
        require(
            lost["disposition"] == ("dropped" if confirmed else "unconfirmed"),
            "Unsettled loss was mislabeled",
        )
        require(
            (lost["drop_audit"] is not None) == confirmed and lost["receipt"] is None,
            "Eviction audit/result differs",
        )
    counts = spool.counts()
    require(
        counts
        == {
            "generated": len(events),
            "queued": len(expected),
            "pending": len(expected) + (0 if confirmed else excess),
            "accepted": 0,
            "rejected": 0,
            "dropped": excess if confirmed else 0,
            "evicted_unconfirmed": 0 if confirmed else excess,
            "evicted_lifetime": excess,
        },
        "Queue accounting differs from every original witness",
    )
    result = inventory(spool)
    require(
        result["metadata"]["source_epoch"] == 1 and result["metadata"]["next_seq"] == len(events),
        "Queue epoch or next sequence differs",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("nominal", "capacity", "overflow", "verify", "full-fault", "confirm-loss"),
    )
    parser.add_argument("--control", type=Path, required=True)
    args = parser.parse_args()
    require(os.geteuid() == 10001, "Queue storage checks require the ordinary application UID")
    control = json.loads(args.control.read_bytes())
    profile = control["profile"]
    require(
        profile["version"] == "queue-storage-integration/1.0.0"
        and profile["packet_bytes"] == 8192
        and profile["catalog_version"] == "1.4.0"
        and profile["schema_version"] == "3.2.0"
        and len(control["samples"]) == 20
        and profile["nominal_horizon_events"] == 1800
        and profile["encoded_capacity_events"] == 4096
        and profile["overflow_events"] == 10
        and profile["loss_record_limit"] == 16384,
        "Frozen queue storage protocol differs",
    )
    for name, expected in control["image_source_sha256"].items():
        path = ROOT / name
        require(
            path.resolve().is_relative_to(ROOT)
            and name.startswith(("src/", "contracts/", "catalog/"))
            and not any(part in ("..", ".") for part in Path(name).parts),
            "Unexpected image source path",
        )
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            "Actual image/source input differs: " + name,
        )
    identity = Identity(**control["identity"])
    contract = monitoring_contract()
    spool = Spool(
        Path("/telemetry/queue.db"),
        identity,
        contract.validate,
        read_receipt,
        schema_version=profile["schema_version"],
        catalog_version=profile["catalog_version"],
        capacity_policy=DropOldestPolicy(loss_record_limit=profile["loss_record_limit"]),
    )
    events = list(control["events"])
    result = {
        "passed": False,
        "action": args.action,
        "role": "telemetry",
        "uid": os.geteuid(),
        "scope": profile["scope"],
        "sqlite_version": sqlite3.sqlite_version,
        "source_hashes_checked": True,
    }
    if args.action in ("nominal", "capacity", "overflow"):
        if not events:
            require(spool.counts()["generated"] == 0, "Refusing a used initial queue")
            spool.start_session()
        target = {
            "nominal": profile["nominal_horizon_events"],
            "capacity": profile["encoded_capacity_events"],
            "overflow": profile["encoded_capacity_events"] + profile["overflow_events"],
        }[args.action]
        require(len(events) < target, "Storage stage does not add the declared originals")
        added = [append(spool, seq, control) for seq in range(len(events), target)]
        events.extend(added)
        result["added_originals"] = added
        result["inventory"] = verify(spool, control, events)
    elif args.action == "full-fault":
        require(os.statvfs("/telemetry").f_bavail == 0, "Telemetry is not physically full")
        try:
            append(spool, len(events), control)
        except sqlite3.Error as exc:
            code = exc.sqlite_errorcode
            require(
                code & 255 in (sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR),
                "Unexpected database failure class",
            )
            result["failed_insert_sqlite_code"] = code
            state = FaultLatch(Path("/critical/fault-state"), identity).trip(
                "telemetry_sqlite_full"
            )
            result["persisted_fault"] = state.body
        else:
            raise RuntimeError("Full telemetry filesystem accepted an unexpected queue insertion")
    elif args.action == "confirm-loss":
        # This fixture has no sender/server and containers have no network. Its evicted
        # originals were never published, so this is a controlled authoritative absence.
        for original in events[: -profile["encoded_capacity_events"]]:
            require(
                spool.confirm_evicted_loss(
                    original["event_id"],
                    original["wire_sha256"],
                    "network-disabled-storage-fixture-never-published",
                ),
                "Controlled never-published loss did not reconcile",
            )
        result["inventory"] = verify(spool, control, events, confirmed=True)
    else:
        result["inventory"] = verify(
            spool, control, events, confirmed=control.get("confirmed", False)
        )
        if control.get("expected_inventory"):
            require(
                result["inventory"]["data_schema_sha256"]
                == control["expected_inventory"]["data_schema_sha256"],
                "Durable queue changed across filesystem failure or reopen",
            )
    usage = os.statvfs("/telemetry")
    result.update(
        passed=True,
        filesystem_bytes=usage.f_blocks * usage.f_frsize,
        available_bytes=usage.f_bavail * usage.f_frsize,
        device=os.stat("/telemetry").st_dev,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
