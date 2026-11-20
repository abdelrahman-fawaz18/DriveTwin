"""Occurrence-aware remaining road geometry with explicit stop and height quality."""

import math
from dataclasses import dataclass, replace

from .contracts import FixedSignal, LiveState, RoadPoint, RouteContext, RouteOccurrence, StopRef
from .validation import integer, number


class RouteUnavailable(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RemainingStop:
    reference: StopRef
    dwell_working_s: float


@dataclass(frozen=True)
class RoadPiece:
    occurrence_index: int
    edge_id: str
    road_class: str
    start: RoadPoint
    end: RoadPoint
    horizontal_m: float
    distance_m: float
    grade_ratio: float
    heading_rad: float
    speed_limit_m_s: float
    elevation_quality: str
    stops_after: tuple[RemainingStop, ...] = ()
    heading_basis: str = "local_east_north"
    yield_visibility_m: float | None = None
    fixed_signal: FixedSignal | None = None
    signal_controlled: bool = False
    source_distance_m: float | None = None


@dataclass(frozen=True)
class RemainingRoute:
    pieces: tuple[RoadPiece, ...]
    initial_stops: tuple[RemainingStop, ...]
    distance_m: float
    horizontal_m: float
    ascent_m: float
    descent_m: float
    assumptions: tuple[str, ...]


def horizontal_length(shape: tuple[RoadPoint, ...]) -> float:
    return math.fsum(math.hypot(b.x_m - a.x_m, b.y_m - a.y_m) for a, b in zip(shape, shape[1:]))


def reference_length(occurrence: RouteOccurrence) -> float:
    return occurrence.reference_length_m or horizontal_length(occurrence.shape)


def interpolate(a: RoadPoint, b: RoadPoint, fraction: float) -> RoadPoint:
    number(fraction, "shape fraction", 0, 1)
    z = None
    if a.elevation_m is not None and b.elevation_m is not None:
        z = a.elevation_m + fraction * (b.elevation_m - a.elevation_m)
    lat, lon = None, None
    if (
        a.latitude_deg is not None
        and b.latitude_deg is not None
        and a.longitude_deg is not None
        and b.longitude_deg is not None
    ):
        lat = a.latitude_deg + fraction * (b.latitude_deg - a.latitude_deg)
        delta_lon = (b.longitude_deg - a.longitude_deg + 180) % 360 - 180
        lon = (a.longitude_deg + fraction * delta_lon + 180) % 360 - 180
    return RoadPoint(
        a.x_m + fraction * (b.x_m - a.x_m), a.y_m + fraction * (b.y_m - a.y_m), z, lat, lon
    )


def true_heading(a: RoadPoint, b: RoadPoint, frame: str) -> tuple[float, str]:
    """Initial compass bearing for short prepared segments, or an explicit local frame."""
    if (
        a.latitude_deg is not None
        and b.latitude_deg is not None
        and a.longitude_deg is not None
        and b.longitude_deg is not None
    ):
        lat1, lat2 = math.radians(a.latitude_deg), math.radians(b.latitude_deg)
        dlon = math.radians(b.longitude_deg - a.longitude_deg)
        east = math.sin(dlon) * math.cos(lat2)
        north = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        if math.hypot(east, north) < 1e-14:
            raise RouteUnavailable("geographic_segment_has_no_direction")
        return math.atan2(east, north) % (2 * math.pi), "geographic_bearing"
    if frame != "local-east-north-meters":
        raise RouteUnavailable("true_heading_requires_geographic_coordinates")
    return math.atan2(b.x_m - a.x_m, b.y_m - a.y_m) % (2 * math.pi), "local_east_north"


def _stop_map(context: RouteContext, state: LiveState) -> dict[StopRef, float]:
    stops: dict[StopRef, float] = {}
    offsets: dict[StopRef, float] = {}
    for occurrence in context.occurrences:
        length = reference_length(occurrence)
        for stop in occurrence.stops:
            if stop.offset_m > length:
                raise RouteUnavailable("stop_outside_occurrence")
            ref = StopRef(occurrence.index, stop.stop_id)
            offsets[ref] = stop.offset_m
            stops[ref] = stop.dwell_working_s
    for ref in state.completed_stops:
        if ref not in stops:
            raise RouteUnavailable("unknown_completed_stop")
        if ref.occurrence_index > state.occurrence_index or (
            ref.occurrence_index == state.occurrence_index
            and offsets[ref] > state.lane_offset_m + 1e-6
        ):
            raise RouteUnavailable("completed_stop_ahead_of_progress")
        del stops[ref]
    if state.current_stop is not None:
        if state.current_stop not in stops:
            raise RouteUnavailable("unknown_current_stop")
        if abs(offsets[state.current_stop] - state.lane_offset_m) > 0.5:
            raise RouteUnavailable("current_stop_position_mismatch")
        if state.speed_m_s > 0.1:
            raise RouteUnavailable("current_stop_while_moving")
        if state.remaining_dwell_working_s is not None:
            stops[state.current_stop] = state.remaining_dwell_working_s
    for ref, offset in offsets.items():
        if ref in stops and (
            ref.occurrence_index < state.occurrence_index
            or (
                ref.occurrence_index == state.occurrence_index
                and offset < state.lane_offset_m - 1e-6
                and ref != state.current_stop
            )
        ):
            raise RouteUnavailable("passed_stop_without_update")
    return stops


def _check_connectivity(context: RouteContext) -> None:
    for before, after in zip(context.occurrences, context.occurrences[1:]):
        a, b = before.shape[-1], after.shape[0]
        if math.hypot(a.x_m - b.x_m, a.y_m - b.y_m) > 1e-6:
            raise RouteUnavailable("route_connection_missing")
        if (
            a.elevation_m is not None
            and b.elevation_m is not None
            and abs(a.elevation_m - b.elevation_m) > 1e-6
        ):
            raise RouteUnavailable("route_elevation_discontinuous")


def _take_stops(
    stops: dict[float, list[RemainingStop]], position: float
) -> tuple[RemainingStop, ...]:
    matches = sorted(p for p in stops if abs(p - position) <= 1e-6)
    return tuple(stop for p in matches for stop in stops.pop(p))


def remaining_route(
    context: RouteContext, state: LiveState, *, max_pieces: int = 10_000
) -> RemainingRoute:
    """Trim by occurrence and lane-relative offset, preserving every intermediate hill.

    Source offsets refer to reference_length_m when provided. They are mapped
    proportionally to horizontal shape distance. The resulting physical path uses
    three-dimensional lengths. Source geometry must include actual junction links.
    """
    integer(max_pieces, "piece limit", 1, 10_000)
    if state.route_revision != context.revision:
        raise RouteUnavailable("route_revision_mismatch")
    if state.occurrence_index >= len(context.occurrences):
        raise RouteUnavailable("occurrence_outside_route")
    _check_connectivity(context)
    stops = _stop_map(context, state)
    pieces: list[RoadPiece] = []
    initial_stops: list[RemainingStop] = []
    assumptions: set[str] = set()
    for occurrence in context.occurrences[state.occurrence_index :]:
        if occurrence.geometry_basis == "assumed_lane_change":
            assumptions.add("assumed_lane_change")
        if occurrence.elevation_quality == "missing":
            raise RouteUnavailable("elevation_missing")
        if occurrence.elevation_quality in (
            "declared_flat",
            "synthetic",
            "terrain",
            "surface_model",
            "portal_interpolation",
        ):
            assumptions.add("elevation_" + occurrence.elevation_quality)
        full_horizontal = horizontal_length(occurrence.shape)
        ref_length = reference_length(occurrence)
        scale = full_horizontal / ref_length
        if abs(scale - 1) > 1e-6:
            assumptions.add("source_offset_scaled_to_geometry")
        offset = state.lane_offset_m if occurrence.index == state.occurrence_index else 0.0
        if offset > ref_length + 1e-6:
            raise RouteUnavailable("progress_outside_occurrence")
        cutoff = min(offset, ref_length) * scale
        yield_at = None
        if occurrence.yield_visibility_m is not None:
            yield_at = max(0.0, ref_length - occurrence.yield_visibility_m) * scale
            assumptions.add("unopposed_minor_junction_visibility_braking")
        stop_positions: dict[float, list[RemainingStop]] = {}
        for stop in occurrence.stops:
            ref = StopRef(occurrence.index, stop.stop_id)
            if ref not in stops:
                continue
            position = stop.offset_m * scale
            if ref == state.current_stop:
                position = cutoff
            stop_positions.setdefault(position, []).append(RemainingStop(ref, stops[ref]))
        current_stops = _take_stops(stop_positions, cutoff)
        if current_stops:
            if pieces:
                pieces[-1] = replace(pieces[-1], stops_after=pieces[-1].stops_after + current_stops)
            else:
                initial_stops.extend(current_stops)
        traversed = 0.0
        for a, b in zip(occurrence.shape, occurrence.shape[1:]):
            horizontal = math.hypot(b.x_m - a.x_m, b.y_m - a.y_m)
            end_at = traversed + horizontal
            if end_at <= cutoff:
                traversed = end_at
                continue
            start_at = max(traversed, cutoff)
            cuts = sorted(p for p in stop_positions if start_at + 1e-6 < p < end_at - 1e-6)
            if yield_at is not None and start_at + 1e-6 < yield_at < end_at - 1e-6:
                cuts = sorted(set((*cuts, yield_at)))
            boundaries = [start_at, *cuts, end_at]
            for start, end in zip(boundaries, boundaries[1:]):
                p = interpolate(a, b, max(0, min(1, (start - traversed) / horizontal)))
                q = interpolate(a, b, max(0, min(1, (end - traversed) / horizontal)))
                if p.elevation_m is None or q.elevation_m is None:
                    raise RouteUnavailable("elevation_missing")
                delta_z = q.elevation_m - p.elevation_m
                horizontal_piece = end - start
                grade = delta_z / horizontal_piece
                if abs(grade) > 0.4:
                    raise RouteUnavailable("road_grade_outside_domain")
                heading, heading_basis = true_heading(p, q, context.horizontal_frame)
                pieces.append(
                    RoadPiece(
                        occurrence.index,
                        occurrence.edge_id,
                        occurrence.road_class,
                        p,
                        q,
                        horizontal_piece,
                        math.hypot(horizontal_piece, delta_z),
                        grade,
                        heading,
                        occurrence.speed_limit_m_s,
                        occurrence.elevation_quality,
                        _take_stops(stop_positions, end),
                        heading_basis,
                        occurrence.yield_visibility_m
                        if yield_at is not None and abs(end - yield_at) <= 1e-6
                        else None,
                        occurrence.fixed_signal
                        if abs(end - horizontal_length(occurrence.shape)) <= 1e-6
                        else None,
                        bool(occurrence.signal_controlled)
                        and abs(end - horizontal_length(occurrence.shape)) <= 1e-6,
                        source_distance_m=horizontal_piece / scale,
                    )
                )
                if len(pieces) > max_pieces:
                    raise RouteUnavailable("route_piece_budget_exceeded")
            traversed = end_at
        if stop_positions:
            raise RouteUnavailable("unresolved_stop_position")
    return RemainingRoute(
        tuple(pieces),
        tuple(initial_stops),
        math.fsum(p.distance_m for p in pieces),
        math.fsum(p.horizontal_m for p in pieces),
        math.fsum(max(0.0, p.grade_ratio * p.horizontal_m) for p in pieces),
        math.fsum(max(0.0, -p.grade_ratio * p.horizontal_m) for p in pieces),
        tuple(sorted(assumptions)),
    )
