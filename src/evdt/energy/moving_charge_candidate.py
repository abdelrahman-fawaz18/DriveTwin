"""Evaluate on-route charging candidates with the route-aware energy model."""

from dataclasses import dataclass, replace
from math import hypot

from .moving_charge_leg import DrivingLeg, calculate_leg
from .moving_charge_station import StationBay, StationStatus
from .moving_charge_transfer import ChargeTransfer, charge_to
from .route_aware.contracts import Curve, RouteOccurrence, StopRef
from .route_aware.geometry import horizontal_length, interpolate, reference_length
from .route_aware.validation import integer, number, text
from .route_aware.wire import ForecastRequest, fingerprint


@dataclass(frozen=True)
class OnRouteStop:
    bay: StationBay
    occurrence_index: int
    offset_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.bay, StationBay):
            raise ValueError("Reviewed station bay required")
        integer(self.occurrence_index, "charge stop occurrence", 0, 9999)
        number(
            self.offset_m, "charge stop position", self.bay.start_offset_m, self.bay.end_offset_m
        )


@dataclass(frozen=True)
class CandidateResult:
    version: str
    candidate_sha256: str
    bay_id: str
    approach: DrivingLeg
    transfer: ChargeTransfer | None
    onward: DrivingLeg | None
    reason: str | None
    next_bay_id: str | None = None

    @property
    def arrival_working_s(self) -> float | None:
        if (
            self.reason
            or self.next_bay_id is not None
            or self.onward is None
            or self.onward.forecast.summary is None
        ):
            return None
        return self.onward.forecast.summary.arrival_working_s


@dataclass(frozen=True)
class VisitPolicy:
    """Declared equipment and planning limits, separate from station observations."""

    margin_soc: float
    equipment_w: float
    acceptance_by_soc: Curve
    max_status_age_real_life_s: float
    connector: str
    vehicle_length_m: float

    def __post_init__(self) -> None:
        number(self.margin_soc, "planning margin", 0, 1)
        number(self.equipment_w, "stationary equipment power", 0, 1_000_000)
        number(self.max_status_age_real_life_s, "maximum bay status age", 0.01, 600)
        text(self.connector, "vehicle charging connector")
        number(self.vehicle_length_m, "vehicle length", 1, 100)
        if not isinstance(self.acceptance_by_soc, Curve) or (
            self.acceptance_by_soc.x_unit,
            self.acceptance_by_soc.y_unit,
        ) != ("1", "W"):
            raise ValueError("Battery acceptance curve must map charge fraction to watts")


def departure_request(
    request: ForecastRequest, stop: OnRouteStop, transfer: ChargeTransfer
) -> ForecastRequest:
    """Build a forecast from a hypothetical stationary departure."""
    completed = tuple(
        StopRef(road.index, passenger.stop_id)
        for road in request.route.occurrences[: stop.occurrence_index + 1]
        for passenger in road.stops
        if road.index < stop.occurrence_index or passenger.offset_m < stop.offset_m
    )
    state = replace(
        request.state,
        working_s=transfer.end_working_s,
        soc=transfer.departure_soc,
        speed_m_s=0,
        occurrence_index=stop.occurrence_index,
        lane_offset_m=stop.offset_m,
        completed_stops=completed,
        current_stop=None,
        remaining_dwell_working_s=None,
    )
    return replace(request, state=state)


def _clip_road(road: RouteOccurrence, offset_m: float) -> RouteOccurrence:
    length = reference_length(road)
    left = offset_m * horizontal_length(road.shape) / length
    points = [road.shape[0]]
    for a, b in zip(road.shape, road.shape[1:]):
        distance = hypot(b.x_m - a.x_m, b.y_m - a.y_m)
        if left <= distance:
            if left > 1e-8:
                points.append(interpolate(a, b, left / distance))
            break
        points.append(b)
        left -= distance
    return replace(
        road,
        shape=tuple(points),
        reference_length_m=offset_m,
        stops=tuple(stop for stop in road.stops if stop.offset_m < offset_m),
        yield_visibility_m=None,
        fixed_signal=None,
        signal_controlled=None,
    )


def approach_request(request: ForecastRequest, stop: OnRouteStop) -> ForecastRequest:
    integer(
        stop.occurrence_index,
        "charge stop occurrence",
        request.state.occurrence_index,
        len(request.route.occurrences) - 1,
    )
    road = request.route.occurrences[stop.occurrence_index]
    if (
        stop.bay.network_sha256 != request.route.network_sha256
        or road.lane_id != stop.bay.lane_id
        or road.geometry_basis != "lane_centerline"
    ):
        raise ValueError("Bay must match a reviewed route lane and network")
    offset = number(
        stop.offset_m, "charge stop position", stop.bay.start_offset_m, stop.bay.end_offset_m
    )
    if not 0 < offset < reference_length(road) or (
        road.index == request.state.occurrence_index and offset <= request.state.lane_offset_m
    ):
        raise ValueError("Charge stop must be ahead and within the lane")
    if road.index == len(request.route.occurrences) - 1:
        raise ValueError("An on-route charge stop needs a separate destination road")
    if any(stop.bay.start_offset_m <= s.offset_m <= stop.bay.end_offset_m for s in road.stops):
        raise ValueError("Charge stop overlaps a mandatory passenger stop")
    revision = fingerprint(
        {"source_plan": request.route.revision, "charge_stop": fingerprint(stop)}
    )
    route = replace(
        request.route,
        revision=revision,
        occurrences=(*request.route.occurrences[: road.index], _clip_road(road, offset)),
    )
    scenario = request.scenario
    if scenario is not None:
        stops = {StopRef(item.index, s.stop_id) for item in route.occurrences for s in item.stops}
        scenario = replace(
            scenario,
            roads=tuple(item for item in scenario.roads if item.occurrence_index <= road.index),
            stops=tuple(item for item in scenario.stops if item.reference in stops),
            closed_occurrences=tuple(i for i in scenario.closed_occurrences if i <= road.index),
        )
    return replace(
        request,
        route=route,
        state=replace(request.state, route_revision=revision),
        scenario=scenario,
    )


CANDIDATE_VERSION = "moving-charge-on-route-candidate/1.0.0"


def candidate_digest(
    request,
    stop,
    station_status,
    station_scenario_id,
    now_real_life_s,
    target_soc,
    policy,
    next_stop,
):
    return fingerprint(
        {
            "version": CANDIDATE_VERSION,
            "request": fingerprint(request),
            "stop": fingerprint(stop),
            "station_status": fingerprint(station_status),
            "station_scenario_id": station_scenario_id,
            "now_real_life_s": now_real_life_s,
            "target_soc": target_soc,
            "policy": fingerprint(policy),
            **({"next_stop": fingerprint(next_stop)} if next_stop is not None else {}),
        }
    )


def evaluate_candidate(
    request: ForecastRequest,
    stop: OnRouteStop,
    station_status: StationStatus,
    *,
    target_soc: float,
    policy: VisitPolicy,
    station_scenario_id: str,
    now_real_life_s: float,
    approach: DrivingLeg | None = None,
    next_stop: OnRouteStop | None = None,
) -> CandidateResult:
    """Compare forecasted charge throughout a visit and the remaining driving leg."""
    floor = request.vehicle.reserve_soc + policy.margin_soc
    number(target_soc, "target charge", floor, 1)
    number(now_real_life_s, "candidate evaluation time", request.requested_real_life_s, 1e12)
    text(station_scenario_id, "authorized station scenario")
    if (
        stop.bay.connector != policy.connector
        or stop.bay.platform_length_m < policy.vehicle_length_m
    ):
        raise ValueError("Bay does not meet the vehicle's connector and length requirements")
    candidate = candidate_digest(
        request,
        stop,
        station_status,
        station_scenario_id,
        now_real_life_s,
        target_soc,
        policy,
        next_stop,
    )
    approach_input = approach_request(request, stop)
    if approach is None:
        approach = calculate_leg(approach_input)
    elif approach.forecast.input_sha256 != fingerprint(approach_input):
        raise ValueError("Cached approach differs from this station and observation")
    summary = approach.forecast.summary
    transfer = onward = None
    if summary is None or summary.arrival_soc is None:
        reason = "approach_unavailable_or_infeasible"
    elif summary.minimum_soc < floor:
        reason = "approach_below_planning_floor"
    else:
        try:
            window = station_status.window(
                stop.bay,
                scenario_id=station_scenario_id,
                now_real_life_s=now_real_life_s,
                current_working_s=request.state.working_s,
                arrival_working_s=summary.arrival_working_s,
                status_max_age_real_life_s=policy.max_status_age_real_life_s,
            )
            transfer = charge_to(
                window,
                arrival_working_s=summary.arrival_working_s,
                arrival_soc=summary.arrival_soc,
                reserve_soc=floor,
                target_soc=target_soc,
                capacity_kwh=request.vehicle.capacity_kwh,
                equipment_w=policy.equipment_w,
                acceptance_by_soc=policy.acceptance_by_soc,
            )
        except ValueError as exc:
            reason = str(exc)
        else:
            # This forecast is local to the hypothetical candidate. It must never
            # enter the database's current forecast or authenticated source path.
            departure = departure_request(request, stop, transfer)
            onward = calculate_leg(
                approach_request(departure, next_stop) if next_stop is not None else departure
            )
            result = onward.forecast.summary
            reason = (
                "departure_unavailable_or_infeasible"
                if result is None or result.arrival_soc is None
                else "departure_below_planning_floor"
                if result.minimum_soc < floor
                else None
            )
    return CandidateResult(
        CANDIDATE_VERSION,
        candidate,
        stop.bay.bay_id,
        approach,
        transfer,
        onward,
        reason,
        next_stop.bay.bay_id if next_stop is not None else None,
    )
