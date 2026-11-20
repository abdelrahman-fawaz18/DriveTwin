"""Offline SUMO network parsing and lane-continuous route expansion.

An edge occurrence is a particular visit to a road. Internal junction lanes are
retained between those visits. No simulator process or future vehicle state is
needed to read the assigned geometry.
"""

import hashlib
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from io import BytesIO

from .contracts import (
    FixedSignal,
    Origin,
    PlannedStop,
    RoadPoint,
    RouteContext,
    RouteOccurrence,
    SignalWindow,
)
from .geometry import RouteUnavailable, horizontal_length, interpolate


@dataclass(frozen=True)
class NetworkLane:
    lane_id: str
    edge_id: str
    internal: bool
    road_class: str
    shape: tuple[RoadPoint, ...]
    length_m: float
    speed_limit_m_s: float
    index: int
    change_left: bool
    change_right: bool


@dataclass(frozen=True)
class Network:
    sha256: str
    projection: str
    offset_x_m: float
    offset_y_m: float
    lanes: Mapping[str, NetworkLane]
    connections: Mapping[str, tuple[str, ...]]
    minor_visibility: Mapping[tuple[str, str], float] = field(default_factory=dict)
    fixed_signals: Mapping[tuple[str, str], FixedSignal] = field(default_factory=dict)
    signal_connections: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExpandedRoute:
    context: RouteContext
    external_occurrences: tuple[int, ...]
    endpoint_snaps: int


def _elements(raw: bytes, tag: str) -> Iterator[ET.Element]:
    """Stream root children without keeping a second full XML graph in memory."""
    parser = ET.iterparse(BytesIO(raw), events=("start", "end"))
    _, root = next(parser)
    depth = 0
    for event, element in parser:
        if event == "start":
            depth += 1
        else:
            if depth == 1:
                if element.tag == tag:
                    yield element
                root.clear()
            depth -= 1


def read_network(
    raw: bytes, *, max_bytes: int = 16 * 1024 * 1024, include_yellow: bool = False
) -> Network:
    if not 1 <= max_bytes <= 64 * 1024 * 1024:
        raise ValueError("Network preprocessing budget exceeds 64 MiB")
    if len(raw) > max_bytes or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Network XML exceeds its budget or contains a document type")
    location = next(_elements(raw, "location"), None)
    if location is None:
        raise ValueError("Network coordinate reference is missing")
    offset = tuple(float(x) for x in location.attrib["netOffset"].split(","))
    if len(offset) != 2 or not all(math.isfinite(v) for v in offset):
        raise ValueError("Network offset must contain two finite values")
    projection = location.attrib["projParameter"]
    lanes: dict[str, NetworkLane] = {}
    by_index: dict[tuple[str, str], str] = {}
    for edge in _elements(raw, "edge"):
        for lane in edge.findall("lane"):
            allowed = lane.get("allow")
            if (
                (
                    allowed is not None
                    and "bus" not in allowed.split()
                    and "all" not in allowed.split()
                )
                or "bus" in lane.get("disallow", "").split()
                or "all" in lane.get("disallow", "").split()
            ):
                continue
            points: list[RoadPoint] = []
            for coordinate in lane.attrib["shape"].split():
                xyz = [float(x) for x in coordinate.split(",")]
                if len(xyz) not in (2, 3):
                    raise ValueError("Invalid lane shape coordinate")
                # Height admission requires a separate datum/source decision.
                point = RoadPoint(xyz[0], xyz[1], None)
                if not points or point != points[-1]:
                    points.append(point)
            if not points:
                raise ValueError("Empty lane geometry")
            identity = lane.attrib["id"]
            if identity in lanes:
                raise ValueError("Duplicate lane identity")
            length, speed = float(lane.attrib["length"]), float(lane.attrib["speed"])
            if not 0 < length <= 10_000_000 or not 0 < speed <= 60:
                raise ValueError("Invalid lane length or speed limit")
            lanes[identity] = NetworkLane(
                identity,
                edge.attrib["id"],
                edge.get("function") == "internal",
                edge.get("type", "junction"),
                tuple(points),
                length,
                speed,
                int(lane.attrib["index"]),
                lane.get("changeLeft") is None
                or bool({"bus", "all"} & set(lane.get("changeLeft", "").split())),
                lane.get("changeRight") is None
                or bool({"bus", "all"} & set(lane.get("changeRight", "").split())),
            )
            by_index[(edge.attrib["id"], lane.attrib["index"])] = identity
    connections: dict[str, set[str]] = {}
    minor_visibility: dict[tuple[str, str], float] = {}
    programs: dict[str, list[ET.Element]] = {}
    for logic in _elements(raw, "tlLogic"):
        programs.setdefault(logic.attrib["id"], []).append(logic)
    fixed_signals: dict[tuple[str, str], FixedSignal] = {}
    signal_connections = []
    for connection in _elements(raw, "connection"):
        source = by_index.get((connection.attrib["from"], connection.attrib["fromLane"]))
        target = connection.get("via") or by_index.get(
            (connection.attrib["to"], connection.attrib["toLane"])
        )
        if source in lanes and target in lanes:
            assert source is not None and target is not None
            connections.setdefault(source, set()).add(target)
            if connection.get("tl") is not None:
                signal_connections.append((source, target))
                choices = programs.get(connection.attrib["tl"], [])
                if len(choices) == 1 and choices[0].get("type") == "static":
                    logic = choices[0]
                    phases = logic.findall("phase")
                    link = int(connection.attrib["linkIndex"])
                    if not any(p.get("next") for p in phases):
                        elapsed = 0.0
                        windows = []
                        yellow = []
                        for phase in phases:
                            duration = float(phase.attrib["duration"])
                            state = phase.attrib["state"]
                            if duration <= 0 or not 0 <= link < len(state):
                                raise ValueError("Invalid fixed signal phase")
                            if state[link] in "Gg":
                                windows.append(SignalWindow(elapsed, elapsed + duration))
                            elif include_yellow and state[link] in "Yy":
                                yellow.append(SignalWindow(elapsed, elapsed + duration))
                            elapsed += duration
                        if windows:
                            fixed_signals[(source, target)] = FixedSignal(
                                logic.attrib["id"],
                                elapsed,
                                float(logic.get("offset", "0")),
                                tuple(windows),
                                tuple(yellow) or None,
                            )
            if connection.get("state") in ("m", "=") and connection.get("tl") is None:
                visibility = float(connection.get("visibility", "4.5"))
                if not 0 < visibility <= 1_000:
                    raise ValueError("Unsupported minor-junction visibility distance")
                minor_visibility[(source, target)] = visibility
    return Network(
        hashlib.sha256(raw).hexdigest(),
        projection,
        *offset,
        lanes,
        {key: tuple(sorted(value)) for key, value in connections.items()},
        minor_visibility,
        fixed_signals,
        tuple(signal_connections),
    )


def _links(network: Network, source: str, next_edge: str) -> list[tuple[str, ...]]:
    pending: list[tuple[str, ...]] = [(source,)]
    results: list[tuple[str, ...]] = []
    while pending:
        path = pending.pop()
        if len(path) > 32 or len(pending) + len(results) > 256:
            raise RouteUnavailable("junction_path_budget_exceeded")
        for target in network.connections.get(path[-1], ()):
            lane = network.lanes[target]
            if lane.internal:
                if target not in path:
                    pending.append((*path, target))
            elif lane.edge_id == next_edge:
                results.append((*path[1:], target))
    return sorted(results)


def _at_fraction(shape: tuple[RoadPoint, ...], fraction: float) -> RoadPoint:
    left = fraction * horizontal_length(shape)
    for a, b in zip(shape, shape[1:]):
        distance = math.hypot(b.x_m - a.x_m, b.y_m - a.y_m)
        if left <= distance:
            return interpolate(a, b, left / distance)
        left -= distance
    return shape[-1]


def _lane_change(entry: NetworkLane, exit_lane: NetworkLane) -> NetworkLane | None:
    """Declared smooth path across adjacent permitted lanes, not a measured maneuver."""
    delta = exit_lane.index - entry.index
    if entry.edge_id != exit_lane.edge_id or abs(delta) != 1 or entry.internal:
        return None
    if (delta > 0 and not entry.change_left) or (delta < 0 and not entry.change_right):
        return None
    if min(entry.length_m, exit_lane.length_m) < 30:
        return None
    fractions = {i / 20 for i in range(21)}
    for shape in (entry.shape, exit_lane.shape):
        length, position = horizontal_length(shape), 0.0
        for a, b in zip(shape, shape[1:]):
            position += math.hypot(b.x_m - a.x_m, b.y_m - a.y_m)
            fractions.add(min(1.0, position / length))
    points: list[RoadPoint] = []
    exit_length = horizontal_length(exit_lane.shape)
    previous_station = -1.0
    for fraction in sorted(fractions):
        a = _at_fraction(entry.shape, fraction)
        # Equal fractions of long curved lanes need not be abreast. Match the
        # adjacent carriageway within a bounded longitudinal window instead.
        station = 0.0
        matches: list[tuple[float, float, RoadPoint]] = []
        for left, right in zip(exit_lane.shape, exit_lane.shape[1:]):
            dx, dy = right.x_m - left.x_m, right.y_m - left.y_m
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                continue
            along = max(
                0.0, min(1.0, ((a.x_m - left.x_m) * dx + (a.y_m - left.y_m) * dy) / length**2)
            )
            position = station + along * length
            if abs(position - fraction * exit_length) <= max(10.0, 0.01 * exit_length):
                point = interpolate(left, right, along)
                matches.append((math.hypot(a.x_m - point.x_m, a.y_m - point.y_m), position, point))
            station += length
        if not matches:
            return None
        _, matched_station, b = min(matches, key=lambda value: value[:2])
        if matched_station + 0.02 < previous_station:
            return None
        previous_station = matched_station
        if fraction == 0:
            b = exit_lane.shape[0]
        elif fraction == 1:
            b = exit_lane.shape[-1]
        if math.hypot(b.x_m - a.x_m, b.y_m - a.y_m) > 5:
            return None
        t = min(1.0, max(0.0, (fraction - 0.2) / 0.6))
        point = interpolate(a, b, t * t * (3 - 2 * t))
        if not points or math.hypot(point.x_m - points[-1].x_m, point.y_m - points[-1].y_m) > 1e-6:
            points.append(point)
    if len(points) < 2:
        return None
    return replace(
        entry,
        shape=tuple(points),
        speed_limit_m_s=min(entry.speed_limit_m_s, exit_lane.speed_limit_m_s),
    )


def expand_route(
    network: Network,
    edge_ids: tuple[str, ...],
    *,
    revision: str,
    stops_by_external_index: Mapping[int, tuple[PlannedStop, ...]] | None = None,
    required_lanes: Mapping[int, str] | None = None,
    entry_lanes: Mapping[int, str] | None = None,
    current_junction_lane: str | None = None,
    structures: Mapping[str, str] | None = None,
) -> ExpandedRoute:
    """Prefer a connected lane path with the fewest assumed lane changes.

    required_lanes pins a stop or a known current lane to its external occurrence.
    Only rounding gaps up to two centimeters can be snapped, and they are counted.
    """
    if not 1 <= len(edge_ids) <= 5_000:
        raise ValueError("External route size is outside its budget")
    required_lanes, stops_by_external_index, structures = (
        required_lanes or {},
        stops_by_external_index or {},
        structures or {},
    )
    entry_lanes = entry_lanes or {}
    if any(i < 0 or i >= len(edge_ids) for i in entry_lanes):
        raise ValueError("Current lane occurrence is outside the route")
    if any(i < 0 or i >= len(edge_ids) for i in (*required_lanes, *stops_by_external_index)):
        raise ValueError("Lane/stop occurrence is outside the route")
    if current_junction_lane is not None and len(edge_ids) < 2:
        raise RouteUnavailable("current_junction_has_no_next_road")
    # Each completed leg is (entry lane, exit lane, following internal lanes).
    options: dict[str, tuple[int, tuple[tuple[str, str, tuple[str, ...]], ...]]] = {
        lane_id: (0, ())
        for lane_id, lane in network.lanes.items()
        if not lane.internal
        and lane.edge_id == edge_ids[0]
        and required_lanes.get(0, lane_id) == lane_id
        and entry_lanes.get(0, lane_id) == lane_id
    }
    for index, edge in enumerate(edge_ids[1:], start=1):
        following: dict[str, tuple[int, tuple[tuple[str, str, tuple[str, ...]], ...]]] = {}
        for lane_id, (cost, legs) in sorted(options.items()):
            entry = network.lanes[lane_id]
            exits = [lane_id]
            if index - 1 not in required_lanes:
                exits += [
                    k
                    for k, lane in network.lanes.items()
                    if lane.edge_id == entry.edge_id
                    and k != lane_id
                    and _lane_change(entry, lane) is not None
                ]
            for exit_id in sorted(exits):
                for link in _links(network, exit_id, edge):
                    if (
                        index == 1
                        and current_junction_lane is not None
                        and current_junction_lane not in link[:-1]
                    ):
                        continue
                    last = link[-1]
                    if required_lanes.get(index, last) != last:
                        continue
                    if entry_lanes.get(index, last) != last:
                        continue
                    candidate = (
                        cost + int(exit_id != lane_id),
                        (*legs, (lane_id, exit_id, link[:-1])),
                    )
                    if last not in following or candidate < following[last]:
                        following[last] = candidate
        options = following
        if not options:
            raise RouteUnavailable("lane_continuous_route_unavailable")
    if not options:
        raise RouteUnavailable("route_start_lane_unavailable")
    last, (_, legs) = min(options.items(), key=lambda item: (item[1], item[0]))
    legs = (*legs, (last, last, ()))
    expanded_lanes: list[tuple[NetworkLane, int | None, tuple[str, ...]]] = []
    for index, (entry_id, exit_id, internal) in enumerate(legs):
        lane = network.lanes[entry_id]
        alternatives: tuple[str, ...] = ()
        if entry_id != exit_id:
            changed = _lane_change(lane, network.lanes[exit_id])
            assert changed is not None
            lane = changed
            alternatives = (entry_id, exit_id)
        expanded_lanes.append((lane, index, alternatives))
        expanded_lanes.extend((network.lanes[k], None, ()) for k in internal)
    occurrences: list[RouteOccurrence] = []
    external_normalized = []
    previous: RoadPoint | None = None
    snapped = 0
    for lane, external_index, alternatives in expanded_lanes:
        shape = lane.shape
        if previous is not None:
            gap = math.hypot(shape[0].x_m - previous.x_m, shape[0].y_m - previous.y_m)
            if gap > 0.020001:
                raise RouteUnavailable("lane_geometry_disconnected")
            if gap:
                shape = (previous, *shape[1:])
                snapped += 1
        if len(shape) == 1:
            if not lane.internal:
                raise RouteUnavailable("external_lane_has_no_geometry")
            # SUMO sometimes emits a nominal 0.1 m internal edge at a shared point.
            previous = shape[-1]
            continue
        stops = (
            stops_by_external_index.get(external_index, ()) if external_index is not None else ()
        )
        if external_index is not None:
            external_normalized.append(len(occurrences))
        occurrences.append(
            RouteOccurrence(
                len(occurrences),
                lane.edge_id,
                lane.lane_id,
                shape,
                lane.speed_limit_m_s,
                lane.road_class,
                structures.get(lane.edge_id, "surface"),
                "missing",
                stops,
                lane.length_m,
                "assumed_lane_change" if alternatives else "lane_centerline",
                alternatives,
            )
        )
        previous = shape[-1]
    context = RouteContext(
        network.sha256,
        revision,
        "sumo-network-meters",
        "unassigned",
        Origin("declared", "SUMO road geometry", network.sha256),
        tuple(occurrences),
    )
    return ExpandedRoute(context, tuple(external_normalized), snapped)
