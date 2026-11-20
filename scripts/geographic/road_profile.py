"""Conservative removal of isolated raster spikes along a measured road profile."""

import math
import statistics
from bisect import bisect_left


def interpolate_profile(distances, heights, position):
    i = min(len(distances) - 1, max(1, bisect_left(distances, position)))
    fraction = (position - distances[i - 1]) / (distances[i] - distances[i - 1])
    return heights[i - 1] + fraction * (heights[i] - heights[i - 1])


def selection_grade(points, spacing_m=10):
    """Compare raster layers at a physical scale, not subpixel map vertices.

    This selects an elevation source only. It neither changes the samples nor
    caps the final road grade. Short junctions use their complete length.
    """
    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    count = max(1, math.ceil(distances[-1] / spacing_m))
    step = distances[-1] / count
    heights = [p[2] for p in points]
    sampled = [interpolate_profile(distances, heights, i * step) for i in range(count + 1)]
    return max(abs(b - a) / step for a, b in zip(sampled, sampled[1:]))


def junction_transition(points, start_height, end_height):
    """Interpolate a short bridge/ground junction between measured road portals.

    SUMO trims the mapped external roads at intersections. The resulting internal
    link can straddle a bridge end, where neither terrain nor surface alone
    represents the entire link. This approximation is limited to 100 m and must
    be labeled in the height receipt; it is not a tunnel or long-bridge model.
    """
    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    length = distances[-1]
    if not 0 < length <= 100 or abs(end_height - start_height) / length > 0.20:
        raise ValueError("Bridge approach junction needs an independent roadway profile")
    return [
        (p[0], p[1], start_height + d / length * (end_height - start_height))
        for p, d in zip(points, distances)
    ]


def bridge_portal(surface, approach, *, surface_end, approach_end):
    """Resolve endpoint canopy using a stable measured ground-road abutment.

    Only the last ten meters of a bridge are interpolated. Its interior surface
    remains unchanged. A ground portal depressed below its approach trend is
    rejected; that case needs surface data instead.
    """
    ground = list(reversed(approach)) if approach_end == -1 else approach
    deck = list(reversed(surface)) if surface_end == -1 else surface

    def distances(line):
        out = [0.0]
        for a, b in zip(line, line[1:]):
            out.append(out[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        return out

    gd, sd = distances(ground), distances(deck)
    if gd[-1] < 15 or sd[-1] < 15:
        return None
    heights = [p[2] for p in ground]
    near, far = [interpolate_profile(gd, heights, d) for d in (5, 15)]
    slope = (far - near) / 10
    portal = ground[0][2]
    if abs(slope) > 0.20 or abs(portal - (near - 5 * slope)) > 1:
        return None
    interior = interpolate_profile(sd, [p[2] for p in deck], 10)
    if abs(interior - portal) / 10 > 0.20:
        return None
    repaired = [
        (p[0], p[1], portal + d / 10 * (interior - portal)) if d < 10 else p
        for p, d in zip(deck, sd)
    ]
    return list(reversed(repaired)) if surface_end == -1 else repaired


def fuse_road_layers(ground, surface, *, surface_at_start=False, surface_at_end=False):
    """Resolve a ground depression with continuous measured surface samples.

    A road can pass through tree canopy and across a small unmapped bridge in
    the same lane. Whole-lane DTM/DSM selection then fails. On a uniform <=10 m
    grid, select between the two measured layers with a 20% continuity bound.
    Ground portals are retained unless a connected bridge requires surface at
    that portal. Surface use has a small penalty; no height is
    fitted to an energy outcome, and no constant-height fallback is available.
    Return None when the measured alternatives cannot form a coherent profile.
    """
    distances = [0.0]
    for a, b in zip(ground, ground[1:]):
        distances.append(distances[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    count = max(1, math.ceil(distances[-1] / 10))
    step = distances[-1] / count
    locations = [i * step for i in range(count + 1)]
    g = [interpolate_profile(distances, [p[2] for p in ground], x) for x in locations]
    s = [interpolate_profile(distances, [p[2] for p in surface], x) for x in locations]
    first = 1 if surface_at_start else 0
    states = {first: (0.0, [s[0] if first else g[0]], ["dsm" if first else "dtm"])}
    for i in range(1, count + 1):
        next_states = {}
        for layer, z in enumerate((g[i], s[i])):
            if i == count and layer != int(surface_at_end):
                continue
            for cost, heights, layers in states.values():
                grade = (z - heights[-1]) / step
                if abs(grade) > 0.20:
                    continue
                value = cost + grade * grade * step + (0.002 * step if layer else 0)
                if layer not in next_states or value < next_states[layer][0]:
                    next_states[layer] = (
                        value,
                        [*heights, z],
                        [*layers, "dsm" if layer else "dtm"],
                    )
        states = next_states
        if not states:
            return None
    _, selected, layers = min(states.values(), key=lambda v: v[0])
    if "dsm" not in layers:
        return None
    output = [
        (p[0], p[1], interpolate_profile(locations, selected, d)) for p, d in zip(ground, distances)
    ]
    return output, {
        "distance_m": locations,
        "ground_m": g,
        "surface_m": s,
        "selected_m": selected,
        "layers": layers,
        "method": "Measured DTM/DSM continuity selection at 10 m with declared portal layers",
        "surface_at_start": surface_at_start,
        "surface_at_end": surface_at_end,
    }


def smooth_profile(distances, heights, *, spacing_m=5, radius_m=15):
    """Fit local grades on uniformly spaced elevation samples.

    Raster noise is not a road ramp. Resampling prevents dense junction vertices
    from dominating the fit. Local weighted linear regression retains constant
    grades, ascent, and descent; it does not impose a flat profile or a grade cap.
    The caller records the original profile and this processing scale.
    """
    count = max(1, math.ceil(distances[-1] / spacing_m))
    uniform = [distances[-1] * i / count for i in range(count + 1)]
    values = [interpolate_profile(distances, heights, x) for x in uniform]
    fitted = []
    for i, x in enumerate(uniform):
        samples = [
            (t - x, z, 1 - abs(t - x) / (radius_m + spacing_m))
            for t, z in zip(uniform[max(0, i - 4) : i + 5], values[max(0, i - 4) : i + 5])
            if abs(t - x) <= radius_m
        ]
        weight = sum(w for _, _, w in samples)
        mx = sum(t * w for t, _, w in samples) / weight
        my = sum(z * w for _, z, w in samples) / weight
        variance = sum(w * (t - mx) ** 2 for t, _, w in samples)
        slope = sum(w * (t - mx) * (z - my) for t, z, w in samples) / variance if variance else 0
        fitted.append(my - slope * mx)
    return [interpolate_profile(uniform, fitted, x) for x in distances]


def remove_spikes(values):
    """Replace only isolated reversing spikes, retaining hills and sustained grades.

    A sample must differ from its 10 m neighborhood by both 0.75 m and three
    robust deviations. A neighboring pair must bracket it, reverse slope, and
    imply a slope over 20 percent. Endpoints and broad terrain changes remain.
    Every replaced height and its interpolation support are returned for audit.
    """
    distances = [0.0]
    for a, b in zip(values, values[1:]):
        distances.append(distances[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    result = list(values)
    corrections = []
    for i in range(1, len(values) - 1):
        left, point, right = values[i - 1 : i + 2]
        dl, dr = distances[i] - distances[i - 1], distances[i + 1] - distances[i]
        if min(dl, dr) <= 0 or (point[2] - left[2]) * (right[2] - point[2]) >= 0:
            continue
        local = [p[2] for j, p in enumerate(values) if abs(distances[j] - distances[i]) <= 10]
        median = statistics.median(local)
        deviation = statistics.median(abs(z - median) for z in local)
        if abs(point[2] - median) <= max(0.75, 3 * 1.4826 * deviation):
            continue
        if max(abs(point[2] - left[2]) / dl, abs(right[2] - point[2]) / dr) <= 0.20:
            continue
        height = left[2] + dl / (dl + dr) * (right[2] - left[2])
        result[i] = (point[0], point[1], height)
        corrections.append(
            {
                "index": i,
                "distance_m": distances[i],
                "raw_m": point[2],
                "filtered_m": height,
                "support_indices": [i - 1, i + 1],
            }
        )
    return result, corrections
