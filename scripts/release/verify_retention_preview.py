"""Exercise the retention preview against a small owned PostgreSQL recording."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from retention_preview import cutoff_time, preview

from evdt.monitoring.database import connection
from evdt.monitoring.repository import migrate
from evdt.monitoring.run_policy import encoded


def seed(conn, run, index, timestamp):
    packet = {
        "run_id": str(run),
        "event_id": str(uuid4()),
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "session_id": str(run),
        "source_epoch": 1,
        "stream_seq": index,
        "sim_time_s": index + 1,
        "samples": [{"signal_id": "motion.speed", "unit": "m/s", "value": index}],
    }
    raw = encoded(packet)
    conn.execute(
        """INSERT INTO m_observations(run_id,event_id,asset_id,source_id,source_epoch,
           session_id,stream_seq,sim_time_s,observed_at,received_at,wire_sha256,raw_payload,payload)
           VALUES (%s,%s,'bus-001','sumo-adapter',1,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            run,
            packet["event_id"],
            run,
            index,
            index + 1,
            timestamp,
            timestamp,
            hashlib.sha256(raw).hexdigest(),
            raw,
            Jsonb(packet),
        ),
    )


def inventory(conn):
    return conn.execute(
        "SELECT run_id,event_id,raw_payload,received_at FROM m_observations ORDER BY run_id,event_id"
    ).fetchall()


def require(condition, description):
    if not condition:
        raise RuntimeError(description)


def main():
    migrate()
    run = UUID(os.environ["EVDT_RUN_ID"])
    other = uuid4()
    as_of = datetime(2026, 11, 6, tzinfo=timezone.utc)
    limits = json.loads(Path("/fixture/policy.json").read_bytes())["limits"]
    cutoff = cutoff_time(as_of, limits["raw_retention_max_s"])
    admin = os.environ["DB_PASSWORD_FILE"]
    with connection() as conn:
        for index, moment in enumerate((cutoff - timedelta(seconds=1), cutoff, as_of)):
            seed(conn, run, index, moment)
        seed(conn, other, 0, cutoff - timedelta(seconds=30))
        original = inventory(conn)
    os.environ.update(DB_USER="evdt_read", DB_PASSWORD_FILE=os.environ["READ_DB_PASSWORD_FILE"])
    with connection() as conn:
        normal = preview(conn, run, as_of, limits)
    with connection() as conn:
        protected = preview(conn, run, as_of, limits, protected=True)
    with connection() as conn:
        over_budget = preview(conn, run, as_of, {**limits, "raw_database_budget_bytes": 1})
    os.environ.update(DB_USER="evdt_admin", DB_PASSWORD_FILE=admin)
    with connection() as conn:
        require(inventory(conn) == original, "A read-only preview changed the stored originals")
    require(
        normal["counts"]
        == {
            "observations": 3,
            "older_observations": 1,
            "scalar_readings": 3,
            "older_scalar_readings": 1,
        }
        and normal["age_candidates"] == {"observations": 1, "scalar_readings": 1}
        and normal["transaction_read_only"]
        and not normal["database_over_budget"]
        and protected["counts"] == normal["counts"]
        and protected["age_candidates"] == {"observations": 0, "scalar_readings": 0}
        and over_budget["database_over_budget"],
        "The retention preview did not preserve its age, protection, size or run boundary",
    )
    print(
        json.dumps(
            {
                "passed": True,
                "observations_in_preview": 3,
                "older_observations": 1,
                "protected_candidates": 0,
                "database_budget_exceedance_detected": True,
                "run_scope_verified": True,
                "database_read_only_and_originals_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
