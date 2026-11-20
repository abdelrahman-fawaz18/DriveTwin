"""Account for the equipment load while a queued SUMO vehicle waits at zero power."""

import math

from .charging_stop import POSITION_TOLERANCE_M


def waiting_at_bay(vehicle, asset, bay) -> bool:
    stops = vehicle.getStops(asset, 1)
    return bool(
        stops
        and stops[0].stoppingPlaceID == bay.bay_id
        and vehicle.isStopped(asset)
        and vehicle.getSpeed(asset) < 0.1
        and vehicle.getLaneID(asset) == bay.lane_id
        and bay.start_offset_m
        <= vehicle.getLanePosition(asset)
        <= bay.end_offset_m + POSITION_TOLERANCE_M
    )


def apply_waiting_equipment(vehicle, asset, *, energy_j, native_draw_wh, equipment_w, step_s):
    """Top up the native draw to the declared equipment load for one waiting step."""
    if not all(math.isfinite(value) for value in (energy_j, native_draw_wh, equipment_w, step_s)):
        raise ValueError("Queued equipment inputs must be finite")
    if equipment_w < 0 or step_s <= 0 or energy_j < 0:
        raise ValueError("Invalid queued equipment inputs")
    extra_j = max(0.0, equipment_w * step_s - max(0.0, native_draw_wh * 3600))
    if not extra_j:
        return energy_j, native_draw_wh
    if extra_j > energy_j:
        raise RuntimeError("Battery cannot supply the queued equipment load")
    expected = energy_j - extra_j
    vehicle.setParameter(asset, "device.battery.chargeLevel", format(expected / 3600, ".17g"))
    observed = float(vehicle.getParameter(asset, "device.battery.chargeLevel")) * 3600
    if not math.isfinite(observed) or abs(observed - expected) > 0.01:
        raise RuntimeError("SUMO did not apply the declared equipment draw")
    return observed, native_draw_wh + extra_j / 3600
