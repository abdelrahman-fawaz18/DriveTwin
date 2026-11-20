"""Opt-in SUMO present-state projection for route-aware energy forecasting.

No future speeds, realized trip energy, or hidden battery capacity are read.
Ambiguous repeated-road stops require an explicitly indexed plan; guessing their
occurrence would create an incorrect route, so this adapter reports unavailable.
"""

import time
from bisect import bisect_left, bisect_right

from evdt.energy.route_aware.context import SourceContext, SourcePlan, SourceStop
from evdt.energy.route_aware.contracts import SourceScope
from evdt.energy.route_aware.validation import number
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint


def _stop_occurrences(edges, index, lane, offset, stops, stopped, lane_edge):
    """Find the unique ordered placement of SUMO's remaining stops."""
    by_edge = {}
    for occurrence in range(index, len(edges)):
        by_edge.setdefault(edges[occurrence], []).append(occurrence)
    choices = []
    for ordinal, stop in enumerate(stops):
        edge = lane_edge(stop.lane)
        if ordinal == 0 and stopped:
            if stop.lane != lane or edge != edges[index]:
                raise ValueError("current_stop_lane_mismatch")
            choices.append((index,))
        else:
            matches = by_edge.get(edge, ())
            if matches and matches[0] == index and stop.endPos + 1e-6 < offset:
                matches = matches[1:]
            choices.append(matches)

    earliest = []
    previous, position = index, float("-inf")
    for stop, matches in zip(stops, choices, strict=True):
        start = previous + (stop.endPos < position)
        choice = bisect_left(matches, start)
        if choice == len(matches):
            raise ValueError("ambiguous_stop_occurrence")
        previous, position = matches[choice], stop.endPos
        earliest.append(previous)

    following, position = len(edges) - 1, float("inf")
    for stop, matches, expected in zip(
        reversed(stops), reversed(choices), reversed(earliest), strict=True
    ):
        end = following - (stop.endPos > position)
        choice = bisect_right(matches, end) - 1
        if choice < 0 or matches[choice] != expected:
            raise ValueError("ambiguous_stop_occurrence")
        following, position = expected, stop.endPos
    return earliest


class RouteCapture:
    def __init__(self, identity, network_sha256, manifest_sha256, publish, *, indexed_journey=None):
        self.scope = SourceScope(**identity)
        self.network_sha256 = network_sha256
        self.manifest_sha256 = manifest_sha256
        self.publish = publish
        self.previous_plan = None
        self.current_context = None
        self.stop_deadline = None
        self.indexed_journey = indexed_journey

    def sample(self, connection, working_s, *, charging_state=None):
        vehicle, asset = connection.vehicle, self.scope.asset_id
        edges = tuple(vehicle.getRoute(asset))
        index = vehicle.getRouteIndex(asset)
        lane = vehicle.getLaneID(asset)
        offset = vehicle.getLanePosition(asset)
        stops = []
        current, remaining, next_stop_deadline = None, None, None
        stopped = bool(vehicle.getStopState(asset) & 1)
        source_stops = vehicle.getStops(asset)
        if self.indexed_journey is None:
            occurrences = _stop_occurrences(
                edges, index, lane, offset, source_stops, stopped, connection.lane.getEdgeID
            )
        else:
            occurrences = self.indexed_journey.stop_occurrences(edges, source_stops)
            # The terminal hold ends the source at arrival; it is not trip dwell.
            source_stops, occurrences = source_stops[:-1], occurrences[:-1]
        for ordinal, (stop, cursor) in enumerate(zip(source_stops, occurrences, strict=True)):
            active = ordinal == 0 and stopped
            # Triggered/until-based dwell needs a declared departure policy.
            if stop.until > 0 or stop.stopFlags & 6:
                raise ValueError("unsupported_departure_condition")
            duration = number(stop.duration, "source dwell", 0, 14_400)
            if active:
                # SUMO getStops reports the live countdown once the stop is reached.
                remaining = duration
                deadline = working_s + remaining
                location = (cursor, stop.lane, stop.endPos)
                previous = self.previous_plan
                if previous is not None and previous.edges == edges:
                    # Carry forward the scheduled dwell while the countdown advances.
                    duration = next(
                        (
                            prior.dwell_working_s
                            for prior in previous.remaining_stops
                            if (prior.external_index, prior.lane_id, prior.offset_m) == location
                        ),
                        duration,
                    )
                    if self.stop_deadline is not None and self.stop_deadline[0] == location:
                        # A changed release time can represent a genuine dwell edit.
                        previous_deadline = self.stop_deadline[1]
                        shift = deadline - previous_deadline
                        if remaining > 0 and abs(shift) > 1e-6:
                            duration = number(max(0, duration + shift), "assigned dwell", 0, 14_400)
                        if remaining == 0:
                            deadline = min(deadline, previous_deadline)
                next_stop_deadline = (location, deadline)
            stop_id = fingerprint(
                {
                    "road": cursor,
                    "lane": stop.lane,
                    "offset": stop.endPos,
                    "duration": duration,
                }
            )
            stops.append(SourceStop(stop_id, cursor, stop.lane, stop.endPos, duration))
            if active:
                current = stop_id
        if charging_state is None:
            charged = number(
                float(vehicle.getParameter(asset, "device.battery.energyCharged")),
                "observed external charge",
                0,
                1_000_000,
            )
            station = vehicle.getParameter(asset, "device.battery.chargingStationId")
            # SUMO's documented absence marker is the literal string "NULL".
            charging_state = (
                "charging" if charged > 0 or station not in ("", "NULL") else "not_charging"
            )
        context = SourceContext(
            "source-route-context/2.0.0",
            self.scope,
            self.manifest_sha256,
            working_s,
            time.time(),
            SourcePlan(self.network_sha256, edges, tuple(stops)),
            index,
            lane,
            offset,
            charging_state,
            current,
            remaining,
        )
        raw = canonical_bytes(context)
        if len(raw) > 65_536:
            raise ValueError("route_context_size_limit")
        result = self.publish(raw)
        if result.get("context_sha256") != fingerprint(context):
            raise RuntimeError("Registered route context acknowledgment mismatch")
        self.previous_plan = context.plan
        self.current_context = context
        self.stop_deadline = next_stop_deadline
        return {"energy_context": result["context_sha256"]}
