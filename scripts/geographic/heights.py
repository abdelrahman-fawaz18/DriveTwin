"""Attach attributable public raster heights to real imported OSM road geometry."""

import argparse
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

from raster_catalog import RasterCatalog
from rasterio.warp import transform
from road_profile import (
    bridge_portal,
    fuse_road_layers,
    junction_transition,
    remove_spikes,
    selection_grade,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/route_aware_energy")]
from grade_profile import regularize  # noqa: E402
from prepare_route import load_grid, structures_from_osm  # noqa: E402
from terrain import fetch  # noqa: E402

from evdt.energy.route_aware.contracts import RoadPoint  # noqa: E402
from evdt.energy.route_aware.elevation import densify  # noqa: E402
from evdt.energy.route_aware.sumo_routes import _elements, read_network  # noqa: E402


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


class Terrain:
    def __init__(self, manifest=None):
        self.grids = OrderedDict()
        self.receipts = {}
        self.custom = Path(manifest) if manifest else None
        self.catalog = RasterCatalog(self.custom, ROOT) if self.custom else None

    def sample(self, longitude, latitude, layer):
        if self.custom:
            manifest, record = self.catalog.select(longitude, latitude, layer)
            key = (str(manifest), layer)
        else:
            # Geographic tile indexes select downloads only; all interpolation
            # is done in the provider's declared metric coordinate frame.
            col, row = math.floor(longitude / 0.02), math.floor(latitude / 0.01)
            key = (col, row, layer)
            manifest = None
        if key not in self.grids:
            if manifest is None:
                bounds = (
                    col * 0.02 - 0.0001,
                    row * 0.01 - 0.0001,
                    (col + 1) * 0.02 + 0.0001,
                    (row + 1) * 0.01 + 0.0001,
                )
                record = fetch(bounds, 2, layer=layer)
                manifest = (
                    ROOT
                    / "data/downloads/energy-v2"
                    / (hashlib.sha256(record["request_url"].encode()).hexdigest() + ".json")
                )
            grid = load_grid(manifest)
            self.receipts[record["sha256"]] = record
            self.grids[key] = grid
            if len(self.grids) > 3:
                self.grids.popitem(last=False)
        grid = self.grids[key]
        self.grids.move_to_end(key)
        x, y = transform("EPSG:4326", grid.metadata.horizontal_frame, [longitude], [latitude])
        value = grid.sample(x[0], y[0])
        if value is None:
            raise ValueError(f"Missing {layer} coverage at {longitude:.6f}, {latitude:.6f}")
        return value


def prepare(folder, custom=None):
    original = folder / "route.net.xml"
    if (folder / "heights.json").exists():
        raise ValueError("Elevation receipt already exists; preserve this prepared input")
    network = read_network(original.read_bytes())
    mission = json.loads((folder / "mission.json").read_bytes())
    structures = structures_from_osm(
        folder / "roads.osm.xml",
        tuple(lane.edge_id for lane in network.lanes.values()),
        max_bytes=32 * 1024 * 1024,
    )
    tree = ET.parse(original)
    road_widths = {}
    osm_raw = (folder / "roads.osm.xml").read_bytes()
    nodes = {
        node.get("id"): (float(node.get("lon")), float(node.get("lat")))
        for node in _elements(osm_raw, "node")
    }
    required_ways = {
        lane.edge_id.lstrip("-").split("#")[0]
        for lane in network.lanes.values()
        if not lane.internal
    }
    road_centers = {}
    for way in _elements(osm_raw, "way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        lanes = tags.get("lanes", "2")
        if lanes.isdigit():
            road_widths[way.get("id")] = min(19.2, max(3.2, int(lanes) * 3.2))
        if way.get("id") in required_ways:
            coordinates = [nodes[ref.get("ref")] for ref in way.findall("nd")]
            lon, lat = zip(*coordinates)
            east, north = transform("EPSG:4326", network.projection, lon, lat)
            road_centers[way.get("id")] = [
                (x + network.offset_x_m, y + network.offset_y_m) for x, y in zip(east, north)
            ]
    del nodes, osm_raw
    centers = {}
    widths = {}
    for edge in tree.getroot().findall("edge"):
        if not edge.get("id").startswith(":"):
            identity = edge.get("id").lstrip("-").split("#")[0]
            line = (
                [tuple(map(float, p.split(",")[:2])) for p in edge.get("shape").split()]
                if edge.get("shape")
                else road_centers.get(identity)
            )
            if line:
                centers[edge.get("id")] = line
                widths[edge.get("id")] = road_widths.get(identity, 6.4)
    # Junction lane heights must use the same surface as the adjoining structure.
    for c in tree.getroot().findall("connection"):
        if c.get("via"):
            kind = structures.get(c.get("from"), "surface")
            if kind == "surface":
                kind = structures.get(c.get("to"), "surface")
            if kind != "surface":
                structures[c.get("via").rsplit("_", 1)[0]] = kind
                adjoining = c.get("from") if structures.get(c.get("from")) == kind else c.get("to")
                if adjoining in centers:
                    identity = c.get("via").rsplit("_", 1)[0]
                    centers[identity], widths[identity] = centers[adjoining], widths[adjoining]
    terrain = Terrain(custom)
    quality, shapes = {}, {}
    surface_resolutions = []
    raster_corrections = []
    points = {}
    max_grade = 0.0
    surface_sections = {}
    fused_profiles = {}

    def surface_profile(shape, identity, expected_end=None):
        """Estimate the road surface from DSM returns inside its mapped carriageway.

        The lower envelope rejects canopy and gantry returns. OSM road width is
        derived from its lane count (3.2 m nominal per lane); crossfall is omitted.
        Profiles still have to pass grade and connected-endpoint checks.
        """
        edge = network.lanes[identity].edge_id
        line = centers.get(edge)
        lon, lat = transform(
            network.projection,
            "EPSG:4326",
            [p.x_m - network.offset_x_m for p in shape],
            [p.y_m - network.offset_y_m for p in shape],
        )
        center = [(p.x_m, p.y_m, terrain.sample(x, y, "dsm")) for p, x, y in zip(shape, lon, lat)]
        center, corrections = remove_spikes(center)

        def max_grade(profile):
            return selection_grade(profile)

        if max_grade(center) < 0.20 and (
            expected_end is None or abs(center[expected_end[0]][2] - expected_end[1]) <= 2
        ):
            raster_corrections.extend(
                {"lane": identity, "layer": "dsm", **item} for item in corrections
            )
            return center
        output = []
        for p in shape:
            candidates = []
            if line:
                for a, b in zip(line, line[1:]):
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    length = math.hypot(dx, dy)
                    if not length:
                        continue
                    fraction = max(
                        0, min(1, ((p.x_m - a[0]) * dx + (p.y_m - a[1]) * dy) / length**2)
                    )
                    x, y = a[0] + fraction * dx, a[1] + fraction * dy
                    candidates.append(
                        (math.hypot(p.x_m - x, p.y_m - y), x, y, -dy / length, dx / length)
                    )
            if candidates and min(candidates)[0] <= widths[edge]:
                _, x, y, nx, ny = min(candidates)
                # OSM centerlines and the elevation survey can be horizontally
                # displaced. Include a documented 3 m registration margin;
                # this remains an estimated roadway surface, not a survey.
                half = max(0, widths[edge] / 2 - 0.5) + 3
                locations = [(x + nx * half * i / 4, y + ny * half * i / 4) for i in range(-4, 5)]
            else:
                locations = [(p.x_m, p.y_m)]
            lon, lat = transform(
                network.projection,
                "EPSG:4326",
                [x - network.offset_x_m for x, y in locations],
                [y - network.offset_y_m for x, y in locations],
            )
            samples = [terrain.sample(x, y, "dsm") for x, y in zip(lon, lat)]
            height = min(samples)
            surface_sections.setdefault(identity, []).append(
                {
                    "xy": [p.x_m, p.y_m],
                    "selected_m": height,
                    "minimum_m": min(samples),
                    "maximum_m": max(samples),
                    "sample_count": len(samples),
                }
            )
            output.append((p.x_m, p.y_m, height))
        output, corrections = remove_spikes(output)
        if (
            max_grade(output) < max_grade(center)
            or expected_end is not None
            and max_grade(output) < 0.20
        ) and (expected_end is None or abs(output[expected_end[0]][2] - expected_end[1]) <= 2):
            raster_corrections.extend(
                {"lane": identity, "layer": "dsm", **item} for item in corrections
            )
            return output
        return center

    for edge in tree.getroot().findall("edge"):
        kind = structures.get(edge.get("id"), "surface")
        if kind == "tunnel":
            raise ValueError(
                "Tunnel roadway needs a supplied measured roadway-height profile; ground or roof heights are not road heights"
            )
        layer = "dsm" if kind == "bridge" else "dtm"
        for lane in edge.findall("lane"):
            identity = lane.get("id")
            if identity not in network.lanes:
                continue
            geometry = network.lanes[identity].shape
            if len(geometry) < 2:
                continue
            shape = densify(geometry, 10)
            lon, lat = transform(
                network.projection,
                "EPSG:4326",
                [p.x_m - network.offset_x_m for p in shape],
                [p.y_m - network.offset_y_m for p in shape],
            )
            values = []
            for p, x, y in zip(shape, lon, lat, strict=True):
                key = (p.x_m, p.y_m, layer)
                if key not in points:
                    points[key] = terrain.sample(x, y, layer)
                values.append((p.x_m, p.y_m, points[key]))
            if layer == "dsm":
                values = surface_profile(shape, identity)

            def steepness(items):
                return selection_grade(items)

            lane_grade = steepness(values)
            selected_layer = layer
            if layer == "dsm":
                values, corrections = remove_spikes(values)
                raster_corrections.extend(
                    {"lane": identity, "layer": layer, **item} for item in corrections
                )
                lane_grade = steepness(values)
            if layer == "dtm" and lane_grade > 0.20:
                direct_surface = [
                    (p.x_m, p.y_m, terrain.sample(x, y, "dsm")) for p, x, y in zip(shape, lon, lat)
                ]
                direct_surface, _ = remove_spikes(direct_surface)
                fused = fuse_road_layers(values, direct_surface)
                if fused is not None:
                    values, detail = fused
                    fused_profiles[identity] = detail
                    selected_layer, lane_grade = "dsm", steepness(values)
            if layer == "dtm" and lane_grade > 0.20:
                surface = surface_profile(shape, identity)
                surface, corrections = remove_spikes(surface)
                fused = fuse_road_layers(values, surface)
                if fused is not None:
                    values, detail = fused
                    detail["surface_sampling"] = "Mapped carriageway lower surface envelope"
                    fused_profiles[identity] = detail
                    selected_layer, lane_grade = "dsm", steepness(values)
                surface_grade = steepness(surface)
                surface_resolutions.append(
                    {
                        "lane": identity,
                        "dtm_max_grade": lane_grade,
                        "dsm_max_grade": surface_grade,
                        "selected": "dsm" if surface_grade < 0.20 else "dtm",
                        "reason": "Compare measured surface against a ground depression; do not flatten or clip elevation",
                    }
                )
                if surface_grade < 0.20 and selected_layer != "dsm":
                    values, selected_layer, lane_grade = surface, "dsm", surface_grade
                    raster_corrections.extend(
                        {"lane": identity, "layer": "dsm", **item} for item in corrections
                    )
            max_grade = max(max_grade, lane_grade)
            lane.set("shape", " ".join(",".join(f"{v:.9f}" for v in p) for p in values))
            shapes[identity] = lane.get("shape")
            quality[identity] = "surface_model" if selected_layer == "dsm" else "terrain"
    enriched = folder / "elevated-route.net.xml"
    junction_profiles = []
    reverse = {}
    for source, targets in network.connections.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)
    for identity in shapes:
        lane = network.lanes[identity]
        if not lane.internal:
            continue
        before = [
            key
            for key in reverse.get(identity, ())
            if key in shapes and not network.lanes[key].internal
        ]
        after = [
            key
            for key in network.connections.get(identity, ())
            if key in shapes and not network.lanes[key].internal
        ]
        if len(before) != 1 or len(after) != 1:
            continue
        left, right = before[0], after[0]
        kinds = [structures.get(network.lanes[key].edge_id, "surface") for key in (left, right)]
        if set(kinds) == {"surface", "bridge"}:
            bridge, ground = (left, right) if kinds[0] == "bridge" else (right, left)
            bridge_end, ground_end = (-1, 0) if bridge == left else (0, -1)
            bridge_line = [tuple(map(float, p.split(","))) for p in shapes[bridge].split()]
            ground_line = [tuple(map(float, p.split(","))) for p in shapes[ground].split()]
            if (
                quality[ground] == "terrain"
                and abs(bridge_line[bridge_end][2] - ground_line[ground_end][2]) > 2
            ):
                repaired = bridge_portal(
                    bridge_line, ground_line, surface_end=bridge_end, approach_end=ground_end
                )
                if repaired is not None:
                    shapes[bridge] = " ".join(",".join(f"{v:.9f}" for v in p) for p in repaired)
                    junction_profiles.append(
                        {
                            "lane": bridge,
                            "ground_approach": ground,
                            "end": bridge_end,
                            "original_endpoint_m": bridge_line[bridge_end][2],
                            "measured_abutment_m": ground_line[ground_end][2],
                            "method": "Ten-meter bridge-end interpolation to a ground abutment consistent with its measured approach trend; interior surface retained",
                        }
                    )
        values = [tuple(map(float, p.split(","))) for p in shapes[identity].split()]
        start = tuple(map(float, shapes[left].split()[-1].split(",")))
        end = tuple(map(float, shapes[right].split()[0].split(",")))
        mismatch = max(abs(start[2] - values[0][2]), abs(end[2] - values[-1][2]))
        if set(kinds) != {"surface", "bridge"} and mismatch <= 2:
            continue
        if math.dist(start[:2], values[0][:2]) > 0.1 or math.dist(end[:2], values[-1][:2]) > 0.1:
            continue
        try:
            fitted = junction_transition(values, start[2], end[2])
        except ValueError:
            # Keep the sampled profile. The later continuity and grade checks
            # still reject an unresolved transition; never invent a long ramp.
            continue
        shapes[identity] = " ".join(",".join(f"{v:.9f}" for v in p) for p in fitted)
        quality[identity] = "portal_interpolation"
        junction_profiles.append(
            {
                "lane": identity,
                "from_lane": left,
                "to_lane": right,
                "start_m": start[2],
                "end_m": end[2],
                "method": "Short junction with conflicting raster returns interpolated between adjoining sampled roadway portals",
            }
        )
    # A shared carriageway can intersect different raster pixels in adjacent
    # lanes. Transfer an intact measured surface profile across that carriageway
    # when a lane's raster instead follows ground beneath the deck or an obstacle.
    # This retains the measured longitudinal relief; it does not flatten the road.
    cross_sections = []
    for edge in tree.getroot().findall("edge"):
        peers = [lane.get("id") for lane in edge.findall("lane") if lane.get("id") in shapes]
        parsed_peers = {
            key: [tuple(map(float, p.split(","))) for p in shapes[key].split()] for key in peers
        }

        def grade(line):
            return selection_grade(line)

        reliable = [
            key
            for key in peers
            if quality[key] == "surface_model" and grade(parsed_peers[key]) < 0.20
        ]
        for key in peers:
            if grade(parsed_peers[key]) <= 0.20 or not reliable:
                continue
            source = min(
                reliable, key=lambda p: abs(network.lanes[p].index - network.lanes[key].index)
            )
            if abs(network.lanes[source].index - network.lanes[key].index) > 1:
                continue
            from evdt.energy.route_aware.sumo_routes import _at_fraction

            source_shape = tuple(RoadPoint(*p) for p in parsed_peers[source])
            line = parsed_peers[key]
            total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(line, line[1:]))
            distance = 0
            replacement = []
            for i, p in enumerate(line):
                if i:
                    distance += math.hypot(p[0] - line[i - 1][0], p[1] - line[i - 1][1])
                sample = _at_fraction(source_shape, min(1, distance / total))
                if math.hypot(p[0] - sample.x_m, p[1] - sample.y_m) > 5.1:
                    break
                replacement.append((p[0], p[1], sample.elevation_m))
            if len(replacement) == len(line):
                shapes[key] = " ".join(",".join(f"{v:.9f}" for v in p) for p in replacement)
                quality[key] = "surface_model"
                cross_sections.append(
                    {
                        "lane": key,
                        "measured_surface_lane": source,
                        "assumption": "Shared carriageway longitudinal elevation; crossfall not modeled",
                    }
                )
    # Reconcile raster estimates at physically joined lane endpoints. A junction
    # cannot have two heights merely because one side sampled the surface layer.
    lane_nodes = {
        lane.get("id"): lane for lane in tree.getroot().iter("lane") if lane.get("id") in shapes
    }
    parent = {}

    def find(key):
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    for source, targets in network.connections.items():
        for target in targets:
            if source in shapes and target in shapes:
                parent[find((source, -1))] = find((target, 0))
    groups = {}
    parsed = {
        key: [tuple(map(float, p.split(","))) for p in value.split()]
        for key, value in shapes.items()
    }
    # Peer-lane reconciliation can change a portal after the first junction
    # pass. Resolve the short connecting link against those final road portals.
    for identity, values in list(parsed.items()):
        if not network.lanes[identity].internal:
            continue
        before = [
            k for k in reverse.get(identity, ()) if k in parsed and not network.lanes[k].internal
        ]
        after = [
            k
            for k in network.connections.get(identity, ())
            if k in parsed and not network.lanes[k].internal
        ]
        if len(before) != 1 or len(after) != 1:
            continue
        start, end = parsed[before[0]][-1], parsed[after[0]][0]
        if max(abs(start[2] - values[0][2]), abs(end[2] - values[-1][2])) <= 2:
            continue
        try:
            parsed[identity] = junction_transition(values, start[2], end[2])
        except ValueError:
            continue
        quality[identity] = "portal_interpolation"
        junction_profiles.append(
            {
                "lane": identity,
                "from_lane": before[0],
                "to_lane": after[0],
                "start_m": start[2],
                "end_m": end[2],
                "method": "Reconciled roadway portals after peer-lane surface processing",
            }
        )
    for key in parent:
        groups.setdefault(find(key), []).append(key)
    boundary_layers = []
    for _ in range(len(parsed) + 1):
        changed = False
        for group in groups.values():
            elevations = [parsed[lane][end][2] for lane, end in group]
            if max(elevations) - min(elevations) <= 2:
                continue
            ground_z = [parsed[lane][end][2] for lane, end in group if quality[lane] == "terrain"]
            surface_candidates = [
                parsed[lane][end][2] for lane, end in group if quality[lane] == "surface_model"
            ]
            if ground_z or surface_candidates:
                ground_endpoint = (
                    sum(ground_z) / len(ground_z) if ground_z else min(surface_candidates)
                )
                for lane, end in group:
                    if (
                        quality[lane] != "surface_model"
                        or abs(parsed[lane][end][2] - ground_endpoint) > 3
                    ):
                        continue
                    original_profile = parsed[lane]
                    alternative = surface_profile(
                        tuple(RoadPoint(*p) for p in original_profile), lane, (end, ground_endpoint)
                    )
                    if (
                        grade(alternative) < 0.20
                        and abs(alternative[end][2] - ground_endpoint) <= 2
                        and alternative != original_profile
                    ):
                        boundary_layers.append(
                            {
                                "lane": lane,
                                "reason": "Roadway-strip surface resolves a small connected-road raster discrepancy",
                                "original_endpoint_m": original_profile[end][2],
                                "surface_endpoint_m": alternative[end][2],
                            }
                        )
                        parsed[lane] = alternative
                        changed = True
            surface_z = [
                parsed[lane][end][2] for lane, end in group if quality[lane] == "surface_model"
            ]
            if not surface_z:
                continue
            expected = sum(surface_z) / len(surface_z)
            for lane, end in group:
                if quality[lane] != "terrain":
                    continue
                line = parsed[lane]
                longitude, latitude = transform(
                    network.projection,
                    "EPSG:4326",
                    [p[0] - network.offset_x_m for p in line],
                    [p[1] - network.offset_y_m for p in line],
                )
                surface = surface_profile(tuple(RoadPoint(*p) for p in line), lane, (end, expected))
                surface, corrections = remove_spikes(surface)
                if grade(surface) >= 0.20:
                    fused = fuse_road_layers(
                        line, surface, surface_at_start=end == 0, surface_at_end=end == -1
                    )
                    if fused is not None:
                        surface, detail = fused
                        detail["reason"] = (
                            "Measured surface at a connected bridge portal; ground under canopy elsewhere"
                        )
                        fused_profiles[lane] = detail
                if grade(surface) < 0.20 and abs(surface[end][2] - expected) <= 2:
                    raster_corrections.extend(
                        {"lane": lane, "layer": "dsm", **item} for item in corrections
                    )
                    boundary_layers.append(
                        {
                            "lane": lane,
                            "reason": "Surface raster continues the connected roadway deck",
                            "ground_endpoint_m": line[end][2],
                            "surface_endpoint_m": surface[end][2],
                        }
                    )
                    parsed[lane] = surface
                    quality[lane] = "surface_model"
                    changed = True
        if not changed:
            break
    adjustments = []
    for group in groups.values():
        coordinates = [parsed[lane][end] for lane, end in group]
        zs = [p[2] for p in coordinates]
        if max(zs) - min(zs) > 2:
            write(
                folder / "height-conflict.json",
                {
                    "endpoints": [
                        {
                            "lane": lane,
                            "end": end,
                            "xyz": parsed[lane][end],
                            "quality": quality[lane],
                        }
                        for lane, end in group
                    ]
                },
            )
            raise ValueError(
                "Connected roadway heights differ by over 2 m; inspect height-conflict.json"
            )
        surface_z = [
            parsed[lane][end][2] for lane, end in group if quality[lane] == "surface_model"
        ]
        joined = sum(surface_z or zs) / len(surface_z or zs)
        for lane, end in group:
            p = parsed[lane][end]
            if abs(p[2] - joined) > 1e-8:
                adjustments.append({"lane": lane, "end": end, "delta_m": joined - p[2]})
            parsed[lane][end] = (p[0], p[1], joined)
    parsed, grade_processing = regularize(network, mission["route_edges"], parsed, groups.values())
    for identity, values in parsed.items():
        shapes[identity] = " ".join(",".join(f"{v:.9f}" for v in p) for p in values)
        lane_nodes[identity].set("shape", shapes[identity])
    tree.write(enriched, encoding="utf-8", xml_declaration=True)
    # The plant uses the original complete network, retaining signals, right of
    # way and all lane permissions. Only the traversable route's heights change.
    plant = ET.parse(folder / "imported.net.xml")
    for lane in plant.getroot().iter("lane"):
        if lane.get("id") in shapes:
            lane.set("shape", shapes[lane.get("id")])
    plant_path = folder / "elevated-plant.net.xml"
    plant.write(plant_path, encoding="utf-8", xml_declaration=True)
    digest = hashlib.sha256(enriched.read_bytes()).hexdigest()
    record = {
        "version": "osm-road-heights/1.0.0",
        "preparation_source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/geographic/heights.py",
                "scripts/geographic/road_profile.py",
                "scripts/geographic/grade_profile.py",
                "scripts/geographic/raster_catalog.py",
                "scripts/route_aware_energy/prepare_route.py",
                "scripts/route_aware_energy/terrain.py",
                "src/evdt/energy/route_aware/sumo_routes.py",
                "src/evdt/energy/route_aware/elevation.py",
            )
        },
        "network_sha256": digest,
        "plant_network_sha256": hashlib.sha256(plant_path.read_bytes()).hexdigest(),
        "original_network_sha256": mission["imported_network_sha256"],
        "vertical_datum": "CGVD2013"
        if not custom
        else json.loads(Path(custom).read_bytes())["vertical_datum"],
        "lane_quality": quality,
        "sources": list(terrain.receipts.values()),
        "maximum_raw_grade": max_grade,
        "flat_fallback_used": False,
        "surface_comparisons": surface_resolutions,
        "mixed_layer_profiles": list(fused_profiles),
        "isolated_raster_spike_corrections": raster_corrections,
        "surface_method": "Lower DSM envelope over mapped carriageway plus 3 m registration margin; nominal 3.2 m lane width, crossfall omitted; grade and junction checks retained",
        "surface_registration_margin_m": 3,
        "carriageway_surface_adjustments": cross_sections,
        "bridge_approach_junction_profiles": junction_profiles,
        "roadway_layer_adjustments": boundary_layers,
        "endpoint_adjustments": adjustments,
        "grade_processing": {
            key: value
            for key, value in grade_processing.items()
            if key not in ("working_distance_m", "raster_elevation_m", "processed_elevation_m")
        },
        "maximum_prepared_grade": max(grade(line) for line in parsed.values()),
        "bridge_method": "Public surface raster sampled along the OSM roadway; no bridge height inferred from terrain",
        "road_sample_spacing_m": 10,
        "warnings": ["steep_raster_grade_requires_inspection"] if max_grade > 0.25 else [],
    }
    write(folder / "heights.json", record)
    write(folder / "grade-processing.json", grade_processing)
    write(folder / "surface-cross-sections.json", surface_sections)
    write(folder / "mixed-layer-profiles.json", fused_profiles)
    write(
        folder / "height-endpoint-reconciliation.json",
        {"maximum_allowed_difference_m": 2, "adjustments": adjustments},
    )
    write(folder / "carriageway-surface-reconciliation.json", {"adjustments": cross_sections})
    write(folder / "roadway-layer-continuity.json", {"adjustments": boundary_layers})

    def ref(path):
        return {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    write(
        folder / "geometry.json",
        {
            "version": "route-energy-geometry/2.0.0",
            "network": ref(enriched),
            "mode": "raster_3d",
            "terrain": ref(folder / "heights.json"),
            "osm": ref(folder / "roads.osm.xml"),
            "origin": {
                "kind": "observed",
                "source": "Public elevation raster on OpenStreetMap road geometry",
                "reference": ref(folder / "heights.json")["sha256"],
            },
            "vertical_datum": record["vertical_datum"],
            "flat_height_m": None,
        },
    )
    print(
        folder.name,
        "elevation prepared",
        len(quality),
        "lanes",
        len(terrain.receipts),
        "raster tiles",
        "maximum sampled grade",
        round(max_grade, 3),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--raster-manifest", type=Path)
    args = parser.parse_args()
    prepare(args.folder.resolve(), args.raster_manifest)
