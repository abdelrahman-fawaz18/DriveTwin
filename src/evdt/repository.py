"""PostgreSQL history and durable rejection accounting for the P0 fixture."""

import hashlib

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from evdt.config import ROOT, db_options


def connection():
    return psycopg.connect(**db_options(), row_factory=dict_row)


def migrate():
    path = ROOT / "migrations/001_observations.sql"
    sql = path.read_text()
    digest = hashlib.sha256(sql.encode()).hexdigest()
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(37110401)")
        exists = conn.execute("SELECT to_regclass('public.schema_migrations') AS name").fetchone()
        if exists["name"]:
            row = conn.execute("SELECT checksum FROM schema_migrations WHERE version=1").fetchone()
            if row is None or row["checksum"] != digest:
                raise RuntimeError("Migration checksum/version mismatch")
        else:
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations(version,checksum) VALUES(1,%s)", (digest,))


def store(raw, topic, received_at, run, contract, retained=False):
    from evdt.contracts import Rejected

    reason = None
    try:
        if retained:
            raise Rejected("retained_not_allowed")
        obj, digest = contract.validate(raw, topic, run)
    except Rejected as exc:
        reason = str(exc)
    with connection() as conn:
        conn.execute(
            "INSERT INTO ingest_counters(run_id) VALUES(%s) ON CONFLICT DO NOTHING", (run,)
        )
        outcome = "rejected"
        if reason is None:
            row = conn.execute(
                """INSERT INTO observations
                (mode,run_id,event_id,asset_id,source_id,session_id,stream_seq,observed_at,
                 received_at,catalog_version,payload_hash,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING RETURNING event_id""",
                (
                    obj["mode"],
                    run,
                    obj["event_id"],
                    obj["asset_id"],
                    obj["source_id"],
                    obj["session_id"],
                    obj["stream_seq"],
                    obj["observed_at"],
                    received_at,
                    obj["catalog_version"],
                    digest,
                    Jsonb(obj),
                ),
            ).fetchone()
            if row:
                outcome = "accepted"
            else:
                old = conn.execute(
                    "SELECT payload_hash FROM observations WHERE mode=%s AND run_id=%s AND event_id=%s",
                    (obj["mode"], run, obj["event_id"]),
                ).fetchone()
                if old and old["payload_hash"] == digest:
                    outcome = "duplicates"
                else:
                    reason = "identity_conflict"
        if reason is not None:
            conn.execute(
                "INSERT INTO quarantine(run_id,received_at,reason,topic,raw_payload) VALUES(%s,%s,%s,%s,%s)",
                (run, received_at, reason, topic, raw),
            )
        # Outcome comes only from the fixed literals above, never from a payload.
        conn.execute(f"UPDATE ingest_counters SET {outcome}={outcome}+1 WHERE run_id=%s", (run,))
    return {"outcome": outcome, "reason": reason}


if __name__ == "__main__":
    migrate()
