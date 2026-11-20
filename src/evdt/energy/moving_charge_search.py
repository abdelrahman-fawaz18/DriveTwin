"""Bound the work needed to choose a provisional charging plan."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic

from .moving_charge_candidate import (
    CandidateResult,
    OnRouteStop,
    VisitPolicy,
    approach_request,
    evaluate_candidate,
)
from .moving_charge_leg import DrivingLeg, calculate_leg
from .moving_charge_station import StationStatus
from .route_aware.validation import integer, number
from .route_aware.wire import ForecastRequest


@dataclass(frozen=True)
class Exclusion:
    bay_id: str
    reason: str


@dataclass(frozen=True)
class Decision:
    status: str
    baseline: DrivingLeg
    preferred: CandidateResult | None
    fallback: CandidateResult | None
    exclusions: tuple[Exclusion, ...]
    candidate_jobs: int


def plan_charge(
    request: ForecastRequest,
    offers: tuple[tuple[OnRouteStop, StationStatus], ...],
    *,
    policy: VisitPolicy,
    target_soc_levels: tuple[float, ...],
    station_scenario_id: str,
    now_real_life_s: float,
    max_candidate_jobs: int,
    budget_real_life_s: float,
    clock: Callable[[], float] = monotonic,
    alternate_inputs: Mapping[str, ForecastRequest] | None = None,
) -> Decision:
    """Compare a finite target grid without assuming feasibility is monotonic."""
    first = clock()
    integer(max_candidate_jobs, "candidate budget", 1, 256)
    number(budget_real_life_s, "candidate decision duration", 0.01, 120)
    if len(offers) > 32 or len({x.bay.bay_id for x, _ in offers}) != len(offers):
        raise ValueError("Use at most 32 distinct charging bays")
    if not 1 <= len(target_soc_levels) <= 16 or target_soc_levels != tuple(
        sorted(set(target_soc_levels))
    ):
        raise ValueError("Choose at most 16 distinct and increasing target charge levels")
    floor = request.vehicle.reserve_soc + policy.margin_soc
    for target in target_soc_levels:
        number(target, "target charge", floor, 1)
    baseline = calculate_leg(request)
    exclusions: list[Exclusion] = []
    feasible: list[CandidateResult] = []
    jobs = 0

    def decision(state: str) -> Decision:
        if state != "provisional_plan":
            return Decision(state, baseline, None, None, tuple(exclusions), jobs)
        feasible.sort(key=lambda item: (item.arrival_working_s, item.bay_id, item.candidate_sha256))
        fallback = next((row for row in feasible[1:] if row.bay_id != feasible[0].bay_id), None)
        return Decision(
            state,
            baseline,
            feasible[0] if feasible else None,
            fallback,
            tuple(exclusions),
            jobs,
        )

    if clock() - first >= budget_real_life_s:
        return decision("search_budget_exhausted")
    if request.state.source_state != "running" or request.state.charging_state != "not_charging":
        return decision("source_unavailable")
    if request.state.soc < request.vehicle.reserve_soc:
        return decision("already_below_reserve")
    summary = baseline.forecast.summary
    if summary is None:
        return decision("baseline_unavailable")
    if summary.arrival_soc is not None and summary.minimum_soc >= floor:
        return decision("continue")

    alternate_inputs = alternate_inputs or {}
    pending: list[tuple[OnRouteStop, StationStatus, DrivingLeg, ForecastRequest]] = []
    for stop, status in sorted(
        offers, key=lambda row: (row[0].occurrence_index, row[0].offset_m, row[0].bay.bay_id)
    ):
        if jobs == max_candidate_jobs or clock() - first >= budget_real_life_s:
            return decision("search_budget_exhausted")
        try:
            if (
                stop.bay.connector != policy.connector
                or stop.bay.platform_length_m < policy.vehicle_length_m
            ):
                raise ValueError("incompatible_bay")
            driving = alternate_inputs.get(stop.bay.bay_id, request)
            approach = approach_request(driving, stop)
        except ValueError as exc:
            exclusions.append(Exclusion(stop.bay.bay_id, str(exc)))
            continue
        jobs += 1
        leg = calculate_leg(approach)
        if clock() - first >= budget_real_life_s:
            return decision("search_budget_exhausted")
        leg_summary = leg.forecast.summary
        if (
            leg_summary is None
            or leg_summary.arrival_soc is None
            or leg_summary.minimum_soc < floor
        ):
            exclusions.append(
                Exclusion(stop.bay.bay_id, "approach_unavailable_or_below_planning_floor")
            )
        else:
            try:
                status.window(
                    stop.bay,
                    scenario_id=station_scenario_id,
                    now_real_life_s=now_real_life_s,
                    current_working_s=request.state.working_s,
                    arrival_working_s=leg_summary.arrival_working_s,
                    status_max_age_real_life_s=policy.max_status_age_real_life_s,
                )
            except ValueError as exc:
                exclusions.append(Exclusion(stop.bay.bay_id, str(exc)))
            else:
                pending.append((stop, status, leg, driving))

    # Give each reachable bay one target before evaluating a second target at any bay.
    for target in target_soc_levels:
        for stop, status, approach, driving in pending:
            if jobs == max_candidate_jobs or clock() - first >= budget_real_life_s:
                return decision("search_budget_exhausted")
            jobs += 1
            candidate = evaluate_candidate(
                driving,
                stop,
                status,
                target_soc=target,
                policy=policy,
                station_scenario_id=station_scenario_id,
                now_real_life_s=now_real_life_s,
                approach=approach,
            )
            if clock() - first >= budget_real_life_s:
                return decision("search_budget_exhausted")
            if candidate.arrival_working_s is not None:
                feasible.append(candidate)
            else:
                exclusions.append(Exclusion(stop.bay.bay_id, candidate.reason or "unavailable"))
    return decision("provisional_plan" if feasible else "no_plan_in_search_domain")
