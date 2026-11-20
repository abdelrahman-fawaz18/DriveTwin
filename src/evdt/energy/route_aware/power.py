"""Forward movement envelopes derived from declared wheel and battery limits."""

import math

from .geometry import RemainingRoute, RouteUnavailable
from .wire import ForecastRequest


def movement_limits(
    request: ForecastRequest, route: RemainingRoute, factors: tuple[float, ...]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Bound speed and acceleration before integration; never raise a power limit.

    Each road uses the worst supplied condition over its horizon. This envelope
    also covers declared stop load changes. The ledger retains time-local values.
    """
    vehicle, state = request.vehicle, request.state
    power = vehicle.max_discharge_w
    if vehicle.discharge_by_temperature is not None:
        if state.battery_temperature_k is None:
            raise RouteUnavailable("required_curve_input_missing")
        power = min(power, vehicle.discharge_by_temperature.at(state.battery_temperature_k))
    efficiency = (
        min(
            (p.y for p in vehicle.propulsion_by_power.points), default=vehicle.propulsion_efficiency
        )
        if vehicle.propulsion_by_power
        else vehicle.propulsion_efficiency
    )
    localized = (
        {r.occurrence_index: r.intervals for r in request.scenario.roads}
        if request.scenario
        else {}
    )
    stop_masses = [vehicle.loaded_mass_kg]
    equipment_peak = 0.0
    if request.scenario:
        stop_masses.extend(
            s.loaded_mass_on_departure_kg
            for s in request.scenario.stops
            if s.loaded_mass_on_departure_kg is not None
        )
        if request.scenario.equipment:
            equipment = request.scenario.equipment
            equipment_peak = (
                equipment.base_electrical_w
                + equipment.ventilation_electrical_w
                + max(
                    equipment.maximum_heating_electrical_w, equipment.maximum_cooling_electrical_w
                )
            )
    speeds, accelerations = [], []
    for piece, factor in zip(route.pieces, factors, strict=True):
        conditions = tuple(
            c
            for c in localized.get(piece.occurrence_index, request.conditions)
            if c.until_working_s > state.working_s
        )
        if not conditions:
            raise RouteUnavailable("conditions_do_not_cover_plan")
        mass = max(*stop_masses, *(c.loaded_mass_kg or vehicle.loaded_mass_kg for c in conditions))
        effective_mass = mass + vehicle.rotating_mass_kg
        theta = math.atan(piece.grade_ratio)
        east, north = math.sin(piece.heading_rad), math.cos(piece.heading_rad)

        def demand(speed: float, acceleration: float) -> float:
            largest = 0.0
            for condition in conditions:
                density = vehicle.air_density_kg_m3
                if (
                    condition.pressure_pa is not None
                    and condition.ambient_temperature_k is not None
                ):
                    density = condition.pressure_pa / (287.05 * condition.ambient_temperature_k)
                re = speed * east - condition.wind_east_m_s
                rn = speed * north - condition.wind_north_m_s
                drag = (
                    0.5
                    * density
                    * vehicle.frontal_area_m2
                    * vehicle.drag_coefficient
                    * math.hypot(re, rn)
                    * (re * east + rn * north)
                )
                force = (
                    mass
                    * vehicle.gravity_m_s2
                    * (
                        math.sin(theta)
                        + vehicle.rolling_coefficient
                        * condition.rolling_multiplier
                        * math.cos(theta)
                    )
                    + drag
                )
                auxiliary = (
                    max(state.auxiliary_power_w, condition.auxiliary_power_w or 0, equipment_peak)
                    + condition.extra_auxiliary_power_w
                )
                largest = max(
                    largest,
                    max(0, (force + effective_mass * acceleration) * speed) / efficiency
                    + auxiliary,
                )
            return largest

        ceiling = 0.99 * power
        if demand(0, 0) >= ceiling:
            raise RouteUnavailable("equipment_exceeds_available_power")
        speed = piece.speed_limit_m_s * factor
        minimum_acceleration = min(0.25, vehicle.max_acceleration_m_s2)
        if demand(speed, minimum_acceleration) > ceiling:
            low, high = 0.0, speed
            for _ in range(40):
                mid = (low + high) / 2
                if demand(mid, minimum_acceleration) <= ceiling:
                    low = mid
                else:
                    high = mid
            speed = low
        if speed < 0.05:
            raise RouteUnavailable("road_exceeds_vehicle_climbing_envelope")
        low, high = 0.0, vehicle.max_acceleration_m_s2
        for _ in range(40):
            mid = (low + high) / 2
            if demand(speed, mid) <= ceiling:
                low = mid
            else:
                high = mid
        speeds.append(speed / piece.speed_limit_m_s)
        accelerations.append(low)
    return tuple(speeds), tuple(accelerations)
