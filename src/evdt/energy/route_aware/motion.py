"""Distance-closing motion plans under explicit forward movement assumptions."""

import math
from dataclasses import dataclass, replace

from .contracts import LiveState, StopRef, VehicleProfile
from .geometry import RemainingRoute, RoadPiece, RouteUnavailable
from .policy import ForecastPolicy
from .validation import number


@dataclass(frozen=True)
class MotionPhase:
    piece_index: int | None
    kind: str
    start_working_s: float
    duration_working_s: float
    distance_m: float
    start_speed_m_s: float
    end_speed_m_s: float
    acceleration_m_s2: float
    stop: StopRef | None = None
    position_rate_m_s: float | None = None


@dataclass(frozen=True)
class MotionPlan:
    phases: tuple[MotionPhase, ...]
    distance_m: float
    moving_working_s: float
    dwell_working_s: float
    duration_working_s: float
    assumptions: tuple[str, ...]


def prefix_plan(plan: MotionPlan, duration_working_s: float) -> MotionPlan:
    """Retain exact partial-phase kinematics at a completed time cutoff."""
    number(duration_working_s, "completed prefix duration", 0, plan.duration_working_s)
    phases = []
    remaining = duration_working_s
    for phase in plan.phases:
        if remaining <= 1e-9:
            break
        duration = min(remaining, phase.duration_working_s)
        distance = (
            phase.position_rate_m_s * duration
            if phase.position_rate_m_s is not None
            else phase.start_speed_m_s * duration + 0.5 * phase.acceleration_m_s2 * duration**2
        )
        phases.append(
            replace(
                phase,
                duration_working_s=duration,
                distance_m=max(0.0, distance),
                end_speed_m_s=max(0.0, phase.start_speed_m_s + phase.acceleration_m_s2 * duration),
            )
        )
        remaining -= duration
    moving = math.fsum(p.duration_working_s for p in phases if p.kind != "dwell")
    dwell = math.fsum(p.duration_working_s for p in phases if p.kind == "dwell")
    return MotionPlan(
        tuple(phases),
        math.fsum(p.distance_m for p in phases),
        moving,
        dwell,
        moving + dwell,
        plan.assumptions,
    )


def _hard_braking_caps(
    pieces: tuple[RoadPiece, ...], brakings: tuple[float, ...], arrival_speed: float
) -> list[float]:
    if not pieces:
        return [arrival_speed]
    caps = [pieces[0].speed_limit_m_s]
    for before, after in zip(pieces, pieces[1:]):
        caps.append(
            0.0 if before.stops_after else min(before.speed_limit_m_s, after.speed_limit_m_s)
        )
    caps.append(0.0 if pieces[-1].stops_after else arrival_speed)
    for index in reversed(range(len(pieces))):
        caps[index] = min(
            caps[index],
            math.sqrt(caps[index + 1] ** 2 + 2 * brakings[index] * pieces[index].distance_m),
        )
    return caps


def _plan_continuous_motion(
    route: RemainingRoute,
    state: LiveState,
    vehicle: VehicleProfile,
    policy: ForecastPolicy,
    *,
    speed_factors: tuple[float, ...] | None = None,
    arrival_speed_m_s: float = 0,
    lateral_acceleration_m_s2: float = 1.0,
    accelerations_m_s2: tuple[float, ...] | None = None,
    decelerations_m_s2: tuple[float, ...] | None = None,
) -> MotionPlan:
    """Backward braking envelope, forward acceleration envelope, then exact phases.

    Fractions describe assumed traffic/movement below road limits. The current
    observed speed is never rewritten to make an infeasible plan appear valid.
    Arrival is a planned stop unless another explicit speed is supplied.
    """
    number(arrival_speed_m_s, "arrival speed", 0, 60)
    number(lateral_acceleration_m_s2, "assumed lateral acceleration", 0.1, 3)
    pieces = route.pieces
    if len(pieces) > policy.max_segments:
        raise RouteUnavailable("motion_segment_budget_exceeded")
    factors = speed_factors if speed_factors is not None else (1.0,) * len(pieces)
    if len(factors) != len(pieces):
        raise ValueError("One movement factor is required per road piece")
    for factor in factors:
        number(factor, "movement speed factor", 0.01, 1)
    limits = [
        min(p.speed_limit_m_s * f, vehicle.max_speed_m_s or 60)
        for p, f in zip(pieces, factors, strict=True)
    ]
    if pieces and arrival_speed_m_s > limits[-1]:
        raise RouteUnavailable("arrival_speed_exceeds_limit")
    caps = [limits[0]] if pieces else [arrival_speed_m_s]
    for index in range(1, len(pieces)):
        before, after = pieces[index - 1], pieces[index]
        angle = abs((after.heading_rad - before.heading_rad + math.pi) % (2 * math.pi) - math.pi)
        cap = min(limits[index - 1], limits[index])
        if before.yield_visibility_m is not None:
            cap = min(cap, math.sqrt(2 * vehicle.max_deceleration_m_s2 * before.yield_visibility_m))
        if angle > 1e-6 and not policy.road_limits_include_turns:
            radius = (before.distance_m + after.distance_m) / (2 * angle)
            cap = min(cap, math.sqrt(lateral_acceleration_m_s2 * radius))
        caps.append(0.0 if before.stops_after else cap)
    if pieces:
        caps.append(0.0 if pieces[-1].stops_after else arrival_speed_m_s)
    braking = vehicle.max_deceleration_m_s2
    brakings = decelerations_m_s2 or (braking,) * len(pieces)
    if len(brakings) != len(pieces):
        raise ValueError("One deceleration bound is required per road piece")
    for value in brakings:
        number(value, "road deceleration limit", 0.1, 4.5)
    acceleration = vehicle.max_acceleration_m_s2
    accelerations = accelerations_m_s2 or (acceleration,) * len(pieces)
    if len(accelerations) != len(pieces):
        raise ValueError("One acceleration bound is required per road piece")
    for value in accelerations:
        number(value, "road acceleration limit", 0.001, acceleration)
    # Legal road limits, required stops, and arrival are hard constraints. Traffic
    # fractions and the polyline's comfort corner speeds are future assumptions.
    hard = _hard_braking_caps(pieces, brakings, arrival_speed_m_s)
    for index in reversed(range(len(pieces))):
        caps[index] = min(
            caps[index],
            math.sqrt(caps[index + 1] ** 2 + 2 * brakings[index] * pieces[index].distance_m),
        )
    if pieces and state.speed_m_s > hard[0] + 1e-8:
        raise RouteUnavailable("current_speed_cannot_meet_plan")
    recovering = bool(pieces and state.speed_m_s > caps[0] + 1e-8)
    if recovering:
        # Preserve observed kinetic energy and brake immediately toward the soft
        # plan. Do not invent an instantaneous speed drop at the forecast cutoff.
        braking_work = 0.0
        for index in range(len(caps)):
            minimum = math.sqrt(max(0.0, state.speed_m_s**2 - 2 * braking_work))
            if minimum > hard[index] + 1e-8:
                raise RouteUnavailable("current_speed_cannot_meet_plan")
            caps[index] = max(caps[index], minimum)
            if index < len(pieces):
                braking_work += brakings[index] * pieces[index].distance_m
    if route.initial_stops and state.speed_m_s > 1e-8:
        raise RouteUnavailable("initial_dwell_while_moving")
    if not pieces and state.speed_m_s > arrival_speed_m_s + 1e-8:
        raise RouteUnavailable("no_distance_for_arrival_braking")
    speeds = [state.speed_m_s]
    for index, piece in enumerate(pieces):
        acceleration = accelerations[index]
        speeds.append(
            min(caps[index + 1], math.sqrt(speeds[-1] ** 2 + 2 * acceleration * piece.distance_m))
        )
    if pieces and abs(speeds[-1] - (0 if pieces[-1].stops_after else arrival_speed_m_s)) > 1e-8:
        raise RouteUnavailable("arrival_speed_unreachable")
    phases: list[MotionPhase] = []
    working_s = state.working_s

    def add(
        index: int | None,
        kind: str,
        duration: float,
        distance: float,
        start: float,
        end: float,
        accel: float,
        stop: StopRef | None = None,
    ) -> None:
        nonlocal working_s
        if duration <= 1e-12 and stop is None:
            return
        if working_s + duration - state.working_s > policy.max_plan_working_s + 1e-8:
            raise RouteUnavailable("motion_duration_budget_exceeded")
        phases.append(
            MotionPhase(index, kind, working_s, duration, distance, start, end, accel, stop)
        )
        working_s += duration

    for stop in route.initial_stops:
        add(None, "dwell", stop.dwell_working_s, 0, 0, 0, 0, stop.reference)
    for index, piece in enumerate(pieces):
        acceleration = accelerations[index]
        start, end = speeds[index], speeds[index + 1]
        braking = brakings[index]
        if start > limits[index] + 1e-8:
            target = max(limits[index], end)
            first = max(0.0, (start**2 - target**2) / (2 * braking))
            last = max(0.0, (target**2 - end**2) / (2 * braking))
            cruise = piece.distance_m - first - last
            if target > start + 1e-8 or cruise < -1e-6:
                raise RouteUnavailable("motion_distance_does_not_close")
            add(index, "brake", (start - target) / braking, first, start, target, -braking)
            if cruise > 1e-9:
                add(index, "cruise", cruise / target, cruise, target, target, 0)
            add(index, "brake", (target - end) / braking, last, target, end, -braking)
            for stop in piece.stops_after:
                add(index, "dwell", stop.dwell_working_s, 0, 0, 0, 0, stop.reference)
            continue
        peak_squared = (
            2 * acceleration * braking * piece.distance_m
            + braking * start**2
            + acceleration * end**2
        ) / (acceleration + braking)
        peak = min(limits[index], math.sqrt(max(0, peak_squared)))
        if peak + 1e-8 < max(start, end):
            raise RouteUnavailable("motion_peak_below_boundary_speed")
        peak = max(peak, start, end)
        up = max(0.0, (peak**2 - start**2) / (2 * acceleration))
        down = max(0.0, (peak**2 - end**2) / (2 * braking))
        cruise = piece.distance_m - up - down
        if cruise < -1e-6:
            raise RouteUnavailable("motion_distance_does_not_close")
        cruise = max(0.0, cruise)
        add(index, "accelerate", (peak - start) / acceleration, up, start, peak, acceleration)
        if cruise:
            if peak <= 0:
                raise RouteUnavailable("positive_distance_without_motion")
            add(index, "cruise", cruise / peak, cruise, peak, peak, 0)
        add(index, "brake", (peak - end) / braking, down, peak, end, -braking)
        for stop in piece.stops_after:
            add(index, "dwell", stop.dwell_working_s, 0, 0, 0, 0, stop.reference)
    distance = math.fsum(p.distance_m for p in phases)
    if abs(distance - route.distance_m) > max(0.1, 0.0001 * route.distance_m):
        raise RouteUnavailable("motion_distance_does_not_close")
    moving = math.fsum(p.duration_working_s for p in phases if p.kind != "dwell")
    dwell = math.fsum(p.duration_working_s for p in phases if p.kind == "dwell")
    assumptions = (
        *route.assumptions,
        "forward_speed_caps_are_assumptions",
        "unprovided_signal_delays_excluded",
        "turn_speed_caps_from_road_provider"
        if policy.road_limits_include_turns
        else "curvature_speed_cap_from_polyline",
        "arrival_speed_declared",
        *(("observed_speed_brakes_toward_assumed_motion",) if recovering else ()),
    )
    return MotionPlan(tuple(phases), distance, moving, dwell, moving + dwell, assumptions)


def _sumo_braking_prefix(
    pieces: tuple[RoadPiece, ...],
    state: LiveState,
    brakings: tuple[float, ...],
    step: float,
    arrival_speed: float,
    *,
    source_distance: bool = False,
) -> tuple[list[MotionPhase], int, float, float, float]:
    """Brake from the measured speed using SUMO's end-speed position update.

    Each step advances at its end speed. A step may end at a lower-limit
    boundary at a higher speed, provided its next step obeys that limit before
    moving on the next piece. Stops may never be crossed by the prefix.
    """
    # TraCI reports lane-relative positions. The simulator advances in that
    # coordinate, while grade and road work use the prepared physical shape.
    lane_pieces = (
        tuple(replace(p, distance_m=p.source_distance_m or p.distance_m) for p in pieces)
        if source_distance
        else pieces
    )
    hard = _hard_braking_caps(lane_pieces, brakings, arrival_speed)
    physical_hard = _hard_braking_caps(pieces, brakings, arrival_speed) if source_distance else hard
    phases: list[MotionPhase] = []
    index, offset, speed = 0, 0.0, state.speed_m_s
    max_steps = math.floor(10 / step)
    for tick in range(max_steps + 1):
        working = state.working_s + tick * step
        piece = lane_pieces[index]
        braking = brakings[index]
        left = piece.distance_m - offset
        cap = min(piece.speed_limit_m_s, math.sqrt(hard[index + 1] ** 2 + 2 * braking * left))
        if speed <= cap + 1e-8:
            if not source_distance:
                return phases, index, offset, speed, working
            ratio = pieces[index].distance_m / piece.distance_m
            physical_offset = min(pieces[index].distance_m, offset * ratio)
            # The suffix uses a continuous physical-distance envelope. Keep
            # stepping if the two coordinate systems give different answers.
            physical_cap = math.sqrt(
                physical_hard[index + 1] ** 2
                + 2 * braking * (pieces[index].distance_m - physical_offset)
            )
            if speed <= physical_cap + 1e-8:
                return phases, index, physical_offset, speed, working
        if tick == max_steps:
            break
        end_speed = speed - braking * step
        if end_speed <= 1e-12:
            end_speed = 0.0
        accel = (end_speed - speed) / step
        if end_speed == 0:
            phases.append(
                MotionPhase(index, "brake", working, step, 0, speed, 0, accel, position_rate_m_s=0)
            )
        else:
            remaining = end_speed * step
            elapsed = 0.0
            while remaining > 0:
                piece = lane_pieces[index]
                if end_speed > piece.speed_limit_m_s + 1e-8:
                    raise RouteUnavailable("current_speed_cannot_meet_plan")
                left = piece.distance_m - offset
                distance = min(remaining, left)
                duration = distance / end_speed
                ratio = pieces[index].distance_m / piece.distance_m
                phases.append(
                    MotionPhase(
                        index,
                        "brake",
                        working + elapsed,
                        duration,
                        distance * ratio,
                        speed + accel * elapsed,
                        speed + accel * (elapsed + duration),
                        accel,
                        position_rate_m_s=end_speed * ratio,
                    )
                )
                remaining -= distance
                if remaining <= 1e-9:
                    remaining = 0.0
                elapsed += duration
                offset += distance
                if left - distance <= 1e-9:
                    if piece.stops_after or index + 1 == len(pieces):
                        if (
                            source_distance
                            and remaining == 0
                            and end_speed <= braking * step
                            and (piece.stops_after or arrival_speed == 0)
                        ):
                            offset = piece.distance_m
                            break
                        raise RouteUnavailable("current_speed_cannot_meet_plan")
                    index, offset = index + 1, 0.0
        speed = end_speed
    raise RouteUnavailable("current_speed_cannot_meet_plan")


def plan_motion(
    route: RemainingRoute,
    state: LiveState,
    vehicle: VehicleProfile,
    policy: ForecastPolicy,
    *,
    speed_factors: tuple[float, ...] | None = None,
    arrival_speed_m_s: float = 0,
    lateral_acceleration_m_s2: float = 1.0,
    accelerations_m_s2: tuple[float, ...] | None = None,
    decelerations_m_s2: tuple[float, ...] | None = None,
) -> MotionPlan:
    try:
        return _plan_continuous_motion(
            route,
            state,
            vehicle,
            policy,
            speed_factors=speed_factors,
            arrival_speed_m_s=arrival_speed_m_s,
            lateral_acceleration_m_s2=lateral_acceleration_m_s2,
            accelerations_m_s2=accelerations_m_s2,
            decelerations_m_s2=decelerations_m_s2,
        )
    except RouteUnavailable as exc:
        step = policy.sumo_euler_step_working_s
        if (
            exc.reason != "current_speed_cannot_meet_plan"
            or step is None
            or not route.pieces
            or route.initial_stops
            or state.speed_m_s > route.pieces[0].speed_limit_m_s + 1e-8
        ):
            raise
    prefix, index, offset, speed, working = _sumo_braking_prefix(
        route.pieces,
        state,
        decelerations_m_s2 or (vehicle.max_deceleration_m_s2,) * len(route.pieces),
        step,
        arrival_speed_m_s,
        source_distance=bool(policy.sumo_braking_source_distance),
    )
    first = route.pieces[index]
    pieces = (replace(first, distance_m=first.distance_m - offset), *route.pieces[index + 1 :])
    suffix = _plan_continuous_motion(
        replace(route, pieces=pieces, distance_m=math.fsum(p.distance_m for p in pieces)),
        replace(state, working_s=working, speed_m_s=speed),
        vehicle,
        policy,
        speed_factors=None if speed_factors is None else speed_factors[index:],
        arrival_speed_m_s=arrival_speed_m_s,
        lateral_acceleration_m_s2=lateral_acceleration_m_s2,
        accelerations_m_s2=None if accelerations_m_s2 is None else accelerations_m_s2[index:],
        decelerations_m_s2=None if decelerations_m_s2 is None else decelerations_m_s2[index:],
    )
    phases = (
        *prefix,
        *(
            replace(phase, piece_index=index + phase.piece_index)
            if phase.piece_index is not None
            else phase
            for phase in suffix.phases
        ),
    )
    distance = math.fsum(p.distance_m for p in phases)
    duration = working - state.working_s
    if abs(distance - route.distance_m) > max(0.1, 0.0001 * route.distance_m):
        raise RouteUnavailable("motion_distance_does_not_close")
    if suffix.duration_working_s + duration > policy.max_plan_working_s + 1e-8:
        raise RouteUnavailable("motion_duration_budget_exceeded")
    return MotionPlan(
        phases,
        distance,
        suffix.moving_working_s + duration,
        suffix.dwell_working_s,
        suffix.duration_working_s + duration,
        (*suffix.assumptions, "sumo_euler_initial_braking"),
    )
