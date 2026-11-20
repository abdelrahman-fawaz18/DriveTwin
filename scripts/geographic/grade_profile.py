"""Resolve raster-scale longitudinal grades on the connected assigned roadway."""

import math

from road_profile import interpolate_profile, smooth_profile

from evdt.energy.route_aware.elevation import densify
from evdt.energy.route_aware.sumo_routes import expand_route


def regularize(network, edges, parsed, groups):
    route = expand_route(network, tuple(edges), revision="elevation-preparation").context
    distances, heights = [], []
    spans = {}
    previous = None
    position = 0.0
    for road in route.occurrences:
        start = position
        for point in densify(road.shape, 10):
            if previous is not None:
                step = math.hypot(point.x_m - previous.x_m, point.y_m - previous.y_m)
                if step <= 1e-6:
                    continue
                position += step
            choices = []
            for lane in (road.lane_id, *road.alternative_lane_ids):
                for a, b in zip(parsed[lane], parsed[lane][1:]):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    length = dx * dx + dy * dy
                    if not length:
                        continue
                    t = max(0, min(1, ((point.x_m - a[0]) * dx + (point.y_m - a[1]) * dy) / length))
                    choices.append(
                        (
                            math.hypot(point.x_m - a[0] - t * dx, point.y_m - a[1] - t * dy),
                            a[2] + t * (b[2] - a[2]),
                        )
                    )
            distances.append(position)
            heights.append(min(choices)[1])
            previous = point
        spans.setdefault(road.edge_id, []).append((start, position))
    smoothed = smooth_profile(distances, heights)
    adjusted = {}
    for identity, line in parsed.items():
        edge = network.lanes[identity].edge_id
        if edge not in spans:
            continue
        lengths = [0.0]
        for a, b in zip(line, line[1:]):
            lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        values = []
        for d, p in zip(lengths, line):
            estimates = [
                interpolate_profile(distances, smoothed, a + d / lengths[-1] * (b - a))
                for a, b in spans[edge]
            ]
            values.append((p[0], p[1], sum(estimates) / len(estimates)))
        adjusted[identity] = values
    result = {**parsed, **adjusted}
    for group in groups:
        selected = [result[lane][end][2] for lane, end in group if lane in adjusted]
        if not selected:
            continue
        height = sum(selected) / len(selected)
        for lane, end in group:
            point = result[lane][end]
            result[lane][end] = (point[0], point[1], height)
    for identity, line in result.items():
        if network.lanes[identity].internal and identity not in adjusted:
            # Other legal turn lanes share the measured junction portals.
            # Short internal links have no independently resolvable raster grade.
            lengths = [0.0]
            for a, b in zip(line, line[1:]):
                lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
            result[identity] = [
                (p[0], p[1], line[0][2] + d / lengths[-1] * (line[-1][2] - line[0][2]))
                for d, p in zip(lengths, line)
            ]
    return result, {
        "method": "Uniform 5 m resampling; local linear grade fit with 15 m radius; shared-carriageway longitudinal profile",
        "spacing_m": 5,
        "radius_m": 15,
        "crossfall_modeled": False,
        "working_distance_m": distances,
        "raster_elevation_m": heights,
        "processed_elevation_m": smoothed,
        "maximum_height_adjustment_m": max(abs(a - b) for a, b in zip(heights, smoothed)),
        "flat_profile_assumed": False,
    }
