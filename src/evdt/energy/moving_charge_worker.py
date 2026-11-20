"""Versioned numerical-worker boundary for provisional one-stop charging plans."""

import json
import sys
from dataclasses import dataclass, field, replace

from .moving_charge_candidate import (
    OnRouteStop,
    VisitPolicy,
    approach_request,
    candidate_digest,
    departure_request,
)
from .moving_charge_search import Decision, plan_charge
from .moving_charge_station import StationStatus
from .moving_charge_transfer import charge_to
from .route_aware.contracts import RouteContext
from .route_aware.forecast import validate_result
from .route_aware.geometry import remaining_route
from .route_aware.validation import choice, digest, integer, number, sequence, text
from .route_aware.wire import (
    MAX_BYTES,
    ForecastRequest,
    _bad_constant,
    _pairs,
    canonical_bytes,
    decode_record,
    fingerprint,
)

MAX_RESULT_BYTES = 262_144
JOB_VERSION = "moving-charge-worker-request/1.0.0"
RESULT_VERSION = "moving-charge-worker-result/1.0.0"


@dataclass(frozen=True)
class StationOffer:
    stop: OnRouteStop
    status: StationStatus
    alternate: RouteContext | None = field(default=None, metadata={"omit_none_on_wire": True})

    def __post_init__(self) -> None:
        if not isinstance(self.stop, OnRouteStop) or not isinstance(self.status, StationStatus):
            raise ValueError("Charging offer needs a bay position and station status")
        if self.stop.bay.bay_id != self.status.bay_id:
            raise ValueError("Charging offer refers to a different bay")
        if self.alternate is not None and not isinstance(self.alternate, RouteContext):
            raise ValueError("Charging detour requires prepared road geometry")

    def driving_request(self, baseline: ForecastRequest) -> ForecastRequest:
        route = self.alternate
        if route is None:
            return baseline
        if (
            route.network_sha256 != baseline.route.network_sha256
            or route.elevation_origin != baseline.route.elevation_origin
            or route.vertical_datum != baseline.route.vertical_datum
            or route.horizontal_frame != baseline.route.horizontal_frame
            or baseline.scenario is not None
            or baseline.state.occurrence_index != 0
            or baseline.state.completed_stops
            or baseline.state.current_stop is not None
            or route.occurrences[0].edge_id != baseline.route.occurrences[0].edge_id
            or route.occurrences[0].lane_id != baseline.route.occurrences[0].lane_id
            or route.occurrences[-1].edge_id != baseline.route.occurrences[-1].edge_id
            or _stops(route) != _stops(baseline.route)
        ):
            raise ValueError("Charging detour changed the observed progress or required stops")
        state = replace(baseline.state, route_revision=route.revision)
        remaining_route(route, state)
        return replace(baseline, route=route, state=state)


def _stops(route: RouteContext):
    return tuple(stop for road in route.occurrences for stop in road.stops)


@dataclass(frozen=True)
class PlanningRequest:
    version: str
    baseline: ForecastRequest
    station_scenario_id: str
    offers: tuple[StationOffer, ...]
    policy: VisitPolicy
    target_soc_levels: tuple[float, ...]
    evaluated_real_life_s: float
    max_candidate_jobs: int
    budget_real_life_s: float

    def __post_init__(self) -> None:
        choice(self.version, "planning request version", (JOB_VERSION,))
        if not isinstance(self.baseline, ForecastRequest) or not isinstance(
            self.policy, VisitPolicy
        ):
            raise ValueError("Charging request requires a forecast input and visit policy")
        text(self.station_scenario_id, "station scenario")
        sequence(self.offers, "station offers", StationOffer, 0, 32)
        for offer in self.offers:
            if offer.status.scenario_id != self.station_scenario_id:
                raise ValueError("Charging offer belongs to another station scenario")
            if offer.alternate is not None:
                driving_request = offer.driving_request(self.baseline)
                if len(canonical_bytes(driving_request)) > self.baseline.policy.max_request_bytes:
                    raise ValueError("Prepared detour exceeds the forecast input budget")
                approach_request(driving_request, offer.stop)
        if len({offer.stop.bay.bay_id for offer in self.offers}) != len(self.offers):
            raise ValueError("Each charging bay may appear once in a request")
        if (
            not isinstance(self.target_soc_levels, tuple)
            or not 1 <= len(self.target_soc_levels) <= 16
        ):
            raise ValueError("Charging request requires bounded target levels")
        for target in self.target_soc_levels:
            number(
                target,
                "charge target",
                self.baseline.vehicle.reserve_soc + self.policy.margin_soc,
                1,
            )
        if tuple(sorted(set(self.target_soc_levels))) != self.target_soc_levels:
            raise ValueError("Charging targets must be unique and ordered")
        number(
            self.evaluated_real_life_s, "planning cutoff", self.baseline.requested_real_life_s, 1e12
        )
        integer(self.max_candidate_jobs, "planning job budget", 1, 256)
        number(self.budget_real_life_s, "planning deadline", 0.01, 120)


@dataclass(frozen=True)
class PlanningResult:
    version: str
    input_sha256: str
    decision: Decision

    def __post_init__(self) -> None:
        choice(self.version, "planning result version", (RESULT_VERSION,))
        digest(self.input_sha256, "planning request digest")
        if not isinstance(self.decision, Decision):
            raise ValueError("Planning result requires a decision")


def parse_request(raw: bytes) -> PlanningRequest:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError("Charging request exceeds its byte limit")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant
        )
        request = decode_record(PlanningRequest, value)
    except (RecursionError, OverflowError, TypeError, UnicodeError) as exc:
        raise ValueError("Malformed charging request") from exc
    if len(canonical_bytes(request.baseline)) > request.baseline.policy.max_request_bytes:
        raise ValueError("Embedded forecast exceeds its byte limit")
    return request


def parse_result(raw: bytes) -> PlanningResult:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_RESULT_BYTES:
        raise ValueError("Charging result exceeds its byte limit")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant
        )
        return decode_record(PlanningResult, value)
    except (RecursionError, OverflowError, TypeError, UnicodeError) as exc:
        raise ValueError("Malformed charging result") from exc


def calculate(request: PlanningRequest) -> PlanningResult:
    decision = plan_charge(
        request.baseline,
        tuple((offer.stop, offer.status) for offer in request.offers),
        policy=request.policy,
        target_soc_levels=request.target_soc_levels,
        station_scenario_id=request.station_scenario_id,
        now_real_life_s=request.evaluated_real_life_s,
        max_candidate_jobs=request.max_candidate_jobs,
        budget_real_life_s=request.budget_real_life_s,
        alternate_inputs={
            offer.stop.bay.bay_id: offer.driving_request(request.baseline)
            for offer in request.offers
            if offer.alternate is not None
        },
    )
    return PlanningResult(RESULT_VERSION, fingerprint(request), decision)


def validate_planning_result(request: PlanningRequest, result: PlanningResult) -> None:
    if result.input_sha256 != fingerprint(request):
        raise ValueError("Charging response belongs to a different request")
    decision = result.decision
    validate_result(request.baseline, decision.baseline.forecast)
    integer(decision.candidate_jobs, "recorded charging jobs", 0, request.max_candidate_jobs)
    choice(
        decision.status,
        "charging decision status",
        (
            "continue",
            "provisional_plan",
            "source_unavailable",
            "already_below_reserve",
            "baseline_unavailable",
            "search_budget_exhausted",
            "no_plan_in_search_domain",
        ),
    )
    if decision.status == "provisional_plan":
        if decision.preferred is None:
            raise ValueError("Provisional plan is missing its preferred visit")
    elif decision.preferred is not None or decision.fallback is not None:
        raise ValueError("Unavailable result contains an actionable candidate")
    offers = {offer.stop.bay.bay_id: offer for offer in request.offers}
    if any(row.bay_id not in offers for row in decision.exclusions):
        raise ValueError("Charging response names an unoffered bay")
    if decision.preferred is not None and decision.fallback is not None:
        if decision.preferred.bay_id == decision.fallback.bay_id:
            raise ValueError("Charging fallback must be a separate bay")
    for row in (decision.preferred, decision.fallback):
        if row is None:
            continue
        if (
            row.bay_id not in offers
            or row.transfer is None
            or row.arrival_working_s is None
            or row.onward is None
            or row.transfer.departure_soc not in request.target_soc_levels
        ):
            raise ValueError("Preferred or fallback visit differs from the offered domain")
        offer = offers[row.bay_id]
        stop = offer.stop
        driving = offer.driving_request(request.baseline)
        if (
            stop.bay.connector != request.policy.connector
            or stop.bay.platform_length_m < request.policy.vehicle_length_m
        ):
            raise ValueError("Candidate selected an incompatible bay")
        validate_result(approach_request(driving, stop), row.approach.forecast)
        validate_result(departure_request(driving, stop, row.transfer), row.onward.forecast)
        if row.candidate_sha256 != candidate_digest(
            driving,
            stop,
            offer.status,
            request.station_scenario_id,
            request.evaluated_real_life_s,
            row.transfer.departure_soc,
            request.policy,
            None,
        ):
            raise ValueError("Candidate identity does not match its reviewed offer")
        approach, onward = row.approach.forecast.summary, row.onward.forecast.summary
        floor = request.baseline.vehicle.reserve_soc + request.policy.margin_soc
        if (
            approach is None
            or approach.arrival_soc is None
            or onward is None
            or onward.arrival_soc is None
            or min(approach.minimum_soc, onward.minimum_soc) < floor
        ):
            raise ValueError("Candidate driving legs must both clear the planning floor")
        window = offer.status.window(
            stop.bay,
            scenario_id=request.station_scenario_id,
            now_real_life_s=request.evaluated_real_life_s,
            current_working_s=request.baseline.state.working_s,
            arrival_working_s=approach.arrival_working_s,
            status_max_age_real_life_s=request.policy.max_status_age_real_life_s,
        )
        if row.transfer != charge_to(
            window,
            arrival_working_s=approach.arrival_working_s,
            arrival_soc=approach.arrival_soc,
            reserve_soc=floor,
            target_soc=row.transfer.departure_soc,
            capacity_kwh=request.baseline.vehicle.capacity_kwh,
            equipment_w=request.policy.equipment_w,
            acceptance_by_soc=request.policy.acceptance_by_soc,
        ):
            raise ValueError("Candidate transfer differs from its station offer and policy")
    if (
        decision.preferred is not None
        and decision.fallback is not None
        and decision.fallback.arrival_working_s < decision.preferred.arrival_working_s
    ):
        raise ValueError("Preferred visit must precede the fallback by arrival time")


def main() -> int:
    try:
        request = parse_request(sys.stdin.buffer.read(MAX_BYTES + 1))
    except ValueError:
        print("invalid_charging_request", file=sys.stderr)
        return 2
    try:
        output = canonical_bytes(calculate(request))
        validate_planning_result(request, parse_result(output))
    except (ValueError, ArithmeticError):
        print("charging_calculation_integrity_failure", file=sys.stderr)
        return 3
    sys.stdout.buffer.write(output + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
