"""Exercise forecast snapshot selection in a disposable PostgreSQL database; no bus."""

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / p) for p in ("src", "tests/unit", "scripts/route_aware_energy")]

from psycopg import sql  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from service_session import DatabaseSession  # noqa: E402
from test_energy_route_product import RUN, product_case  # noqa: E402

from evdt.energy.route_aware.product import prepare_input  # noqa: E402
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint  # noqa: E402
from evdt.monitoring.route_energy_views import collect, provision  # noqa: E402


def check(conn, case):
    args, assignment, _ = product_case()
    scope = replace(args["scope"], session_id="00000000-0000-0000-0000-000000000002")
    key = (RUN, scope.asset_id, scope.source_id, scope.source_epoch)
    conn.execute(
        "INSERT INTO m_run_definitions(run_id,policy_sha256,raw_policy,policy) VALUES(%s,%s,%s,%s)",
        (RUN, "a" * 64, b"{}", Jsonb({})),
    )
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    stamp = now.timestamp()
    base = replace(args["context"], scope=scope, observed_real_life_s=stamp)
    head = replace(base, working_s=35)
    state = "running"
    if case == "route_change":
        head = replace(head, plan=replace(head.plan, remaining_stops=()))
    elif case == "charging":
        head = replace(head, charging_state="charging")
    elif case == "too_far_behind":
        head = replace(head, working_s=61)
    elif case == "stale":
        base = replace(base, observed_real_life_s=stamp - 6)
    elif case == "paused":
        state = "paused"
    conn.execute(
        """INSERT INTO m_registrations
        (run_id,asset_id,source_id,source_epoch,session_id,manifest_sha256,
        minimum_sim_time_s,raw_manifest,manifest) VALUES(%s,%s,%s,%s,%s,%s,0,%s,%s)""",
        (*key, scope.session_id, base.manifest_sha256, b"{}", Jsonb({})),
    )
    conn.execute("INSERT INTO m_source_heads VALUES(%s,%s,%s,%s)", key)
    conn.execute(
        """INSERT INTO m_source_clocks
        (run_id,asset_id,source_id,source_epoch,revision,state,sim_time_s,observed_at,received_at)
        VALUES(%s,%s,%s,%s,1,%s,%s,%s,%s)""",
        (*key, state, head.working_s, now, now),
    )
    for context in (replace(base, working_s=29), base, head):
        if case == "missing_head" and context is head:
            continue
        conn.execute(
            """INSERT INTO m_route_contexts
            (run_id,asset_id,source_id,source_epoch,session_id,context_sha256,plan_sha256,
            working_s,raw_context,context) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                *key,
                scope.session_id,
                fingerprint(context),
                context.plan.revision,
                context.working_s,
                canonical_bytes(context),
                Jsonb(json.loads(canonical_bytes(context))),
            ),
        )
        if context is head and case != "caught_up":
            continue
        packet = dict(
            args["packet"].payload,
            session_id=scope.session_id,
            event_id=str(uuid4()),
            sim_time_s=context.working_s,
            annotations={"energy_context": fingerprint(context)},
        )
        if case == "wrong_binding" and context is base:
            packet["annotations"]["energy_context"] = "f" * 64
        at = datetime.fromtimestamp(context.observed_real_life_s, timezone.utc)
        conn.execute(
            """INSERT INTO m_observations
            (run_id,asset_id,source_id,source_epoch,session_id,event_id,stream_seq,
             sim_time_s,observed_at,received_at,wire_sha256,raw_payload,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                *key,
                scope.session_id,
                packet["event_id"],
                int(context.working_s),
                context.working_s,
                at,
                now,
                "a" * 64,
                canonical_bytes(packet),
                Jsonb(packet),
            ),
        )
    provision(conn, assignment)
    # Read through the same database role as the Monitoring API.
    conn.execute("SET LOCAL ROLE evdt_read")
    snapshot = collect(conn, RUN, scope.asset_id)
    if case in ("delivery_lag", "caught_up", "wrong_binding"):
        assert snapshot["reason"] is None, snapshot["reason"]
        cutoff = {"delivery_lag": 30, "caught_up": 35, "wrong_binding": 29}[case]
        assert snapshot["packet"]["payload"]["sim_time_s"] == cutoff
        assert snapshot["clock"]["working_s"] == 35
        _, job = prepare_input(
            snapshot,
            run_id=RUN,
            asset_id=scope.asset_id,
            prepare_route=lambda a, c: args["prepared"],
        )
        assert job.state.working_s == cutoff
    else:
        expected = {
            "route_change": "current_route_plan_changed",
            "charging": "charging_charging",
            "too_far_behind": "current_bound_observation_not_ready",
            "stale": "required_input_stale_or_future",
            "paused": "source_paused",
            "missing_head": "current_bound_observation_not_ready",
        }
        assert snapshot["reason"] == expected[case], snapshot["reason"]


def main():
    output = ROOT / "evidence/energy-v2" / ("input-delivery-" + uuid4().hex[:12])
    output.mkdir()
    session = DatabaseSession(output)
    report = {"passed": False, "bus_started": False, "cases": [], "cleanup_verified": False}
    os.environ["EVDT_RUN_ID"] = RUN
    try:
        session.start()
        with session.connect() as conn:
            for role in (
                "evdt_ingest",
                "evdt_read",
                "evdt_registry",
                "evdt_replay",
                "evdt_forecast",
            ):
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
            for path in sorted((ROOT / "migrations/monitoring").glob("*.sql")):
                conn.execute(path.read_text())
            conn.execute("GRANT USAGE ON SCHEMA public TO evdt_read")
            conn.execute(
                "GRANT SELECT ON m_source_clocks,m_source_heads,m_registrations,m_observations TO evdt_read"
            )
        for case in (
            "delivery_lag",
            "caught_up",
            "wrong_binding",
            "route_change",
            "charging",
            "too_far_behind",
            "stale",
            "paused",
            "missing_head",
        ):
            with session.connect() as conn, conn.transaction(force_rollback=True):
                check(conn, case)
            report["cases"].append({"case": case, "passed": True})
        report["passed"] = True
    except Exception as exc:
        report["error"] = repr(exc)
    finally:
        try:
            session.finish()
            report["cleanup_verified"] = True
        except Exception as exc:
            report.update(passed=False, cleanup_error=repr(exc))
        (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
        print("Results:", output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
