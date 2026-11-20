"""Hash-bound geographic inputs for a paced, independently observed SUMO trip."""

import hashlib
import json
import math
import shutil
import xml.etree.ElementTree as ET

from evdt.energy.moving_charge_detour import detour_indices


class PreparedJourney:
    def __init__(self, scenario):
        self.scenario = scenario
        self.spec = json.loads((scenario / "prepared-journey.json").read_bytes())
        if self.spec["version"] != "prepared-live-journey/1.0.0":
            raise ValueError("Unsupported prepared journey")
        for name in ("network.net.xml", "routes.rou.xml"):
            if (
                hashlib.sha256((scenario / name).read_bytes()).hexdigest()
                != self.spec["sha256"][name]
            ):
                raise ValueError("Prepared journey input changed: " + name)
        self.arrived = False
        self.charge_stop = None

    def detour_plan(self, detour):
        """Remap the protected stops, endpoint, and restricted road visits."""
        edges, indices = detour_indices(tuple(self.spec["route_edges"]), detour)
        if self.spec.get("speed_zones"):
            raise ValueError("Charging detour requires a reviewed restriction mapping")
        try:

            def remap(stop):
                return {**stop, "external_index": indices[stop["external_index"]]}

            return {
                **self.spec,
                "route_edges": list(edges),
                "endpoint": remap(self.spec["endpoint"]),
                "stops": [remap(stop) for stop in self.spec["stops"]],
            }
        except KeyError as exc:
            raise ValueError("Charging detour would omit a scheduled stop") from exc

    def install(self, work, asset):
        shutil.copyfile(self.scenario / "network.net.xml", work / "network.net.xml")
        routes = ET.parse(self.scenario / "routes.rou.xml")
        vehicles = routes.findall("vehicle")
        if len(vehicles) != 1:
            raise ValueError("A live journey must contain exactly one bus")
        vehicles[0].set("id", asset)
        routes.write(work / "routes.rou.xml", encoding="utf-8", xml_declaration=True)

    def observe(self, connection, asset):
        vehicle = connection.vehicle
        index = vehicle.getRouteIndex(asset)
        factor = self.spec["speed_factor"]
        for zone in self.spec.get("speed_zones", []):
            if zone["start_index"] <= index < zone["end_index"]:
                factor = min(factor, zone["speed_factor"])
        vehicle.setSpeedFactor(asset, factor)
        finish = self.spec["endpoint"]
        self.arrived = (
            index == finish["external_index"]
            and vehicle.getLaneID(asset) == finish["lane_id"]
            and vehicle.getLanePosition(asset) >= finish["offset_m"] - 0.2
            and vehicle.getSpeed(asset) <= 0.001
        )

    def trip_progress(self, connection, asset):
        """Report distance along the active SUMO route and the first remaining passenger stop."""
        vehicle = connection.vehicle
        edges = tuple(vehicle.getRoute(asset))
        index = vehicle.getRouteIndex(asset)
        finish = self.spec["endpoint"]
        if not 0 <= index <= finish["external_index"] == len(edges) - 1:
            raise ValueError("Observed route differs from the assigned destination")
        stops = vehicle.getStops(asset)
        self.stop_occurrences(edges, stops)
        charging_bay = self.charge_stop.bay.bay_id if self.charge_stop is not None else None
        passenger_stops = (
            [stop for stop in stops if stop.stoppingPlaceID != charging_bay]
            if charging_bay is not None
            else stops
        )
        # The validated suffix includes the terminal hold as its last entry.
        next_stop = self.spec["stops"][-len(passenger_stops)] if len(passenger_stops) > 1 else None
        stop_id = next_stop.get("stop_id") if next_stop else None
        if next_stop is not None and not isinstance(stop_id, str):
            raise ValueError("Passenger stop identity is unavailable")
        remaining = 0 if self.arrived else None
        target = edges[-1]
        if not self.arrived and edges[index:].count(target) == 1:
            # TraCI accepts an edge ID, which cannot distinguish repeated future visits.
            lane_index = int(finish["lane_id"].rsplit("_", 1)[1])
            distance = vehicle.getDrivingDistance(asset, target, finish["offset_m"], lane_index)
            if math.isfinite(distance) and 0 <= distance <= 10_000_000:
                remaining = round(distance, 1)
        return {
            "remaining_m": remaining,
            "next_stop_id": stop_id,
            "at_passenger_stop": bool(
                next_stop and passenger_stops[0] is stops[0] and vehicle.getStopState(asset) & 1
            ),
        }

    def stop_occurrences(self, edges, stops):
        if tuple(edges) != tuple(self.spec["route_edges"]):
            raise ValueError("Prepared live route changed; indexed stops need a new assignment")
        if self.charge_stop is not None:
            entry = self.charge_stop
            matching = [
                i for i, stop in enumerate(stops) if stop.stoppingPlaceID == entry.bay.bay_id
            ]
            if matching:
                if len(matching) != 1:
                    raise ValueError("Duplicate charging stop observed")
                position = matching[0]
                stop = stops[position]
                if stop.lane != entry.bay.lane_id or (
                    abs(stop.endPos - entry.bay.end_offset_m) > 0.001
                ):
                    raise ValueError("Observed charging stop differs from its assignment")
                other = (*stops[:position], *stops[position + 1 :])
                indices = self.passenger_stop_occurrences(other)
                indices.insert(position, entry.external_index)
                if indices != sorted(indices):
                    raise ValueError("Charging stop reordered the passenger stops")
                return indices
        return self.passenger_stop_occurrences(stops)

    def passenger_stop_occurrences(self, stops):
        assigned = self.spec["stops"]
        if len(stops) > len(assigned):
            raise ValueError("Unexpected stop added to the prepared live route")
        remaining = assigned[len(assigned) - len(stops) :]
        for actual, expected in zip(stops, remaining, strict=True):
            if (
                actual.lane != expected["lane_id"]
                or abs(actual.endPos - expected["offset_m"]) > 0.001
            ):
                raise ValueError("Observed stops differ from the indexed source assignment")
        return [s["external_index"] for s in remaining]
