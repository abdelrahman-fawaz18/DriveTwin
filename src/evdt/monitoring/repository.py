"""Transactionally persist simulation history, projections, and retryable receipts."""

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from evdt.config import ROOT
from evdt.contracts import Rejected
from evdt.monitoring.contracts import receipt, receipt_identity
from evdt.monitoring.database import connection
from evdt.monitoring.trace import emit


def migrate():
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(37110402)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS m_migrations(name text PRIMARY KEY,checksum text NOT NULL)"
        )
        for path in sorted((ROOT / "migrations/monitoring").glob("*.sql")):
            content = path.read_text()
            digest = hashlib.sha256(content.encode()).hexdigest()
            previous = conn.execute(
                "SELECT checksum FROM m_migrations WHERE name=%s", (path.name,)
            ).fetchone()
            if previous and previous["checksum"] != digest:
                raise RuntimeError("Applied monitoring migration changed")
            if not previous:
                conn.execute(content)
                conn.execute("INSERT INTO m_migrations VALUES(%s,%s)", (path.name, digest))
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        for role, file in [
            ("evdt_ingest", "INGEST_DB_PASSWORD_FILE"),
            ("evdt_read", "READ_DB_PASSWORD_FILE"),
            ("evdt_registry", "REGISTRY_DB_PASSWORD_FILE"),
            ("evdt_replay", "REPLAY_DB_PASSWORD_FILE"),
        ]:
            password = Path(os.environ[file]).read_text().strip()
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(
                sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        conn.execute(
            "GRANT SELECT,INSERT ON m_sessions,m_observations,m_receipts,m_quarantine TO evdt_ingest"
        )
        conn.execute("GRANT SELECT,INSERT,UPDATE ON m_current TO evdt_ingest")
        conn.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO evdt_ingest")
        conn.execute("GRANT SELECT ON m_observations,m_current,m_migrations TO evdt_read")
        conn.execute("GRANT SELECT ON m_energy_assignments,m_energy_forecasts TO evdt_read")
        conn.execute(
            "GRANT SELECT ON m_run_definitions,m_registrations,m_source_heads,m_retired_sources,m_source_clocks,m_clock_events TO evdt_read,evdt_ingest,evdt_registry"
        )
        conn.execute("GRANT SELECT ON m_observations TO evdt_registry")
        conn.execute(
            "GRANT INSERT ON m_registrations,m_retired_sources,m_clock_events TO evdt_registry"
        )
        conn.execute("GRANT INSERT,UPDATE ON m_source_heads,m_source_clocks TO evdt_registry")
        conn.execute("GRANT SELECT ON m_dashboard_heads,m_sample_history TO evdt_read")
        conn.execute("GRANT SELECT,INSERT,UPDATE ON m_dashboard_heads TO evdt_ingest,evdt_registry")
        conn.execute("GRANT INSERT ON m_sample_history TO evdt_ingest")
        conn.execute(
            "GRANT SELECT ON m_replay_packets,m_replay_seed_samples,m_replay_definitions,m_replay_recipes,m_replay_contexts,m_replay_heads TO evdt_replay"
        )
        conn.execute(
            "GRANT SELECT ON m_map_contexts,m_dashboard_events,m_scoped_rejections TO evdt_read"
        )
        conn.execute("GRANT INSERT ON m_dashboard_events TO evdt_ingest,evdt_registry")
        conn.execute(
            "GRANT EXECUTE ON FUNCTION m_dashboard_next(uuid,text) TO evdt_ingest,evdt_registry"
        )
        from evdt.monitoring.registry import provision

        provision(conn, Path(os.environ["RUN_POLICY_FILE"]).read_bytes())
        from evdt.monitoring.map_context import provision as provision_context

        provision_context(conn, os.environ.get("MAP_CONTEXT_FILE"))


def store(raw, topic, run, contract, *, retained=False, received_at=None):
    now = received_at or datetime.now(timezone.utc)
    wire = hashlib.sha256(raw).hexdigest()
    obj, reason = None, None
    try:
        obj, _ = contract.validate(raw, topic, run)
        if retained:
            raise Rejected("retained_not_allowed")
        observed = datetime.fromisoformat(obj["observed_at"].replace("Z", "+00:00"))
        if observed > now + timedelta(seconds=contract.profile["max_future_clock_skew_s"]):
            raise Rejected("future_timestamp")
    except Rejected as exc:
        reason = str(exc)
        if obj is None:
            obj = receipt_identity(raw, topic, run, contract.catalog)
    response = None
    with connection() as conn:
        # Only a fully validated identity can receive a terminal persistence receipt.
        if obj is not None:
            from evdt.monitoring.registry import source_lock

            source_lock(conn, obj)
            previous = conn.execute(
                "SELECT receipt FROM m_receipts WHERE run_id=%s AND event_id=%s AND wire_sha256=%s",
                (run, obj["event_id"], wire),
            ).fetchone()
            if previous:
                return {"outcome": "duplicate", "receipt": bytes(previous["receipt"]), "obj": obj}
        outcome = "rejected"
        admission = {}
        if reason is None:
            try:
                from evdt.monitoring.registry import admit

                admission = admit(conn, obj, now)
            except Rejected as exc:
                reason = str(exc)
        if reason is None:
            conn.execute(
                "INSERT INTO m_sessions VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (run, obj["asset_id"], obj["source_id"], obj["source_epoch"], obj["session_id"]),
            )
            session = conn.execute(
                "SELECT session_id FROM m_sessions WHERE run_id=%s AND asset_id=%s AND source_id=%s AND source_epoch=%s",
                (run, obj["asset_id"], obj["source_id"], obj["source_epoch"]),
            ).fetchone()
            if not session or session["session_id"] != uuid.UUID(obj["session_id"]):
                reason = "source_epoch_conflict"
            else:
                row = conn.execute(
                    """INSERT INTO m_observations
                    (run_id,event_id,asset_id,source_id,source_epoch,session_id,stream_seq,sim_time_s,
                     observed_at,received_at,wire_sha256,raw_payload,payload,admission)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING event_id""",
                    (
                        run,
                        obj["event_id"],
                        obj["asset_id"],
                        obj["source_id"],
                        obj["source_epoch"],
                        obj["session_id"],
                        obj["stream_seq"],
                        obj["sim_time_s"],
                        obj["observed_at"],
                        now,
                        wire,
                        raw,
                        Jsonb(obj),
                        Jsonb(admission),
                    ),
                ).fetchone()
                if row:
                    outcome = "accepted"
                    for sample in obj["samples"]:
                        conn.execute(
                            """INSERT INTO m_current VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(run_id,asset_id,signal_id) DO UPDATE SET
                        event_id=excluded.event_id,source_id=excluded.source_id,source_epoch=excluded.source_epoch,
                        stream_seq=excluded.stream_seq,sim_time_s=excluded.sim_time_s,
                        observed_at=excluded.observed_at,sample=excluded.sample
                        WHERE (excluded.sim_time_s,excluded.source_epoch,excluded.stream_seq) >
                              (m_current.sim_time_s,m_current.source_epoch,m_current.stream_seq)""",
                            (
                                run,
                                obj["asset_id"],
                                sample["signal_id"],
                                obj["event_id"],
                                obj["source_id"],
                                obj["source_epoch"],
                                obj["stream_seq"],
                                obj["sim_time_s"],
                                obj["observed_at"],
                                Jsonb(sample),
                            ),
                        )
                    emit("projection_staged", obj)
                else:
                    previous = conn.execute(
                        "SELECT receipt FROM m_receipts WHERE run_id=%s AND event_id=%s AND wire_sha256=%s",
                        (run, obj["event_id"], wire),
                    ).fetchone()
                    if previous:
                        return {
                            "outcome": "duplicate",
                            "receipt": bytes(previous["receipt"]),
                            "obj": obj,
                        }
                    reason = "identity_conflict"
        if reason is not None:
            conn.execute(
                "INSERT INTO m_quarantine(run_id,received_at,reason,wire_sha256,raw_payload,asset_id) VALUES(%s,%s,%s,%s,%s,%s)",
                (run, now, reason, wire, raw, obj["asset_id"] if obj is not None else None),
            )
        if obj is not None:
            response = receipt(
                obj, raw, "accepted" if outcome == "accepted" else "rejected", reason
            )
            conn.execute(
                "INSERT INTO m_receipts VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (run, obj["event_id"], wire, response),
            )
    emit("transaction_committed", obj, outcome=outcome, wire_sha256=wire)
    return {"outcome": outcome, "reason": reason, "receipt": response, "obj": obj}


if __name__ == "__main__":
    migrate()
