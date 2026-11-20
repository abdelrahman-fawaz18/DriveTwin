"""Producer-authenticated, immutable route metadata for the energy service."""

from dataclasses import asdict
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from evdt.contract_profile import Rejected
from evdt.energy.route_aware.context import parse_context
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint
from evdt.monitoring.registry import binding, source_lock
from evdt.monitoring.repository import connection


def register_context(raw):
    context = parse_context(raw)
    identity = asdict(context.scope)
    identity["manifest_sha256"] = context.manifest_sha256
    key = (context.scope.run_id, context.scope.asset_id, context.scope.source_id)
    content = fingerprint(context)
    now = datetime.now(timezone.utc).timestamp()
    with connection() as conn:
        source_lock(conn, identity)
        registration = binding(conn, identity)
        prior = conn.execute(
            "SELECT context_sha256 FROM m_route_contexts WHERE run_id=%s AND context_sha256=%s",
            (key[0], content),
        ).fetchone()
        if prior:
            return {"accepted": True, "context_sha256": content, "duplicate": True}
        if (
            not -0.05
            <= now - context.observed_real_life_s
            <= registration["manifest"]["heartbeat_deadline_s"]
        ):
            raise Rejected("route_context_timestamp")
        head = conn.execute(
            "SELECT * FROM m_source_clocks WHERE run_id=%s AND asset_id=%s AND source_id=%s", key
        ).fetchone()
        if (
            head["source_epoch"] != context.scope.source_epoch
            or head["state"] != "running"
            or abs(head["sim_time_s"] - context.working_s) > 1e-6
        ):
            raise Rejected("route_context_clock")
        conn.execute(
            """INSERT INTO m_route_contexts
            (run_id,asset_id,source_id,source_epoch,session_id,context_sha256,
             plan_sha256,working_s,raw_context,context)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                *key,
                context.scope.source_epoch,
                context.scope.session_id,
                content,
                context.plan.revision,
                context.working_s,
                canonical_bytes(context),
                Jsonb(asdict(context)),
            ),
        )
    return {"accepted": True, "context_sha256": content, "duplicate": False}
