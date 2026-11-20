"""Opt-in atomic eviction bookkeeping; no physical filesystem quota claim."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from evdt.monitoring.spool import SpoolFull


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DropOldestPolicy:
    encoded_limit: int = 32 * 1024 * 1024
    loss_record_limit: int = 16384

    def __post_init__(self) -> None:
        if (
            type(self.encoded_limit) is not int
            or self.encoded_limit < 1
            or type(self.loss_record_limit) is not int
            or not 1 <= self.loss_record_limit <= 1000000
        ):
            raise ValueError("Invalid explicit queue-policy capacity")

    def bind(self, conn: sqlite3.Connection) -> None:
        binding = json.dumps(
            {
                "version": "oldest-first/1.0.0",
                "encoded_limit": self.encoded_limit,
                "loss_record_limit": self.loss_record_limit,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='queue_capacity'"
        ).fetchone()
        if exists:
            row = conn.execute("SELECT binding FROM queue_capacity WHERE id=1").fetchone()
            if row is None or row[0] != binding:
                raise ValueError("Queue capacity policy is immutable")
            return
        state = conn.execute("SELECT * FROM metadata WHERE id=1").fetchone()
        if (
            any(
                state[key]
                for key in ("source_epoch", "next_seq", "accepted_count", "rejected_count")
            )
            or conn.execute("SELECT count(*) FROM pending").fetchone()[0]
            or conn.execute("SELECT count(*) FROM terminal").fetchone()[0]
        ):
            raise ValueError("A used legacy queue needs explicit capacity-policy migration")
        conn.execute(
            "CREATE TABLE queue_capacity(id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL,generated INTEGER NOT NULL DEFAULT 0,evicted INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute("INSERT INTO queue_capacity(id,binding) VALUES(1,?)", (binding,))
        conn.execute("""CREATE TABLE queue_evictions(
            event_id TEXT PRIMARY KEY,wire_sha256 TEXT NOT NULL,
            source_epoch INTEGER NOT NULL,stream_seq INTEGER NOT NULL,
            sim_time_s TEXT NOT NULL,observed_at TEXT NOT NULL,evicted_at TEXT NOT NULL,
            disposition TEXT NOT NULL CHECK(disposition IN ('unconfirmed','dropped','accepted','rejected')),
            drop_audit TEXT,receipt TEXT,resolved_at TEXT,
            UNIQUE(source_epoch,stream_seq))""")

    def admit(self, conn: sqlite3.Connection, raw: bytes, limit: int) -> None:
        if limit != self.encoded_limit:
            raise ValueError("Active queue limit differs from its bound policy")
        if len(raw) > limit:
            raise SpoolFull("One event exceeds the encoded queue limit; nothing evicted")
        size = conn.execute("SELECT coalesce(sum(length(raw)),0) FROM pending").fetchone()[0]
        needed = size + len(raw) - limit
        if needed <= 0:
            return
        selected = []
        for row in conn.execute(
            "SELECT event_id,raw,wire_sha256,source_epoch,stream_seq FROM pending ORDER BY source_epoch,stream_seq"
        ):
            selected.append(row)
            needed -= len(row["raw"])
            if needed <= 0:
                break
        existing = conn.execute("SELECT count(*) FROM queue_evictions").fetchone()[0]
        if needed > 0 or existing + len(selected) > self.loss_record_limit:
            raise SpoolFull("Durable eviction audit capacity exhausted; existing entries preserved")
        stamp = utc()
        for row in selected:
            body = json.loads(row["raw"])
            conn.execute(
                "INSERT INTO queue_evictions(event_id,wire_sha256,source_epoch,stream_seq,sim_time_s,observed_at,evicted_at,disposition) VALUES(?,?,?,?,?,?,?,'unconfirmed')",
                (
                    row["event_id"],
                    row["wire_sha256"],
                    row["source_epoch"],
                    row["stream_seq"],
                    json.dumps(body["sim_time_s"], allow_nan=False),
                    body["observed_at"],
                    stamp,
                ),
            )
            conn.execute("DELETE FROM pending WHERE event_id=?", (row["event_id"],))
        conn.execute("UPDATE queue_capacity SET evicted=evicted+? WHERE id=1", (len(selected),))

    def generated(self, conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE queue_capacity SET generated=generated+1 WHERE id=1")

    def acknowledge(self, conn: sqlite3.Connection, obj: dict[str, Any]) -> bool:
        row = conn.execute(
            "SELECT * FROM queue_evictions WHERE event_id=?", (obj["event_id"],)
        ).fetchone()
        if (
            row is None
            or row["wire_sha256"] != obj["wire_sha256"]
            or row["disposition"] in ("accepted", "rejected")
        ):
            return False
        column = "accepted_count" if obj["status"] == "accepted" else "rejected_count"
        conn.execute(f"UPDATE metadata SET {column}={column}+1 WHERE id=1")
        conn.execute(
            "UPDATE queue_evictions SET disposition=?,receipt=?,resolved_at=? WHERE event_id=?",
            (obj["status"], json.dumps(obj, sort_keys=True), utc(), obj["event_id"]),
        )
        return True

    def confirm_loss(
        self, conn: sqlite3.Connection, event_id: str, digest: str, audit_reference: str
    ) -> bool:
        if (
            not isinstance(audit_reference, str)
            or not audit_reference.strip()
            or len(audit_reference) > 512
            or any(ord(ch) < 32 for ch in audit_reference)
        ):
            raise ValueError("Loss confirmation needs a bounded durable audit reference")
        row = conn.execute("SELECT * FROM queue_evictions WHERE event_id=?", (event_id,)).fetchone()
        if row is None or row["wire_sha256"] != digest or row["disposition"] != "unconfirmed":
            return False
        conn.execute(
            "UPDATE queue_evictions SET disposition='dropped',drop_audit=?,resolved_at=? WHERE event_id=?",
            (audit_reference, utc(), event_id),
        )
        return True

    def counts(self, conn: sqlite3.Connection) -> dict[str, int]:
        state = conn.execute("SELECT * FROM metadata WHERE id=1").fetchone()
        capacity = conn.execute("SELECT * FROM queue_capacity WHERE id=1").fetchone()
        dispositions = dict(
            conn.execute("SELECT disposition,count(*) FROM queue_evictions GROUP BY disposition")
        )
        queued = conn.execute("SELECT count(*) FROM pending").fetchone()[0]
        unconfirmed = dispositions.get("unconfirmed", 0)
        result: dict[str, int] = {
            "generated": capacity["generated"],
            "queued": queued,
            "pending": queued + unconfirmed,
            "accepted": state["accepted_count"],
            "rejected": state["rejected_count"],
            "dropped": dispositions.get("dropped", 0),
            "evicted_unconfirmed": unconfirmed,
            "evicted_lifetime": capacity["evicted"],
        }
        if result["generated"] != sum(
            result[key] for key in ("accepted", "rejected", "dropped", "pending")
        ):
            raise RuntimeError("Durable queue event accounting is inconsistent")
        return result

    def losses(
        self, conn: sqlite3.Connection, limit: int = 1000, after: int = 0
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000 or type(after) is not int or after < 0:
            raise ValueError("Invalid bounded eviction audit page")
        result = []
        for row in conn.execute(
            "SELECT rowid AS audit_sequence,* FROM queue_evictions WHERE rowid>? ORDER BY rowid LIMIT ?",
            (after, limit),
        ):
            item = dict(row)
            # Preserve admitted JSON numeric values, integer kind and signed zero.
            item["sim_time_s"] = json.loads(item["sim_time_s"])
            result.append(item)
        return result
