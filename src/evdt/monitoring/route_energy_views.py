"""Scoped input projection and immutable route-aware forecast records."""

import json
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from evdt.energy.route_aware.admission import (
    AcceptedPacket,
    InputUnavailable,
    TrustedClock,
    validate_current_context,
)
from evdt.energy.route_aware.assignment import Assignment
from evdt.energy.route_aware.context import SourcePlan, parse_context
from evdt.energy.route_aware.contracts import SourceScope
from evdt.energy.route_aware.forecast import validate_result
from evdt.energy.route_aware.residuals import Residual, persistent_mismatch
from evdt.energy.route_aware.wire import canonical_bytes, decode_record, fingerprint
from evdt.monitoring.charging_startup import configured_delay, startup_waiting
from evdt.monitoring.database import connection

INPUT_VERSION = "trusted-route-energy-input/2.1.0"
VIEW_VERSION = "route-energy-view/2.0.0"


def provision(conn, assignment):
    """Administrator-only CLI. Browser and forecast-writer roles cannot assign."""
    if not isinstance(assignment, Assignment) or assignment.run_id != os.environ["EVDT_RUN_ID"]:
        raise ValueError("Assignment type or run mismatch")
    raw = canonical_bytes(assignment)
    if len(raw) > 1_048_576:
        raise ValueError("Assignment byte budget exceeded")
    key = (assignment.run_id, assignment.asset_id)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,730128003))", (":".join(key),))
    row = conn.execute(
        "SELECT revision,assignment_sha256 FROM m_route_energy_assignments WHERE run_id=%s AND asset_id=%s ORDER BY revision DESC LIMIT 1",
        key,
    ).fetchone()
    if (
        row
        and row["revision"] == assignment.revision
        and row["assignment_sha256"] == fingerprint(assignment)
    ):
        return False
    if assignment.revision != (row["revision"] + 1 if row else 1):
        raise ValueError("Assignment revisions must advance exactly once")
    conn.execute(
        "INSERT INTO m_route_energy_assignments(run_id,asset_id,source_id,revision,assignment_sha256,assignment) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            *key,
            assignment.source_id,
            assignment.revision,
            fingerprint(assignment),
            Jsonb(json.loads(raw)),
        ),
    )
    return True


def latest_assignment(conn, run, asset):
    row = conn.execute(
        "SELECT assignment_sha256,assignment FROM m_route_energy_assignments WHERE run_id=%s AND asset_id=%s ORDER BY revision DESC LIMIT 1",
        (run, asset),
    ).fetchone()
    if not row:
        return None
    result = decode_record(Assignment, row["assignment"])
    if fingerprint(result) != row["assignment_sha256"] or (result.run_id, result.asset_id) != (
        str(run),
        asset,
    ):
        raise ValueError("Stored assignment integrity failure")
    return result


def collect(conn, run, asset):
    """Return a JSON-ready snapshot from a scoped, repeatable-read transaction."""
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"].timestamp()
    assignment = latest_assignment(conn, run, asset)
    value = {
        "version": INPUT_VERSION,
        "run_id": str(run),
        "asset_id": asset,
        "as_of_real_life_s": now,
        "assignment": jsonable_encoder(assignment) if assignment else None,
        "assignment_sha256": fingerprint(assignment) if assignment else None,
        "clock": None,
        "packet": None,
        "context": None,
        "current_context": None,
        "reason": None,
    }
    if not assignment or not assignment.enabled:
        value["reason"] = "forecast_not_configured" if assignment is None else "forecast_disabled"
        return value
    clock = conn.execute(
        """SELECT c.*,r.session_id,r.manifest_sha256 FROM m_source_clocks c
        JOIN m_source_heads h USING(run_id,asset_id,source_id,source_epoch)
        JOIN m_registrations r USING(run_id,asset_id,source_id,source_epoch)
        WHERE c.run_id=%s AND c.asset_id=%s AND c.source_id=%s""",
        (run, asset, assignment.source_id),
    ).fetchone()
    if not clock:
        value["reason"] = "source_identity_not_ready"
        return value
    scope = SourceScope(
        str(run), asset, assignment.source_id, clock["source_epoch"], str(clock["session_id"])
    )
    trusted_clock = TrustedClock(
        scope,
        clock["state"],
        clock["sim_time_s"],
        clock["observed_at"].timestamp(),
        clock["received_at"].timestamp(),
        clock["manifest_sha256"],
    )
    value["clock"] = jsonable_encoder(trusted_clock)
    if clock["state"] != "running":
        value["reason"] = "source_" + clock["state"]
        return value
    if any(
        not -0.05 <= now - stamp <= assignment.policy.source_max_age_real_life_s
        for stamp in (trusted_clock.observed_real_life_s, trusted_clock.received_real_life_s)
    ):
        value["reason"] = "source_stale_or_future"
        return value
    current_row = conn.execute(
        """SELECT raw_context FROM m_route_contexts
        WHERE run_id=%s AND asset_id=%s AND source_id=%s AND source_epoch=%s
        AND session_id=%s AND working_s=%s""",
        (
            run,
            asset,
            scope.source_id,
            scope.source_epoch,
            scope.session_id,
            trusted_clock.working_s,
        ),
    ).fetchone()
    if current_row is None:
        value["reason"] = "current_bound_observation_not_ready"
        return value
    current_context = parse_context(bytes(current_row["raw_context"]))
    value["current_context"] = jsonable_encoder(current_context)
    row = conn.execute(
        """SELECT o.payload,o.wire_sha256,o.observed_at,o.received_at,x.raw_context
        FROM m_observations o JOIN m_route_contexts x
        ON (x.run_id,x.asset_id,x.source_id,x.source_epoch,x.session_id,x.working_s)=
           (o.run_id,o.asset_id,o.source_id,o.source_epoch,o.session_id,o.sim_time_s)
        AND x.context_sha256=o.payload->'annotations'->>'energy_context'
        WHERE o.run_id=%s AND o.asset_id=%s AND o.source_id=%s AND o.source_epoch=%s
        AND o.session_id=%s AND o.sim_time_s BETWEEN %s AND %s
        ORDER BY o.sim_time_s DESC,o.event_id LIMIT 2""",
        (
            run,
            asset,
            scope.source_id,
            scope.source_epoch,
            scope.session_id,
            max(0, trusted_clock.working_s - assignment.policy.observation_lag_limit_working_s),
            trusted_clock.working_s,
        ),
    ).fetchall()
    if not row or (
        len(row) == 2 and row[0]["payload"]["sim_time_s"] == row[1]["payload"]["sim_time_s"]
    ):
        value["reason"] = "current_bound_observation_not_ready"
        return value
    observation = row[0]
    context = parse_context(bytes(observation["raw_context"]))
    packet = AcceptedPacket(
        observation["payload"],
        observation["wire_sha256"],
        observation["observed_at"].timestamp(),
        observation["received_at"].timestamp(),
    )
    try:
        validate_current_context(trusted_clock, context, current_context, assignment.policy, now)
    except InputUnavailable as exc:
        value["reason"] = exc.reason
    if any(
        not -0.05 <= now - stamp <= assignment.policy.source_max_age_real_life_s
        for stamp in (
            packet.observed_real_life_s,
            packet.received_real_life_s,
            context.observed_real_life_s,
        )
    ):
        value["reason"] = "required_input_stale_or_future"
    elif context.charging_state != "not_charging":
        value["reason"] = "charging_" + context.charging_state
    value["packet"], value["context"] = jsonable_encoder(packet), jsonable_encoder(context)
    return value


def input_view(asset):
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        return collect(conn, os.environ["EVDT_RUN_ID"], asset)


def save_result(conn, assignment, claim, completion):
    """Called in the same transaction as closing the owned durable claim."""
    request, result = claim.request, completion.result
    validate_result(request, result)
    scope = request.state.scope
    if (
        (scope.run_id, scope.asset_id, scope.source_id)
        != (assignment.run_id, assignment.asset_id, assignment.source_id)
        or request.route.network_sha256 != assignment.network_sha256
        or request.vehicle != assignment.vehicle
        or request.conditions != assignment.conditions
        or request.policy != assignment.policy
        or (assignment.scenario_route_revision is None and request.scenario != assignment.scenario)
        or (
            assignment.scenario_route_revision is not None
            and request.route.revision != assignment.scenario_route_revision
        )
        or request.assignment_sha256 != fingerprint(assignment)
        or completion.fence != claim.fence
    ):
        raise ValueError("Result differs from its assigned source, model, or fenced claim")
    # The trusted input builder projects localized assumptions onto the remaining
    # route. Their indices need not equal the original assignment's indices.
    # Persist only the exact request durably claimed after that admission. The
    # worker cannot replace the projection, assignment, owner, or terminal state.
    job = conn.execute(
        """SELECT owner,run_id,asset_id,generation,input_sha256,request,status
        FROM m_route_forecast_jobs WHERE fence=%s""",
        (claim.fence,),
    ).fetchone()
    if (
        not job
        or (str(job["owner"]), str(job["run_id"]), job["asset_id"])
        != (claim.owner, scope.run_id, scope.asset_id)
        or job["generation"] != request.generation
        or job["input_sha256"] != fingerprint(request)
        or job["request"] != json.loads(canonical_bytes(request))
        or job["status"] not in ("current", "historical")
        or job["status"] != completion.classification
    ):
        raise ValueError("Result differs from its immutable terminal request")
    payload = json.loads(canonical_bytes(result))
    params = (
        claim.fence,
        scope.run_id,
        scope.asset_id,
        scope.source_id,
        fingerprint(assignment),
        fingerprint(request),
        fingerprint(result),
        completion.classification,
        Jsonb(payload),
    )
    conn.execute(
        """INSERT INTO m_route_energy_results
        (fence,run_id,asset_id,source_id,assignment_sha256,input_sha256,result_sha256,classification,payload)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        params,
    )
    saved = conn.execute(
        "SELECT payload,assignment_sha256,classification FROM m_route_energy_results WHERE fence=%s",
        (claim.fence,),
    ).fetchone()
    if (
        not saved
        or saved["payload"] != payload
        or saved["assignment_sha256"] != fingerprint(assignment)
        or saved["classification"] != completion.classification
    ):
        raise ValueError("Conflicting immutable route forecast")


def save_residual(conn, fence, residual):
    if not isinstance(residual, Residual):
        raise ValueError("Validated residual required")
    payload = json.loads(canonical_bytes(residual))
    conn.execute(
        "INSERT INTO m_route_energy_residuals(fence,observation_sha256,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
        (fence, residual.observation_sha256, Jsonb(payload)),
    )
    saved = conn.execute(
        "SELECT payload FROM m_route_energy_residuals WHERE fence=%s AND observation_sha256=%s",
        (fence, residual.observation_sha256),
    ).fetchone()
    if not saved or saved["payload"] != payload:
        raise ValueError("Conflicting immutable comparison")


def mismatch_summary(records, current):
    """Summarize three nonoverlapping intervals for the current input assignment."""
    result = {"status": "insufficient_data", "intervals": 0, "threshold_j": 900_000.0}
    if not current.get("assignment") or not current.get("clock") or not current.get("context"):
        return result
    assignment = decode_record(Assignment, current["assignment"])
    threshold = assignment.policy.condition_energy_kwh * 3_600_000
    result["threshold_j"] = threshold
    revision = decode_record(SourcePlan, current["context"]["plan"]).revision
    selected = []
    for row in sorted(records, key=lambda row: row["payload"]["until_working_s"], reverse=True):
        if row["assignment_sha256"] != current["assignment_sha256"]:
            continue
        item = decode_record(Residual, row["payload"])
        if (
            json.loads(canonical_bytes(item.scope)) != current["clock"]["scope"]
            or item.route_revision != revision
        ):
            continue
        if selected and item.until_working_s > selected[-1].from_working_s:
            continue
        selected.append(item)
        if len(selected) == 3:
            break
    result["intervals"] = len(selected)
    if len(selected) == 3:
        result["status"] = (
            "persistent_mismatch"
            if persistent_mismatch(tuple(reversed(selected)), threshold_j=threshold)
            else "no_persistent_mismatch"
        )
    return result


def read(asset, before=None, limit=20, fence=None):
    run = os.environ["EVDT_RUN_ID"]
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        current = collect(conn, run, asset)
        lifecycle = conn.execute(
            "SELECT state,updated_at FROM m_route_forecast_states WHERE run_id=%s AND asset_id=%s",
            (run, asset),
        ).fetchone()
        residuals = conn.execute(
            """SELECT c.fence,c.created_at,c.payload,r.assignment_sha256 FROM m_route_energy_residuals c
            JOIN m_route_energy_results r USING(fence)
            WHERE r.run_id=%s AND r.asset_id=%s ORDER BY c.created_at DESC,c.fence DESC LIMIT 20""",
            (run, asset),
        ).fetchall()
        if fence is not None:
            row = conn.execute(
                "SELECT fence,created_at,payload,classification,result_sha256 FROM m_route_energy_results WHERE run_id=%s AND asset_id=%s AND fence=%s",
                (run, asset, fence),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Forecast not found in this asset")
            return {
                "version": "route-energy-detail/2.0.0",
                "run_id": run,
                "asset_id": asset,
                "historical": True,
                "item": row,
            }
        rows = conn.execute(
            """SELECT r.fence,r.created_at,r.payload,r.classification,r.result_sha256,
            r.assignment_sha256,j.request->'state'->>'route_revision' AS route_revision,
            j.request->>'trigger' AS trigger,
            j.request->'vehicle'->'reserve_soc' AS reserve_soc FROM m_route_energy_results r
            JOIN m_route_forecast_jobs j USING(fence,run_id,asset_id,input_sha256)
            WHERE r.run_id=%s AND r.asset_id=%s AND r.fence<%s ORDER BY r.fence DESC LIMIT %s""",
            (run, asset, before or 9007199254740991, limit + 1),
        ).fetchall()
    reason = current["reason"]
    latest = rows[0] if rows else None
    working_age = real_age = None
    diagnostic = None
    if before is not None:
        reason = "historical_page"
    elif reason is None and latest:
        result = latest["payload"]
        assignment = decode_record(Assignment, current["assignment"])
        working_age = current["clock"]["working_s"] - result["cutoff_working_s"]
        real_age = current["as_of_real_life_s"] - result["requested_real_life_s"]
        if (
            latest["assignment_sha256"] != current["assignment_sha256"]
            or result["scope"] != current["clock"]["scope"]
            or latest["route_revision"]
            != decode_record(SourcePlan, current["context"]["plan"]).revision
        ):
            reason = "obsolete_source_route_or_assignment"
        elif latest["classification"] != "current":
            reason = "historical_completion"
        elif not 0 <= working_age <= assignment.policy.forecast_max_age_working_s:
            reason = "forecast_working_age_exceeded"
        elif not -0.05 <= real_age <= assignment.policy.forecast_max_age_real_life_s:
            reason = "forecast_real_life_age_exceeded"
        elif result["status"] != "available":
            reason = result["reason"] or result["status"]
            if result["status"] == "infeasible":
                diagnostic = {"fence": latest["fence"], "payload": result}
    elif reason is None:
        reason = "forecast_pending"
    return {
        "version": VIEW_VERSION,
        "run_id": run,
        "asset_id": asset,
        "configured": current["assignment"] is not None,
        "as_of_real_life_s": current["as_of_real_life_s"],
        "status": "available" if reason is None else "unavailable",
        "reason": reason,
        "current": latest["payload"] if reason is None and latest else None,
        "current_fence": latest["fence"] if reason is None and latest else None,
        "diagnostic": diagnostic,
        "working_age_s": working_age,
        "real_life_age_s": real_age,
        "lifecycle": {
            "phase": lifecycle["state"]["phase"],
            "reason": lifecycle["state"]["reason"],
            "updated_at": lifecycle["updated_at"],
            "generation": lifecycle["state"]["generation"],
        }
        if lifecycle
        else None,
        "items": rows[:limit],
        "comparisons": residuals,
        "comparison_status": mismatch_summary(residuals, current),
        "next": rows[limit - 1]["fence"] if len(rows) > limit else None,
        "uncertainty_method": "not_established",
        "automatic_dispatch_approved": False,
        **(
            {"charging_advisor_configured": True}
            if "EVDT_MOVING_CHARGE_MARGIN_SOC" in os.environ
            else {}
        ),
        **(
            {"charging_startup_waiting": True}
            if startup_waiting(configured_delay(), (current.get("clock") or {}).get("working_s"))
            else {}
        ),
    }


def map_view(asset, fence):
    from evdt.monitoring.forecast_map import project

    run = os.environ["EVDT_RUN_ID"]
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        row = conn.execute(
            """SELECT j.request,r.payload FROM m_route_energy_results r
            JOIN m_route_forecast_jobs j USING(fence,run_id,asset_id,input_sha256)
            WHERE r.run_id=%s AND r.asset_id=%s AND r.fence=%s""",
            (run, asset, fence),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Forecast not found in this asset")
    try:
        return project(row["request"], row["payload"], fence, run, asset)
    except ValueError as exc:
        raise HTTPException(409, "Saved forecast geography is unavailable") from exc


def main():
    import argparse
    import sys

    from psycopg import sql

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["provision", "enable-writer"])
    args = parser.parse_args()
    with connection() as conn:
        if args.action == "provision":
            raw = sys.stdin.buffer.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("Assignment input byte budget exceeded")
            assignment = decode_record(Assignment, json.loads(raw))
            inserted = provision(conn, assignment)
            print(
                json.dumps(
                    {
                        "provisioned": True,
                        "inserted": inserted,
                        "assignment_sha256": fingerprint(assignment),
                    }
                )
            )
        else:
            password = Path(os.environ["FORECAST_DB_PASSWORD_FILE"]).read_text().strip()
            if len(password) < 32:
                raise ValueError("Generated forecast credential required")
            conn.execute(
                sql.SQL("ALTER ROLE evdt_forecast LOGIN PASSWORD {}").format(sql.Literal(password))
            )
            print(json.dumps({"writer_enabled": True}))


if __name__ == "__main__":
    main()
