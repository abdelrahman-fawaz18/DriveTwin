"""Read provisional charging estimates without granting command authority."""

import math
import os

from evdt.energy.route_aware.admission import InputUnavailable, _signal
from evdt.energy.route_aware.context import SourcePlan
from evdt.energy.route_aware.wire import decode_record

from .charging_startup import configured_delay, startup_waiting
from .database import connection
from .route_energy_views import collect

EXCLUSION_REASONS = {
    "incompatible_bay": "incompatible_bay",
    "Bay does not meet the vehicle's connector and length requirements": "incompatible_bay",
    "Bay must match a reviewed route lane and network": "unverified_approach",
    "Charge stop must be ahead and within the lane": "unverified_approach",
    "An on-route charge stop needs a separate destination road": "unverified_approach",
    "Charge stop overlaps a mandatory passenger stop": "passenger_stop_conflict",
    "approach_unavailable_or_below_planning_floor": "approach_unavailable_or_below_floor",
    "approach_unavailable_or_infeasible": "approach_unavailable_or_below_floor",
    "approach_below_planning_floor": "approach_unavailable_or_below_floor",
    "Station status belongs to a different bay or scenario": "station_status_unverified",
    "Station status is stale or has not yet been received": "station_status_unverified",
    "Station has no usable offer": "station_unavailable",
    "Station closes before charging can begin": "station_window_too_short",
    "access_window_ends_before_target": "station_window_too_short",
    "below_reserve_while_waiting": "below_floor_while_waiting",
    "insufficient_net_charging_power": "insufficient_power",
    "departure_unavailable_or_infeasible": "remaining_route_unavailable_or_below_floor",
    "departure_below_planning_floor": "remaining_route_unavailable_or_below_floor",
}


def excluded_bays(decision):
    """Summarize constraints found for bays outside the displayed options."""
    selected = {row["bay_id"] for row in (decision["preferred"], decision["fallback"]) if row}
    groups = {}
    for row in decision.get("exclusions", ()):
        bay = row["bay_id"]
        if bay not in selected and row["reason"] != "charging_target_must_add_energy":
            reason = EXCLUSION_REASONS.get(row["reason"], "qualification_input_unavailable")
            groups.setdefault(bay, {})[reason] = None
    return [{"bay_id": bay, "reasons": list(reasons)} for bay, reasons in groups.items()]


def view(asset):
    margin = os.environ.get("EVDT_MOVING_CHARGE_MARGIN_SOC")
    if margin is None:
        return {"configured": False}
    run = os.environ["EVDT_RUN_ID"]
    operator_control = asset == os.environ.get("EVDT_MOVING_CHARGE_OPERATOR_ASSET")
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        current, forecast, job = read_state(conn, run, asset)
        effect = observed_effect(conn, run, asset) if operator_control else None
    result = present(job, current, forecast, float(margin), charging_delay=configured_delay())
    if operator_control:
        result["operator_control"] = True
        result["observed_effect"] = effect
        if os.environ.get("EVDT_MOVING_CHARGE_RECOVERY") == "1":
            result["operator_recovery"] = True
        if os.environ.get("EVDT_MOVING_CHARGE_AUTOMATIC") == "1":
            result["automatic_qualification"] = True
        if os.environ.get("EVDT_MOVING_CHARGE_QUEUE_TRIAL") == "1":
            result["queue_qualification"] = True
    return result


def observed_effect(conn, run, asset):
    record = conn.execute(
        """SELECT o.sim_time_s,o.payload->'annotations' AS annotations
           FROM m_source_heads h
           JOIN m_registrations r USING(run_id,asset_id,source_id,source_epoch)
           JOIN LATERAL (
            SELECT sim_time_s,session_id,payload FROM m_observations o
            WHERE (o.run_id,o.asset_id,o.source_id,o.source_epoch)=
                  (h.run_id,h.asset_id,h.source_id,h.source_epoch)
            ORDER BY o.sim_time_s DESC,o.event_id DESC LIMIT 1
           ) o ON o.session_id=r.session_id AND o.payload->>'manifest_sha256'=r.manifest_sha256
           WHERE h.run_id=%s AND h.asset_id=%s AND h.source_id='sumo-adapter'""",
        (run, asset),
    ).fetchone()
    if record is None:
        return None
    annotations = record["annotations"] or {}
    event = annotations.get("charging_effect")
    bay = annotations.get("charging_bay")
    try:
        energy = float(annotations.get("charging_delivered_j", ""))
        working_s = float(annotations.get("charging_effect_time", ""))
    except (TypeError, ValueError):
        return None
    if (
        event
        not in (
            "approaching",
            "waiting_for_charger",
            "charging",
            "target_reached",
            "motion_resumed",
        )
        or not isinstance(bay, str)
        or not bay
        or not math.isfinite(energy)
        or (energy != 0 if event in ("approaching", "waiting_for_charger") else energy <= 0)
        or not 0 <= working_s <= record["sim_time_s"] + 1e-6
    ):
        return None
    return {
        "state": event,
        "bay_id": bay,
        "delivered_j": energy,
        "observed_working_s": working_s,
    }


def read_state(conn, run, asset):
    current = collect(conn, run, asset)
    forecast = conn.execute(
        """SELECT r.fence,r.assignment_sha256,r.classification,r.payload,
        j.request->'state'->>'route_revision' AS route_revision
        FROM m_route_energy_results r
        JOIN m_route_forecast_jobs j USING(fence,run_id,asset_id,input_sha256)
        WHERE r.run_id=%s AND r.asset_id=%s ORDER BY r.fence DESC LIMIT 1""",
        (run, asset),
    ).fetchone()
    job = conn.execute(
        """SELECT c.status,c.fence,c.forecast_fence,
        CASE WHEN c.station_rechecked_until IS NOT NULL THEN
            c.validity || jsonb_build_object('real_life_expires',c.station_rechecked_until)
        ELSE c.validity END AS validity,c.payload,c.assignment_sha256
        FROM m_moving_charge_jobs c
        JOIN m_route_energy_results r ON r.fence=c.forecast_fence
        WHERE r.run_id=%s AND r.asset_id=%s
        ORDER BY c.forecast_fence DESC,c.fence DESC LIMIT 1""",
        (run, asset),
    ).fetchone()
    return current, forecast, job


def present(job, current, forecast, margin_soc, *, charging_delay=0):
    result = {
        "configured": True,
        "status": "waiting",
        "estimate": None,
        "recorded": None,
    }
    if startup_waiting(charging_delay, (current.get("clock") or {}).get("working_s")):
        return {**result, "status": "startup_delay", "startup_delay_working_s": charging_delay}
    if job and job["payload"] is not None:
        decision = job["payload"]["decision"]
        result["recorded"] = {
            "status": decision["status"],
            "cutoff_working_s": decision["baseline"]["initial"]["working_s"],
        }
    reason = current["reason"]
    latest = forecast["fence"] if forecast else None
    if reason is None and forecast and (not job or job["forecast_fence"] != latest):
        policy = current["assignment"]["policy"]
        payload = forecast["payload"]
        floor = current["assignment"]["vehicle"]["reserve_soc"] + margin_soc
        summary = payload.get("summary")
        if (
            forecast["classification"] == "current"
            and forecast["assignment_sha256"] == current["assignment_sha256"]
            and payload["scope"] == current["clock"]["scope"]
            and forecast["route_revision"]
            == decode_record(SourcePlan, current["context"]["plan"]).revision
            and 0
            <= current["clock"]["working_s"] - payload["cutoff_working_s"]
            <= policy["forecast_max_age_working_s"]
            and -0.05
            <= current["as_of_real_life_s"] - payload["requested_real_life_s"]
            <= policy["forecast_max_age_real_life_s"]
            and payload["status"] == "available"
            and summary is not None
            and summary["arrival_soc"] is not None
            and summary["minimum_soc"] >= floor
        ):
            result["status"] = "continue"
            return result
    if reason is not None:
        result["status"] = "source_unavailable"
    elif not job:
        return result
    elif job["forecast_fence"] != latest:
        result["status"] = "historical"
    elif job["status"] != "current":
        result["status"] = job["status"]
    else:
        validity = job["validity"]
        clock = current["clock"]
        scope = job["payload"]["decision"]["baseline"]["forecast"]["scope"]
        revision = decode_record(SourcePlan, current["context"]["plan"]).revision
        if (
            not validity
            or scope != clock["scope"]
            or validity["forecast_assignment_sha256"] != current["assignment_sha256"]
            or validity["route_revision"] != revision
            or not result["recorded"]["cutoff_working_s"]
            <= clock["working_s"]
            < validity["working_expires"]
            or current["as_of_real_life_s"] >= validity["real_life_expires"]
        ):
            result["status"] = "historical"
        else:
            result["status"] = decision["status"]
            expires_in = validity["real_life_expires"] - current["as_of_real_life_s"]
            if decision["status"] in ("provisional_plan", "no_plan_in_search_domain"):
                exclusions = excluded_bays(decision)
                if exclusions:
                    result["excluded_bays"] = exclusions
                    result["comparison_expires_in_real_life_s"] = expires_in
            crossing = decision["baseline"]["first_below_reserve"]
            if (
                crossing is not None
                and crossing.get("coordinates") is not None
                and crossing["working_s"] > clock["working_s"]
            ):
                result["reserve_crossing"] = {
                    "coordinates": crossing["coordinates"],
                    "working_s": crossing["working_s"],
                    "expires_in_real_life_s": expires_in,
                }
            estimate = decision["preferred"]
            if estimate is not None:
                context = current.get("current_context") or current["context"]
                fallback = decision["fallback"]
                fallback_only = not reachable(estimate, validity, context, clock["working_s"])
                if fallback_only:
                    estimate, fallback = fallback, None
                    if not reachable(estimate, validity, context, clock["working_s"]):
                        result["status"] = "historical"
                        return result
                approach = estimate["approach"]["forecast"]["summary"]
                transfer = estimate["transfer"]
                onward = estimate["onward"]["forecast"]["summary"]
                bay = validity["bay_positions"][estimate["bay_id"]]
                result["estimate"] = {
                    "job_fence": job.get("fence"),
                    "candidate_sha256": estimate.get("candidate_sha256"),
                    "bay_id": estimate["bay_id"],
                    "approval_waiting_for_road": context["lane_id"].startswith(":"),
                    "approval_waiting_for_motion": not is_moving(current.get("packet")),
                    "past_approval_point": context["external_index"] >= bay["external_index"],
                    **({"fallback_only": True} if fallback_only else {}),
                    **({"off_route": True} if bay.get("branch_only") else {}),
                    **({"detour_sha256": bay["detour_sha256"]} if bay.get("detour_sha256") else {}),
                    **({"station_state": bay["station_state"]} if "station_state" in bay else {}),
                    "target_soc": transfer["departure_soc"],
                    "minimum_soc": min(
                        approach["minimum_soc"], transfer["plug_in_soc"], onward["minimum_soc"]
                    ),
                    "charger_arrival_working_s": approach["arrival_working_s"],
                    "charger_arrival_soc": transfer["arrival_soc"],
                    "estimated_grid_energy_j": transfer["grid_energy_j"],
                    "destination_soc": onward["arrival_soc"],
                    "working_expires": min(
                        validity["working_expires"], approach["arrival_working_s"]
                    ),
                    "expires_at_real_life_s": validity["real_life_expires"],
                    "wait_working_s": transfer["wait_working_s"],
                    "charging_working_s": transfer["end_working_s"] - transfer["start_working_s"],
                    "arrival_working_s": onward["arrival_working_s"],
                    "crossing_working_s": crossing["working_s"] if crossing is not None else None,
                    "fallback_bay_id": fallback["bay_id"]
                    if reachable(fallback, validity, context, clock["working_s"])
                    else None,
                    "expires_in_real_life_s": expires_in,
                }
                if result["estimate"]["fallback_bay_id"] is not None:
                    backup_bay = validity["bay_positions"][fallback["bay_id"]]
                    if "station_state" in backup_bay:
                        result["estimate"]["fallback_station_state"] = backup_bay["station_state"]
                    if "detour_sha256" in backup_bay:
                        result["estimate"]["fallback_detour_sha256"] = backup_bay["detour_sha256"]
                    result["estimate"]["fallback_sha256"] = fallback.get("candidate_sha256")
                    result["estimate"]["fallback_past_approval_point"] = (
                        context["external_index"] >= backup_bay["external_index"]
                    )
                    backup_approach = fallback["approach"]["forecast"]["summary"]
                    backup_transfer = fallback["transfer"]
                    backup_onward = fallback["onward"]["forecast"]["summary"]
                    result["estimate"]["fallback_details"] = {
                        "target_soc": backup_transfer["departure_soc"],
                        "minimum_soc": min(
                            backup_approach["minimum_soc"],
                            backup_transfer["plug_in_soc"],
                            backup_onward["minimum_soc"],
                        ),
                        "wait_working_s": backup_transfer["wait_working_s"],
                        "charging_working_s": backup_transfer["end_working_s"]
                        - backup_transfer["start_working_s"],
                        "arrival_working_s": backup_onward["arrival_working_s"],
                        "working_expires": min(
                            validity["working_expires"], backup_approach["arrival_working_s"]
                        ),
                    }
                    if backup_bay.get("branch_only"):
                        result["estimate"]["fallback_off_route"] = True
    return result


def is_moving(packet):
    if not packet:
        return False
    try:
        return _signal(packet["payload"], "motion.speed", "m/s", 0, 100) > 0
    except InputUnavailable:
        return False


def reachable(candidate, validity, context, working_s):
    return (
        candidate is not None
        and working_s < candidate["approach"]["forecast"]["summary"]["arrival_working_s"]
        and not passed_bay(candidate, validity, context)
    )


def passed_bay(candidate, validity, context):
    bay = validity.get("bay_positions", {}).get(candidate["bay_id"])
    if bay is None:
        return True
    occurrence, assigned = context["external_index"], bay["external_index"]
    if bay.get("branch_only"):
        return occurrence >= assigned
    return occurrence > assigned or (
        occurrence == assigned
        and (
            context["lane_id"] != bay["lane_id"] or context["lane_offset_m"] >= bay["end_offset_m"]
        )
    )
