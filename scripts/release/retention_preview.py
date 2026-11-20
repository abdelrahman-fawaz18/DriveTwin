"""Preview local recording age and database size using the normal read role."""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from evdt.monitoring.database import connection

COUNTS = """
    SELECT
      (SELECT count(*) FROM m_observations WHERE run_id=%(run)s) AS observations,
      (SELECT count(*) FROM m_observations WHERE run_id=%(run)s AND received_at<%(cutoff)s)
        AS older_observations,
      (SELECT min(received_at) FROM m_observations WHERE run_id=%(run)s) AS oldest_received_at,
      (SELECT count(*) FROM m_sample_history WHERE run_id=%(run)s) AS scalar_readings,
      (SELECT count(*) FROM m_sample_history WHERE run_id=%(run)s AND received_at<%(cutoff)s)
        AS older_scalar_readings
"""


def cutoff_time(as_of, age_limit_s):
    if as_of.utcoffset() is None:
        raise ValueError("The preview time requires an explicit UTC offset")
    return as_of.astimezone(timezone.utc) - timedelta(seconds=age_limit_s)


def preview(conn, run, as_of, limits, *, protected=False):
    cutoff = cutoff_time(as_of, limits["raw_retention_max_s"])
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    conn.execute("SET LOCAL statement_timeout = '5s'")
    status = conn.execute(
        "SELECT current_user AS role, current_setting('transaction_read_only') AS read_only"
    ).fetchone()
    if status != {"role": "evdt_read", "read_only": "on"}:
        raise ValueError("The preview requires the ordinary read role and a read-only transaction")
    found = conn.execute("SELECT 1 FROM m_run_definitions WHERE run_id=%s", (run,)).fetchone()
    if not found:
        raise ValueError("No recording matches the selected run")
    counts = conn.execute(COUNTS, {"run": run, "cutoff": cutoff}).fetchone()
    database = conn.execute("SELECT pg_database_size(current_database()) AS bytes").fetchone()
    budget = limits["raw_database_budget_bytes"]
    oldest = counts.pop("oldest_received_at")
    return {
        "scope": "One local recording. Database size includes all relations in the selected database.",
        "run_id": str(run),
        "as_of_utc": as_of.astimezone(timezone.utc).isoformat(),
        "received_before_utc": cutoff.isoformat(),
        "oldest_received_at": oldest.isoformat() if oldest else None,
        "counts": counts,
        "protected_recording": protected,
        "age_candidates": {
            "observations": 0 if protected else counts["older_observations"],
            "scalar_readings": 0 if protected else counts["older_scalar_readings"],
        },
        "database_bytes": database["bytes"],
        "database_budget_bytes": budget,
        "database_over_budget": database["bytes"] > budget,
        "trends": "Summaries are calculated from raw history. No separate trend store is retained.",
        "transaction_read_only": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True, help="Versioned workload policy")
    parser.add_argument("--as-of", type=datetime.fromisoformat, default=None)
    parser.add_argument(
        "--protected", action="store_true", help="Exclude this recording from candidates"
    )
    args = parser.parse_args()
    if os.environ.get("DB_USER") != "evdt_read":
        parser.error("Run the preview from the monitoring reader service")
    run = UUID(os.environ["EVDT_RUN_ID"])
    limits = json.loads(args.policy.read_bytes())["limits"]
    as_of = args.as_of or datetime.now(timezone.utc)
    with connection() as conn:
        report = preview(conn, run, as_of, limits, protected=args.protected)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
