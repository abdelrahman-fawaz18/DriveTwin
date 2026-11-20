"""Stationary charging with a declared battery acceptance curve."""

import math
from dataclasses import dataclass

from .moving_charge_station import ChargeWindow
from .route_aware.contracts import Curve
from .route_aware.validation import number


@dataclass(frozen=True)
class ChargeTransfer:
    start_working_s: float
    end_working_s: float
    arrival_soc: float
    plug_in_soc: float
    departure_soc: float
    equipment_energy_j: float
    grid_energy_j: float
    wait_working_s: float


def charge_to(
    window: ChargeWindow,
    *,
    arrival_working_s: float,
    arrival_soc: float,
    reserve_soc: float,
    target_soc: float,
    capacity_kwh: float,
    equipment_w: float,
    acceptance_by_soc: Curve,
) -> ChargeTransfer:
    """Integrate a linear power curve exactly on each interval."""
    number(reserve_soc, "reserve charge", 0, 1)
    number(arrival_soc, "arrival charge", reserve_soc, 1)
    number(target_soc, "target charge", reserve_soc, 1)
    number(arrival_working_s, "station arrival time", 0, window.charge_from_working_s)
    number(capacity_kwh, "nominal pack capacity", 0.01, 10_000)
    number(equipment_w, "stationary equipment power", 0, 1_000_000)
    if (acceptance_by_soc.x_unit, acceptance_by_soc.y_unit) != ("1", "W"):
        raise ValueError("Charge acceptance must map charge fraction to watts")
    capacity_j = capacity_kwh * 3_600_000
    wait = window.charge_from_working_s - arrival_working_s
    at_plug = arrival_soc - wait * equipment_w / capacity_j
    if at_plug < reserve_soc:
        raise ValueError("below_reserve_while_waiting")
    if target_soc <= at_plug:
        raise ValueError("charging_target_must_add_energy")
    if any(
        not 0 <= point.x <= 1 or not 0 <= point.y <= 5_000_000 for point in acceptance_by_soc.points
    ):
        raise ValueError("Charge curve values are outside the battery domain")
    bay_power = window.input_power_w * window.efficiency

    knots = [at_plug]
    knots.extend(point.x for point in acceptance_by_soc.points if at_plug < point.x < target_soc)
    knots.append(target_soc)
    durations = []
    for lo, hi in zip(knots, knots[1:]):
        p0, p1 = acceptance_by_soc.at(lo), acceptance_by_soc.at(hi)
        points = [(lo, p0), (hi, p1)]
        if (p0 - bay_power) * (p1 - bay_power) < 0:
            crossing = lo + (hi - lo) * (bay_power - p0) / (p1 - p0)
            points.insert(1, (crossing, bay_power))
        for (a, p_a), (b, p_b) in zip(points, points[1:]):
            net_a, net_b = min(bay_power, p_a) - equipment_w, min(bay_power, p_b) - equipment_w
            if min(net_a, net_b) <= 0:
                raise ValueError("insufficient_net_charging_power")
            relative = (net_b - net_a) / net_a
            factor = math.log1p(relative) / relative if relative else 1.0
            durations.append(capacity_j * (b - a) / net_a * factor)
    duration = math.fsum(durations)
    finish = window.charge_from_working_s + duration
    if finish > window.charge_until_working_s:
        raise ValueError("access_window_ends_before_target")
    stored_j = (target_soc - at_plug) * capacity_j
    return ChargeTransfer(
        window.charge_from_working_s,
        finish,
        arrival_soc,
        at_plug,
        target_soc,
        (wait + duration) * equipment_w,
        (stored_j + duration * equipment_w) / window.efficiency,
        wait,
    )
