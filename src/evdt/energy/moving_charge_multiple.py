"""Bounded enumeration of two-stop charging plans for development qualification."""

from dataclasses import dataclass
from itertools import combinations
from time import monotonic

from .moving_charge_candidate import (
    CandidateResult,
    OnRouteStop,
    VisitPolicy,
    approach_request,
    departure_request,
    evaluate_candidate,
)
from .moving_charge_leg import calculate_leg
from .moving_charge_station import StationStatus
from .route_aware.validation import integer, number
from .route_aware.wire import ForecastRequest


@dataclass(frozen=True)
class TwoStopDecision:
    status: str
    visits: tuple[CandidateResult, ...]
    evaluated: int


def plan_two_stops(
    request: ForecastRequest,
    offers: tuple[tuple[OnRouteStop, StationStatus], ...],
    *,
    policy: VisitPolicy,
    targets: tuple[float, ...],
    station_scenario_id: str,
    now_real_life_s: float,
    max_evaluations: int,
    budget_real_life_s: float,
) -> TwoStopDecision:
    """Evaluate a small search domain with hard limits and no partial winner."""
    if not 2 <= len(offers) <= 8 or len({stop.bay.bay_id for stop, _ in offers}) != len(offers):
        raise ValueError("Two-stop search needs two to eight distinct bays")
    if not 1 <= len(targets) <= 8 or tuple(sorted(set(targets))) != targets:
        raise ValueError("Two-stop targets must be distinct and increasing")
    for target in targets:
        number(target, "two-stop charge target", request.vehicle.reserve_soc + policy.margin_soc, 1)
    integer(max_evaluations, "two-stop evaluation budget", 1, 256)
    number(budget_real_life_s, "two-stop deadline", 0.01, 120)
    if (
        request.state.source_state != "running"
        or request.state.charging_state != "not_charging"
        or request.state.soc < request.vehicle.reserve_soc
    ):
        return TwoStopDecision("source_unavailable", (), 0)
    ordered = sorted(
        offers, key=lambda pair: (pair[0].occurrence_index, pair[0].offset_m, pair[0].bay.bay_id)
    )
    start = monotonic()
    spent = 0
    best = ()
    score = None
    for (first, first_status), (second, second_status) in combinations(ordered, 2):
        if (first.occurrence_index, first.offset_m) >= (second.occurrence_index, second.offset_m):
            continue
        if first.bay.site_id == second.bay.site_id:
            continue
        if any(
            stop.bay.connector != policy.connector
            or stop.bay.platform_length_m < policy.vehicle_length_m
            for stop in (first, second)
        ):
            continue
        if spent == max_evaluations or monotonic() - start >= budget_real_life_s:
            return TwoStopDecision("search_budget_exhausted", (), spent)
        spent += 1
        try:
            approach = calculate_leg(approach_request(request, first))
        except ValueError:
            continue
        for target in targets:
            if spent == max_evaluations or monotonic() - start >= budget_real_life_s:
                return TwoStopDecision("search_budget_exhausted", (), spent)
            spent += 1
            try:
                initial = evaluate_candidate(
                    request,
                    first,
                    first_status,
                    target_soc=target,
                    policy=policy,
                    station_scenario_id=station_scenario_id,
                    now_real_life_s=now_real_life_s,
                    approach=approach,
                    next_stop=second,
                )
            except ValueError:
                continue
            if initial.reason is not None or initial.transfer is None or initial.onward is None:
                continue
            pseudo_request = departure_request(request, first, initial.transfer)
            for final_target in targets:
                if spent == max_evaluations or monotonic() - start >= budget_real_life_s:
                    return TwoStopDecision("search_budget_exhausted", (), spent)
                spent += 1
                # Reuse the first candidate's predicted inter-station leg. No later
                # reading or new station observation enters this calculation.
                terminal = evaluate_candidate(
                    pseudo_request,
                    second,
                    second_status,
                    target_soc=final_target,
                    policy=policy,
                    station_scenario_id=station_scenario_id,
                    now_real_life_s=now_real_life_s,
                    approach=initial.onward,
                )
                if terminal.arrival_working_s is None:
                    continue
                key = (
                    terminal.arrival_working_s,
                    first.bay.bay_id,
                    second.bay.bay_id,
                    target,
                    final_target,
                )
                if score is None or key < score:
                    score, best = key, (initial, terminal)
    if monotonic() - start >= budget_real_life_s:
        return TwoStopDecision("search_budget_exhausted", (), spent)
    return TwoStopDecision(
        "provisional_two_stop_plan" if best else "no_plan_in_search_domain", best, spent
    )
