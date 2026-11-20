"""Bounded movement projection for declared, unchanged fixed-cycle signals.

This reads a schedule, never a live traffic controller or future bus state.
Actuated programs, queues, priority requests, and signal optimization are absent.
"""

import math
from dataclasses import replace

from .contracts import LiveState, StopRef, VehicleProfile
from .geometry import RemainingRoute, RemainingStop, RouteUnavailable
from .motion import MotionPhase, MotionPlan, plan_motion
from .policy import ForecastPolicy


def _extend_wait(
    plan: MotionPlan, reference: StopRef, delay: float, state: LiveState, policy: ForecastPolicy
) -> MotionPlan:
    """Change an existing stationary wait without recomputing identical kinematics.

    Rebuild timestamps in their original summation order. Moving phases and
    their distances, speeds, and accelerations remain exactly unchanged.
    """
    phases = []
    working = state.working_s
    for phase in plan.phases:
        duration = phase.duration_working_s + (delay if phase.stop == reference else 0)
        if working + duration - state.working_s > policy.max_plan_working_s + 1e-8:
            raise RouteUnavailable("motion_duration_budget_exceeded")
        phases.append(
            MotionPhase(
                phase.piece_index,
                phase.kind,
                working,
                duration,
                phase.distance_m,
                phase.start_speed_m_s,
                phase.end_speed_m_s,
                phase.acceleration_m_s2,
                phase.stop,
                phase.position_rate_m_s,
            )
        )
        working += duration
    dwell = math.fsum(p.duration_working_s for p in phases if p.kind == "dwell")
    return replace(
        plan,
        phases=tuple(phases),
        dwell_working_s=dwell,
        duration_working_s=plan.moving_working_s + dwell,
    )


def plan_fixed_signals(
    route: RemainingRoute,
    state: LiveState,
    vehicle: VehicleProfile,
    policy: ForecastPolicy,
    *,
    speed_factors: tuple[float, ...],
    accelerations_m_s2: tuple[float, ...] | None,
) -> MotionPlan:
    if any(p.signal_controlled and p.fixed_signal is None for p in route.pieces):
        raise RouteUnavailable("fixed_signal_schedule_unavailable")
    signals = [(i, p.fixed_signal) for i, p in enumerate(route.pieces) if p.fixed_signal]
    if len(signals) > 128:
        raise RouteUnavailable("fixed_signal_budget_exceeded")
    adjusted = route

    def project(current: RemainingRoute) -> MotionPlan:
        # Signal braking is a declared plant parameter. Passenger stops and
        # other roads retain the vehicle's comfortable deceleration.
        decelerations = [vehicle.max_deceleration_m_s2] * len(current.pieces)
        signal_stop_refs = set(stop_ids.values())
        approach = None
        for i in reversed(range(len(current.pieces))):
            piece = current.pieces[i]
            if piece.stops_after:
                approach = (
                    piece.occurrence_index
                    if all(s.reference in signal_stop_refs for s in piece.stops_after)
                    else None
                )
            if approach == piece.occurrence_index:
                decelerations[i] = max(
                    vehicle.max_deceleration_m_s2,
                    policy.signal_deceleration_m_s2 or vehicle.max_deceleration_m_s2,
                )
        return plan_motion(
            current,
            state,
            vehicle,
            policy,
            speed_factors=speed_factors,
            accelerations_m_s2=accelerations_m_s2,
            decelerations_m_s2=tuple(decelerations),
        )

    stop_ids = {
        i: StopRef(route.pieces[i].occurrence_index, f"forecast-signal-{i}") for i, _ in signals
    }
    plan = project(adjusted)
    for _ in range(3 * len(signals) + 1):
        crossing = {}
        for phase in plan.phases:
            if phase.piece_index is not None:
                crossing[phase.piece_index] = phase.start_working_s + phase.duration_working_s
        used_yellow_clearance = False
        for index, signal in signals:
            assert signal is not None
            delay = signal.wait(crossing[index])
            if delay <= 1e-6:
                continue
            road = adjusted.pieces[index]
            reference = stop_ids[index]
            existing = next((s for s in road.stops_after if s.reference == reference), None)
            if existing is None:
                # First establish a feasible stop, then calculate its release time.
                stops = (*road.stops_after, RemainingStop(reference, 0))
            else:
                stops = tuple(
                    replace(s, dwell_working_s=s.dwell_working_s + delay)
                    if s.reference == reference
                    else s
                    for s in road.stops_after
                )
            pieces = list(adjusted.pieces)
            pieces[index] = replace(road, stops_after=stops)
            trial = replace(adjusted, pieces=tuple(pieces))
            if existing is not None and "sumo_euler_initial_braking" not in plan.assumptions:
                plan = _extend_wait(plan, reference, delay, state, policy)
            else:
                try:
                    plan = project(trial)
                except RouteUnavailable as exc:
                    if not (
                        policy.sumo_yellow_clearance
                        and not road.stops_after
                        and signal.is_yellow(crossing[index])
                        and exc.reason == "current_speed_cannot_meet_plan"
                    ):
                        raise
                    # Only the unscheduled signal stop failed. Keep the feasible
                    # moving plan on yellow and recheck it after any later plan change.
                    used_yellow_clearance = True
                    continue
            adjusted = trial
            break
        else:
            assumptions = set(plan.assumptions)
            assumptions.discard("unprovided_signal_delays_excluded")
            assumptions.add("declared_unchanged_fixed_signal_program")
            assumptions.add("signal_braking_projection_can_add_conservative_stops")
            if used_yellow_clearance:
                assumptions.add("sumo_yellow_clearance_when_stop_infeasible")
            return replace(plan, assumptions=tuple(sorted(assumptions)))
    raise RouteUnavailable("fixed_signal_projection_did_not_converge")
