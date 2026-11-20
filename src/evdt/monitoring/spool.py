"""Disk-backed immutable event queue with identity-bound terminal receipts."""

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evdt.monitoring.spool_capacity import DropOldestPolicy


@dataclass(frozen=True)
class Identity:
    run_id: str
    asset_id: str
    source_id: str

    @property
    def topic(self) -> str:
        return f"evdt/simulation/{self.run_id}/{self.asset_id}/{self.source_id}/observations"


@dataclass(frozen=True)
class Pending:
    event_id: str
    raw: bytes
    wire_sha256: str


class SpoolFull(RuntimeError):
    """Producer must apply backpressure; existing unacknowledged data is preserved."""


class Spool:
    def __init__(
        self,
        path: Path,
        identity: Identity,
        validate: Callable[[bytes, str, str], tuple[dict[str, Any], str]],
        read_receipt: Callable[[bytes], dict[str, Any]],
        *,
        max_pending_bytes: int = 32 * 1024 * 1024,
        terminal_history_limit: int = 10000,
        schema_version: str = "2.0.0",
        catalog_version: str = "1.0.0",
        capacity_policy: "DropOldestPolicy | None" = None,
    ) -> None:
        if max_pending_bytes <= 0 or terminal_history_limit < 1:
            raise ValueError("Queue capacity must be positive")
        self.path, self.identity = path, identity
        self.validate, self.read_receipt = validate, read_receipt
        self.max_pending_bytes = max_pending_bytes
        self.terminal_history_limit = terminal_history_limit
        self.schema_version, self.catalog_version = schema_version, catalog_version
        self.capacity_policy = capacity_policy
        if capacity_policy is not None and capacity_policy.encoded_limit != max_pending_bytes:
            raise ValueError("Explicit capacity policy and encoded limit differ")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (
                    id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL,
                    source_epoch INTEGER NOT NULL DEFAULT 0, session_id TEXT,
                    next_seq INTEGER NOT NULL DEFAULT 0,
                    accepted_count INTEGER NOT NULL DEFAULT 0,
                    rejected_count INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pending (
                    event_id TEXT PRIMARY KEY, raw BLOB NOT NULL, wire_sha256 TEXT NOT NULL,
                    source_epoch INTEGER NOT NULL, stream_seq INTEGER NOT NULL,
                    UNIQUE(source_epoch,stream_seq));
                CREATE TABLE IF NOT EXISTS terminal (
                    event_id TEXT PRIMARY KEY, wire_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL, reason TEXT, received_at TEXT NOT NULL);
            """)
            binding = json.dumps(identity.__dict__, sort_keys=True)
            conn.execute("INSERT OR IGNORE INTO metadata(id,identity) VALUES(1,?)", (binding,))
            if conn.execute("SELECT identity FROM metadata WHERE id=1").fetchone()[0] != binding:
                raise ValueError("A spool cannot be reused for another run/asset/source")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS queue_format(id INTEGER PRIMARY KEY CHECK(id=1),schema_version TEXT NOT NULL,catalog_version TEXT NOT NULL)"
            )
            previous = conn.execute(
                "SELECT schema_version,catalog_version FROM queue_format WHERE id=1"
            ).fetchone()
            if previous is None:
                legacy = conn.execute("SELECT raw FROM pending").fetchall()
                if legacy:
                    if any(
                        (
                            json.loads(row["raw"])["schema_version"],
                            json.loads(row["raw"])["catalog_version"],
                        )
                        != (schema_version, catalog_version)
                        for row in legacy
                    ):
                        raise ValueError("Legacy queued format differs; use an explicit migration")
                elif conn.execute("SELECT source_epoch FROM metadata WHERE id=1").fetchone()[0]:
                    raise ValueError("A used legacy queue needs explicit format migration")
                conn.execute(
                    "INSERT INTO queue_format VALUES(1,?,?)", (schema_version, catalog_version)
                )
            elif tuple(previous) != (schema_version, catalog_version):
                raise ValueError("A queue's registered wire/catalog format is immutable")
            if capacity_policy is not None:
                capacity_policy.bind(conn)
            elif conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='queue_capacity'"
            ).fetchone():
                raise ValueError("This queue requires its explicitly bound capacity policy")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            if str(mode).lower() != "delete":
                raise RuntimeError("Rollback-journal mode is required for this queue profile")
            conn.execute("PRAGMA synchronous=EXTRA")
            if conn.execute("PRAGMA synchronous").fetchone()[0] != 3:
                raise RuntimeError("EXTRA synchronization is required for committed queue entries")
            with conn:
                yield conn
        finally:
            conn.close()

    def start_session(self, *, require_fresh: bool = False) -> str:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                require_fresh
                and conn.execute("SELECT source_epoch FROM metadata WHERE id=1").fetchone()[0] != 0
            ):
                raise RuntimeError(
                    "Plant restart requires a new run until checkpoint/resume is implemented"
                )
            session = str(uuid.uuid4())
            conn.execute(
                "UPDATE metadata SET source_epoch=source_epoch+1,session_id=?,next_seq=0 WHERE id=1",
                (session,),
            )
        return session

    def enqueue(
        self,
        samples: list[dict[str, Any]],
        sim_time_s: float,
        *,
        observed_at: str | None = None,
        annotations: dict[str, str] | None = None,
        manifest_sha256: str | None = None,
    ) -> Pending:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute("SELECT * FROM metadata WHERE id=1").fetchone()
            if not state["session_id"]:
                raise RuntimeError("The observation producer must explicitly start a session")
            event_id = str(uuid.uuid4())
            body = {
                **self.identity.__dict__,
                "schema_version": self.schema_version,
                "catalog_version": self.catalog_version,
                "event_id": event_id,
                "session_id": state["session_id"],
                "source_epoch": state["source_epoch"],
                "stream_seq": state["next_seq"],
                "mode": "simulation",
                "clock_domain": "simulation",
                "sim_time_s": sim_time_s,
                "observed_at": observed_at
                or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "samples": samples,
            }
            if annotations is not None:
                body["annotations"] = annotations
            if manifest_sha256 is not None:
                body["manifest_sha256"] = manifest_sha256
            raw = json.dumps(body, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()
            self.validate(raw, self.identity.topic, self.identity.run_id)
            if self.capacity_policy is not None:
                self.capacity_policy.admit(conn, raw, self.max_pending_bytes)
            else:
                size = int(
                    conn.execute("SELECT coalesce(sum(length(raw)),0) FROM pending").fetchone()[0]
                )
                if size + len(raw) > self.max_pending_bytes:
                    raise SpoolFull("Encoded queue limit reached; producer must pause explicitly")
            digest = hashlib.sha256(raw).hexdigest()
            conn.execute(
                "INSERT INTO pending VALUES(?,?,?,?,?)",
                (event_id, raw, digest, state["source_epoch"], state["next_seq"]),
            )
            conn.execute("UPDATE metadata SET next_seq=next_seq+1 WHERE id=1")
            if self.capacity_policy is not None:
                self.capacity_policy.generated(conn)
        return Pending(event_id, raw, digest)

    def pending(self, limit: int = 128) -> list[Pending]:
        if not 1 <= limit <= 1024:
            raise ValueError("Invalid batch size")
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT event_id,raw,wire_sha256 FROM pending ORDER BY source_epoch,stream_seq LIMIT ?",
                (limit,),
            ).fetchall()
            return [Pending(str(r[0]), bytes(r[1]), str(r[2])) for r in rows]

    def acknowledge(self, raw: bytes) -> bool:
        obj = self.read_receipt(raw)
        if any(obj[key] != value for key, value in self.identity.__dict__.items()):
            return False
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT wire_sha256 FROM pending WHERE event_id=?", (obj["event_id"],)
            ).fetchone()
            if row is None:
                return (
                    self.capacity_policy.acknowledge(conn, obj) if self.capacity_policy else False
                )
            if row[0] != obj["wire_sha256"]:
                return False
            conn.execute(
                "INSERT INTO terminal VALUES(?,?,?,?,?)",
                (
                    obj["event_id"],
                    obj["wire_sha256"],
                    obj["status"],
                    obj["reason"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute("DELETE FROM pending WHERE event_id=?", (obj["event_id"],))
            column = "accepted_count" if obj["status"] == "accepted" else "rejected_count"
            conn.execute(f"UPDATE metadata SET {column}={column}+1 WHERE id=1")
            # Bound local receipt detail while preserving the server's durable history.
            conn.execute(
                "DELETE FROM terminal WHERE rowid NOT IN (SELECT rowid FROM terminal ORDER BY rowid DESC LIMIT ?)",
                (self.terminal_history_limit,),
            )
        return True

    def counts(self) -> dict[str, int]:
        with self.connection() as conn:
            # All counters describe one committed snapshot while producer/receipt
            # writers may be active on other connections.
            conn.execute("BEGIN")
            if self.capacity_policy is not None:
                return self.capacity_policy.counts(conn)
            result = {"pending": int(conn.execute("SELECT count(*) FROM pending").fetchone()[0])}
            row = conn.execute(
                "SELECT accepted_count,rejected_count FROM metadata WHERE id=1"
            ).fetchone()
            for name, value in zip(("accepted", "rejected"), row):
                if value:
                    result[name] = int(value)
            return result

    def eviction_audit(self, limit: int = 1000, after: int = 0) -> list[dict[str, Any]]:
        if self.capacity_policy is None:
            raise ValueError("This queue has no eviction policy")
        with self.connection() as conn:
            return self.capacity_policy.losses(conn, limit, after)

    def confirm_evicted_loss(self, event_id: str, digest: str, audit_reference: str) -> bool:
        """Trusted local audit only; external publication/drain proof is the caller's duty."""
        if self.capacity_policy is None:
            raise ValueError("This queue has no eviction policy")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self.capacity_policy.confirm_loss(conn, event_id, digest, audit_reference)
