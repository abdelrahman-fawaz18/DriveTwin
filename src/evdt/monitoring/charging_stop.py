"""A guarded, on-route charging stop insertion for a SUMO step boundary."""

import math
import xml.etree.ElementTree as ET

# TraCI command flag for the chargingStation stopping place.
CHARGING_STATION = 32
POSITION_TOLERANCE_M = 0.001
MOVING_CHARGE_STOP_LIMIT_S = 600


class StopInsertionRefused(ValueError):
    """No simulator mutation was sent."""


def stop_identity(stop):
    return (
        stop.lane,
        stop.startPos,
        stop.endPos,
        stop.stoppingPlaceID,
        stop.stopFlags,
        stop.duration,
        stop.until,
        stop.split,
        stop.join,
    )


def write_stations(path, bays):
    extra = ET.Element("additional")
    for bay in bays:
        ET.SubElement(
            extra,
            "chargingStation",
            id=bay.bay_id,
            lane=bay.lane_id,
            startPos=str(bay.start_offset_m),
            endPos=str(bay.end_offset_m),
            power=str(bay.power_w),
            efficiency=str(bay.efficiency),
            chargeDelay=str(bay.setup_working_s),
            chargeInTransit="false",
        )
    ET.ElementTree(extra).write(path, encoding="utf-8", xml_declaration=True)


def admitted_charge_wh(vehicle, asset, bay):
    reported = float(vehicle.getParameter(asset, "device.battery.energyCharged"))
    if not math.isfinite(reported) or reported < 0:
        raise RuntimeError("Invalid SUMO charger reading")
    if (
        bay is None
        or not reported
        or not vehicle.isStopped(asset)
        or vehicle.getSpeed(asset) >= 0.1
    ):
        return 0.0
    stops = vehicle.getStops(asset, 1)
    lane = vehicle.getLaneID(asset)
    offset = vehicle.getLanePosition(asset)
    if (
        not stops
        or stops[0].stoppingPlaceID != bay.bay_id
        or lane != bay.lane_id
        or not bay.start_offset_m <= offset <= bay.end_offset_m + POSITION_TOLERANCE_M
    ):
        return 0.0
    if vehicle.getParameter(asset, "device.battery.chargingStationId") != bay.bay_id:
        raise RuntimeError("Charging station identity disagrees with the authorized stop")
    return reported


def insert_on_route_charge(
    connection,
    asset,
    journey,
    assigned,
    *,
    stop_limit_s,
    minimum_lead_m,
    braking_m_s2,
    held_bay=None,
):
    """Check progress and insert a single stop without advancing SUMO.

    The caller owns command authorization. If mutation was attempted and any
    postcondition fails, the caller must stop and reconcile before stepping.
    """
    vehicle, bay = connection.vehicle, assigned.bay
    route = tuple(vehicle.getRoute(asset))
    lane, position = vehicle.getLaneID(asset), vehicle.getLanePosition(asset)
    index, speed = vehicle.getRouteIndex(asset), vehicle.getSpeed(asset)
    stopped = vehicle.isStopped(asset)
    motion_eligible = stopped and speed == 0 if held_bay is not None else not stopped and speed > 0
    if (
        not 0 <= index < assigned.external_index < len(route) - 1
        or lane.startswith(":")
        or not math.isfinite(position)
        or not math.isfinite(speed)
        or not motion_eligible
        or not 0 < stop_limit_s <= 14_400
        or not 0 < braking_m_s2 <= 10
        or not 0 <= minimum_lead_m <= 10_000
    ):
        raise StopInsertionRefused("Charging stop is outside the reviewed handoff domain")
    if (
        connection.chargingstation.getLaneID(bay.bay_id) != bay.lane_id
        or abs(connection.chargingstation.getStartPos(bay.bay_id) - bay.start_offset_m)
        > POSITION_TOLERANCE_M
        or abs(connection.chargingstation.getEndPos(bay.bay_id) - bay.end_offset_m)
        > POSITION_TOLERANCE_M
    ):
        raise StopInsertionRefused("SUMO station does not match the reviewed bay")
    target = route[assigned.external_index]
    if connection.lane.getEdgeID(bay.lane_id) != target or route[index:].count(target) != 1:
        raise StopInsertionRefused("The station needs one unambiguous upcoming road visit")
    lane_index = int(bay.lane_id.rsplit("_", 1)[1])
    distance = vehicle.getDrivingDistance(asset, target, bay.end_offset_m, lane_index)
    lead = max(minimum_lead_m, speed + speed**2 / (2 * braking_m_s2))
    if not math.isfinite(distance) or not lead <= distance:
        raise StopInsertionRefused("The vehicle has insufficient distance to reach the stop")
    before = vehicle.getStops(asset)
    occurrences = journey.stop_occurrences(route, before)
    if not occurrences or assigned.external_index in occurrences:
        raise StopInsertionRefused("Charging must use a road visit without a passenger stop")
    insert_at = sum(i < assigned.external_index for i in occurrences)
    charging = [stop for stop in before if stop.stopFlags & CHARGING_STATION]
    if held_bay is None:
        if charging:
            raise StopInsertionRefused("An upcoming charging stop already exists")
    elif (
        len(charging) != 1
        or charging[0] is not before[0]
        or before[0].stoppingPlaceID != held_bay.bay_id
        or before[0].lane != held_bay.lane_id
        or held_bay.bay_id == bay.bay_id
        or lane != held_bay.lane_id
        or not held_bay.start_offset_m <= position <= held_bay.end_offset_m + POSITION_TOLERANCE_M
        or connection.chargingstation.getChargingPower(held_bay.bay_id) != 0
        or connection.chargingstation.getChargingPower(bay.bay_id) != bay.power_w
    ):
        raise StopInsertionRefused("The held bay or replacement power no longer matches")

    vehicle.insertStop(
        asset, insert_at, bay.bay_id, duration=stop_limit_s, flags=CHARGING_STATION, teleport=0
    )
    after = vehicle.getStops(asset)
    expected = tuple(map(stop_identity, before))
    observed = tuple(map(stop_identity, (*after[:insert_at], *after[insert_at + 1 :])))
    if (
        tuple(vehicle.getRoute(asset)) != route
        or len(after) != len(before) + 1
        or observed != expected
        or after[insert_at].stoppingPlaceID != bay.bay_id
        or after[insert_at].lane != bay.lane_id
        or not after[insert_at].stopFlags & CHARGING_STATION
        or abs(after[insert_at].endPos - bay.end_offset_m) > POSITION_TOLERANCE_M
    ):
        raise RuntimeError("Inserted stop could not be reconciled before the next SUMO step")
    if held_bay is not None:
        vehicle.resume(asset)
        if (
            tuple(vehicle.getRoute(asset)) != route
            or vehicle.getRouteIndex(asset) != index
            or tuple(map(stop_identity, vehicle.getStops(asset)))
            != tuple(map(stop_identity, after[1:]))
        ):
            raise RuntimeError(
                "Replacement release could not be reconciled before the next SUMO step"
            )
        return insert_at - 1
    return insert_at
