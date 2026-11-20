"""Offline station energy forecasts from declared departure and route data."""

import math
from dataclasses import dataclass

JOULES_PER_KWH = 3_600_000
CAPACITY_KWH = 350
RESERVE_KWH = 35


@dataclass(frozen=True)
class StationApproach:
    initial_energy_kwh: float
    auxiliary_power_w: float
    distance_m: float
    free_flow_s: float
    traffic_signals: int
    expected_wait_s: float

    def __post_init__(self):
        values = vars(self).values()
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise ValueError("Approach inputs must be finite numbers")
        if (
            not CAPACITY_KWH * 0.101 <= self.initial_energy_kwh <= CAPACITY_KWH * 0.16
            or not 8000 <= self.auxiliary_power_w <= 12000
            or self.distance_m <= 0
            or self.free_flow_s <= 0
            or type(self.traffic_signals) is not int
            or self.traffic_signals < 0
            or not 0 <= self.expected_wait_s <= 45
        ):
            raise ValueError("Approach is outside the station forecast domain")

    def drive_time_s(self, route_aware):
        if route_aware:
            return 1.6 * self.free_flow_s + 6 * self.traffic_signals + 5
        return self.distance_m / 8


def forecast(approach, *, traction_kwh_per_km, route_aware, radius_kwh=0.0):
    if not (
        math.isfinite(traction_kwh_per_km)
        and 0 <= traction_kwh_per_km <= 3
        and math.isfinite(radius_kwh)
        and 0 <= radius_kwh <= 1.75
    ):
        raise ValueError("Invalid station forecast coefficients")
    drive_time_s = approach.drive_time_s(route_aware)
    drive_aux_kwh = approach.auxiliary_power_w * drive_time_s / JOULES_PER_KWH
    wait_kwh = approach.auxiliary_power_w * approach.expected_wait_s / JOULES_PER_KWH
    arrival = approach.initial_energy_kwh - drive_aux_kwh
    arrival -= traction_kwh_per_km * approach.distance_m / 1000
    # Estimate energy recovered in the final braking event on a flat route.
    braking_speed_squared = min(8**2, approach.distance_m)
    rebound_kwh = (
        0.55 * 18_000 * braking_speed_squared / (2 * JOULES_PER_KWH) if route_aware else 0.0
    )
    minimum = arrival - max(wait_kwh, rebound_kwh)
    return {
        "predicted_drive_s": drive_time_s,
        "arrival_kwh": arrival,
        "arrival_soc_pct": 100 * arrival / CAPACITY_KWH,
        "minimum_kwh": minimum,
        "arrival_interval_kwh": [arrival - radius_kwh, arrival + radius_kwh],
        "minimum_interval_kwh": [minimum - radius_kwh, minimum + radius_kwh],
        "reserve_feasible": minimum - radius_kwh >= RESERVE_KWH,
    }
