"""Cheap change screening for refresh decisions; never a published forecast."""

import math
from dataclasses import replace

from .environment import equipment_demand
from .forecast import ForecastResult
from .geometry import RouteUnavailable, remaining_route
from .ledger import condition_at
from .scenarios import apply_stop_assumptions
from .scheduling import Impact, route_context_key
from .wire import ForecastRequest


def _proxy(job: ForecastRequest, current: ForecastRequest, speed_reference: float) -> float:
    route = apply_stop_assumptions(remaining_route(current.route, current.state), job.scenario)
    vehicle = job.vehicle
    local = {r.occurrence_index: r.intervals for r in job.scenario.roads} if job.scenario else {}
    effects = {e.reference: e for e in job.scenario.stops} if job.scenario else {}
    time = current.state.working_s
    total = 0.0
    mass_override = None
    for piece in route.pieces:
        conditions = condition_at(local.get(piece.occurrence_index, job.conditions), time)
        speed = max(0.1, min(piece.speed_limit_m_s * conditions.speed_factor, speed_reference))
        duration = piece.distance_m / speed
        mass = (
            mass_override
            if mass_override is not None
            else (conditions.loaded_mass_kg or vehicle.loaded_mass_kg)
        )
        east, north = math.sin(piece.heading_rad), math.cos(piece.heading_rad)
        relative_east, relative_north = (
            speed * east - conditions.wind_east_m_s,
            speed * north - conditions.wind_north_m_s,
        )
        density = (
            conditions.pressure_pa / (287.05 * conditions.ambient_temperature_k)
            if conditions.pressure_pa and conditions.ambient_temperature_k
            else vehicle.air_density_kg_m3
        )
        wheel = (
            vehicle.rolling_coefficient
            * conditions.rolling_multiplier
            * mass
            * vehicle.gravity_m_s2
            * piece.horizontal_m
            + mass * vehicle.gravity_m_s2 * piece.grade_ratio * piece.horizontal_m
            + 0.5
            * density
            * vehicle.drag_coefficient
            * vehicle.frontal_area_m2
            * math.hypot(relative_east, relative_north)
            * (relative_east * east + relative_north * north)
            * piece.distance_m
        )
        total += (
            max(0, wheel) / vehicle.propulsion_efficiency
            + min(0, wheel) * vehicle.regeneration_efficiency
        )
        future_aux = conditions.auxiliary_power_w
        if job.scenario and job.scenario.equipment:
            if conditions.ambient_temperature_k is None:
                raise RouteUnavailable("equipment_model_ambient_temperature_missing")
            future_aux = equipment_demand(
                job.scenario.equipment,
                ambient_temperature_k=conditions.ambient_temperature_k,
                occupants=job.scenario.initial_occupants,
                door_open_fraction=0,
                absorbed_solar_w=job.scenario.absorbed_solar_w,
            ).total_electrical_w
        near = max(
            0.0, min(duration, current.state.working_s + job.policy.near_horizon_working_s - time)
        )
        if duration > near and future_aux is None:
            raise RouteUnavailable("forward_auxiliary_assumption_missing")
        total += job.state.auxiliary_power_w * near + (future_aux or 0) * (duration - near)
        total += conditions.extra_auxiliary_power_w * duration
        for stop in piece.stops_after:
            total += (
                future_aux if future_aux is not None else job.state.auxiliary_power_w
            ) * stop.dwell_working_s
            total += (
                0.5
                * (mass + vehicle.rotating_mass_kg)
                * speed**2
                * (1 / vehicle.propulsion_efficiency - vehicle.regeneration_efficiency)
            )
            if (
                stop.reference in effects
                and effects[stop.reference].loaded_mass_on_departure_kg is not None
            ):
                mass_override = effects[stop.reference].loaded_mass_on_departure_kg
            time += stop.dwell_working_s
        time += duration
    return total


def estimate_impact(
    reference: ForecastRequest | None, current: ForecastRequest, accepted: ForecastResult | None
) -> Impact:
    if (
        reference is None
        or reference.state.scope != current.state.scope
        or route_context_key(reference) != route_context_key(current)
    ):
        return Impact()
    if reference.assignment_sha256 != current.assignment_sha256:
        return Impact(domain_changed=True)
    if (
        reference.assignment_sha256 is not None
        and reference.assignment_sha256 == current.assignment_sha256
    ):
        # Use today's prepared indices for the same approved road/stop effects.
        # The old observed equipment value remains the change-screening baseline.
        reference = replace(
            reference,
            route=current.route,
            scenario=current.scenario,
            state=replace(
                current.state,
                auxiliary_power_w=reference.state.auxiliary_power_w,
                soc=reference.state.soc,
            ),
        )
    reserve = current.vehicle.reserve_soc
    crossing = reference.state.soc >= reserve > current.state.soc
    if (
        reference.conditions == current.conditions
        and reference.scenario == current.scenario
        and reference.vehicle == current.vehicle
        and reference.state.auxiliary_power_w == current.state.auxiliary_power_w
    ):
        return Impact(reserve_crossed=crossing)
    capacity_j = current.vehicle.capacity_kwh * 3_600_000
    speed_reference = 10.0
    if accepted and accepted.summary and accepted.summary.duration_working_s:
        speed_reference = max(
            1, min(30, accepted.summary.remaining_distance_m / accepted.summary.duration_working_s)
        )
    try:
        delta = _proxy(current, current, speed_reference) - _proxy(
            reference, current, speed_reference
        )
    except (RouteUnavailable, ValueError):
        # Coverage/domain changes need an explicit new availability decision;
        # they are not represented as an invented energy magnitude.
        return Impact(domain_changed=True)
    if accepted and accepted.summary:
        crossing |= (
            accepted.summary.minimum_soc
            >= reserve
            > accepted.summary.minimum_soc - delta / capacity_j
        )
    return Impact(abs(delta) / 3_600_000, min(1, abs(delta) / capacity_j), crossing)
