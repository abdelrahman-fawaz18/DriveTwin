"""Offline eligibility, ranking, and recommendation changes for one simulated run."""

from dataclasses import dataclass, replace

from evdt.energy.station_arrival import CAPACITY_KWH, JOULES_PER_KWH, RESERVE_KWH, forecast

POLICY_VERSION = "offline-charging-advisor/1.0.0"
TARGET_KWH = 0.25 * CAPACITY_KWH
IMPROVEMENT_S = 30
PERSISTENCE_S = 20
COOLDOWN_S = 60
MAX_SAMPLE_GAP_S = 10


def eligibility(station, status, decision, route, coefficients, horizon_s):
    """Return the evidence and reason codes used for one station decision."""
    row = {"station_id": station["station_id"], "status": status, "reasons": []}
    reasons = row["reasons"]
    wait_s = None
    if decision["connector_id"] not in station["connector_ids"]:
        reasons.append("incompatible_connector")
    if status is None:
        reasons.append("status_missing")
    else:
        age = decision["sim_s"] - status["observed_sim_s"]
        row["status_age_s"] = age
        if age >= station["status_max_age_sim_s"]:
            reasons.append("status_stale")
        if status["state"] != "open":
            reasons.append("station_" + status["state"])
        occupied = status["occupied_bays"]
        queued = status["queued_vehicles"]
        if occupied > station["bay_count"] or queued > station["queue_capacity"]:
            reasons.append("invalid_occupancy")
        elif occupied == station["bay_count"] or queued:
            if queued == station["queue_capacity"]:
                reasons.append("queue_full")
            elif status["expected_wait_s"] is None or status["expected_wait_s"] <= 0:
                reasons.append("wait_unknown")
            elif status["expected_wait_s"] > 45:
                reasons.append("wait_outside_domain")
            else:
                wait_s = status["expected_wait_s"]
        else:
            wait_s = 0.0

    if isinstance(route, str):
        reasons.append(route)
    else:
        approach, route_record = route
        row["route"] = route_record
        if wait_s is not None:
            if approach.drive_time_s(route_aware=True) + wait_s > horizon_s:
                reasons.append("approach_outside_forecast_domain")
            else:
                energy = forecast(
                    replace(approach, expected_wait_s=wait_s), route_aware=True, **coefficients
                )
                row["energy"] = energy
                if not energy["reserve_feasible"]:
                    reasons.append("reserve_shortfall")

    net_w = (
        min(station["power_per_bay_w"], decision["bus_acceptance_w"]) * station["charge_efficiency"]
        - decision["auxiliary_power_w"]
    )
    if net_w <= 0:
        reasons.append("nonpositive_charge_power")
    if decision["bus_charge_limit_kwh"] < TARGET_KWH:
        reasons.append("target_above_bus_limit")
    budget = decision["post_charge_draw_budget_kwh"]
    if budget is None:
        reasons.append("post_charge_budget_missing")
    elif TARGET_KWH - budget < RESERVE_KWH:
        reasons.append("target_insufficient_for_remaining_mission")
    row["eligible"] = not reasons
    if row["eligible"]:
        energy_after_wait = row["energy"]["arrival_kwh"]
        energy_after_wait -= decision["auxiliary_power_w"] * wait_s / JOULES_PER_KWH
        charge_s = max(0.0, TARGET_KWH - energy_after_wait) * JOULES_PER_KWH / net_w
        detour_s = row["route"]["detour_s"]
        row["score"] = {
            "detour_s": detour_s,
            "wait_s": wait_s,
            "charge_s": charge_s,
            "total_s": detour_s + wait_s + charge_s,
            "net_charge_power_w": net_w,
            "target_kwh": TARGET_KWH,
        }
    return row


def rank(rows):
    eligible = [row for row in rows if row["eligible"]]
    ordered = sorted(eligible, key=lambda row: (row["score"]["total_s"], row["station_id"]))
    nearest = min(
        eligible,
        key=lambda row: (row["route"]["approach_distance_m"], row["station_id"]),
        default=None,
    )
    return ordered, nearest


@dataclass
class Recommendation:
    selected: str | None = None
    selected_at: float = 0.0
    challenger: str | None = None
    challenger_since: float = 0.0
    last_decision: float | None = None

    def update(self, sim_s, ordered):
        previous = self.selected
        gap = None if self.last_decision is None else sim_s - self.last_decision
        if gap is not None and gap <= 0:
            raise ValueError("Recommendation decisions must advance simulation time")
        self.last_decision = sim_s
        eligible = {row["station_id"]: row["score"]["total_s"] for row in ordered}
        best = ordered[0]["station_id"] if ordered else None
        if best is None:
            self.selected = self.challenger = None
            reason = "no_feasible_station"
        elif previous not in eligible:
            self.selected = best
            self.selected_at = sim_s
            self.challenger = None
            reason = "initial_recommendation" if previous is None else "incumbent_ineligible"
        elif eligible[previous] - eligible[best] < IMPROVEMENT_S:
            self.challenger = None
            reason = "retain_incumbent"
        else:
            if self.challenger != best or gap is None or gap > MAX_SAMPLE_GAP_S:
                self.challenger = best
                self.challenger_since = sim_s
            if sim_s - self.challenger_since < PERSISTENCE_S:
                reason = "await_sustained_improvement"
            elif sim_s - self.selected_at < COOLDOWN_S:
                reason = "cooldown"
            else:
                self.selected = best
                self.selected_at = sim_s
                self.challenger = None
                reason = "sustained_improvement"
        return {
            "previous_station": previous,
            "recommended_station": self.selected,
            "reason": reason,
            "challenger": self.challenger,
            "challenger_since_sim_s": self.challenger_since if self.challenger else None,
        }
