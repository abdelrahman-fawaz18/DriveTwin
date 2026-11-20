"""Trusted, cached static road preparation outside the numerical worker."""

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from evdt.energy.route_aware.contracts import Origin, RoadPoint
from evdt.energy.route_aware.elevation import densify
from evdt.energy.route_aware.preparation import prepare_plan
from evdt.energy.route_aware.sumo_routes import read_network
from evdt.energy.route_aware.wire import decode_record

ROOT = Path(__file__).resolve().parents[2]


def checked_file(reference):
    if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
        raise ValueError("Static input needs its relative path and SHA-256")
    path = (ROOT / reference["path"]).resolve()
    if not any(
        path.is_relative_to(ROOT / folder)
        for folder in ("data", "simulation", "evidence", ".runtime")
    ):
        raise ValueError("Static geometry input must stay in project data/runtime storage")
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Static geometry input exceeds its file budget")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("Static geometry fingerprint mismatch")
    return path, raw


class GeometryProvider:
    def __init__(self, bundle_path, *, include_yellow=False):
        raw = Path(bundle_path).read_bytes()
        if len(raw) > 65_536:
            raise ValueError("Geometry bundle manifest exceeds its budget")
        self.bundle_sha256 = hashlib.sha256(raw).hexdigest()
        bundle = json.loads(raw)
        if (
            set(bundle)
            != {
                "version",
                "network",
                "mode",
                "terrain",
                "osm",
                "origin",
                "vertical_datum",
                "flat_height_m",
            }
            or bundle["version"] != "route-energy-geometry/2.0.0"
        ):
            raise ValueError("Unsupported geometry bundle manifest")
        _, network_raw = checked_file(bundle["network"])
        self.network = read_network(network_raw, include_yellow=include_yellow)
        self.origin = decode_record(Origin, bundle["origin"])
        self.datum = bundle["vertical_datum"]
        self.mode = bundle["mode"]
        self.flat_height = bundle["flat_height_m"]
        self.cache = {}
        self.structures = {}
        self.grid = None
        self.road_shapes = {}
        if bundle["osm"] is not None:
            from prepare_route import structures_from_osm

            osm_path, _ = checked_file(bundle["osm"])
            self.structures = structures_from_osm(
                osm_path,
                {lane.edge_id for lane in self.network.lanes.values()},
                max_bytes=32 * 1024 * 1024,
            )
        if self.mode == "terrain":
            from prepare_route import load_grid

            manifest_path, _ = checked_file(bundle["terrain"])
            self.grid = load_grid(manifest_path)
            if self.network.projection in ("!", "-", ".") or bundle["osm"] is None:
                raise ValueError("Terrain needs geographic projection and structure classification")
            if (
                self.origin.reference != self.grid.metadata.content_sha256
                or self.datum != self.grid.metadata.vertical_datum
            ):
                raise ValueError("Terrain provenance or vertical datum mismatch")
        elif self.mode == "declared_flat":
            from evdt.energy.route_aware.validation import number

            number(self.flat_height, "declared flat height", -1000, 10_000)
            if self.origin.kind not in ("declared", "synthetic"):
                raise ValueError("Flat geometry must carry an explicit assumption")
        elif self.mode in ("network_3d", "raster_3d"):
            if self.mode == "network_3d" and self.origin.kind != "synthetic":
                raise ValueError(
                    "Network 3D adapter currently supports declared synthetic geometry"
                )
            for lane in ET.fromstring(network_raw).iter("lane"):
                if lane.attrib["id"] not in self.network.lanes:
                    continue
                coordinates = tuple(
                    tuple(float(v) for v in point.split(","))
                    for point in lane.attrib["shape"].split()
                )
                parsed_lane = self.network.lanes[lane.attrib["id"]]
                if parsed_lane.internal and len(parsed_lane.shape) < 2:
                    # A nominal zero-length link is skipped by route expansion.
                    continue
                if any(len(point) != 3 for point in coordinates):
                    raise ValueError("Every roadway vertex needs an explicit height")
                self.road_shapes[lane.attrib["id"]] = coordinates
            if self.mode == "raster_3d":
                _, height_raw = checked_file(bundle["terrain"])
                self.height_receipt = json.loads(height_raw)
                if (
                    self.height_receipt["network_sha256"] != self.network.sha256
                    or self.height_receipt["vertical_datum"] != self.datum
                    or self.origin.reference != hashlib.sha256(height_raw).hexdigest()
                    or bundle["osm"] is None
                ):
                    raise ValueError("Road elevation receipt does not bind this network")
        else:
            raise ValueError("Unsupported geometry height source")

    def enrich(self, road):
        if self.mode == "terrain":
            from rasterio.warp import transform

            if road.structure != "surface":
                raise ValueError("Structure needs a separately verified road-height profile")
            grid = self.grid
            points = densify(road.shape, max(20, grid.metadata.nominal_resolution_m))
            x = [p.x_m - self.network.offset_x_m for p in points]
            y = [p.y_m - self.network.offset_y_m for p in points]
            east, north = transform(self.network.projection, grid.metadata.horizontal_frame, x, y)
            longitude, latitude = transform(self.network.projection, "EPSG:4326", x, y)
            shape = tuple(
                RoadPoint(p.x_m, p.y_m, grid.sample(e, n), lat, lon)
                for p, e, n, lat, lon in zip(points, east, north, latitude, longitude, strict=True)
            )
            quality = "missing" if any(p.elevation_m is None for p in shape) else "terrain"
        elif self.mode == "declared_flat":
            shape = tuple(replace(p, elevation_m=self.flat_height) for p in road.shape)
            quality = "declared_flat"
        else:
            shape = tuple(replace(p, elevation_m=self.height(road, p)) for p in road.shape)
            quality = (
                "synthetic"
                if self.mode == "network_3d"
                else self.height_receipt["lane_quality"][road.lane_id]
            )
        if self.mode != "terrain" and self.network.projection not in ("!", "-", "."):
            from rasterio.warp import transform

            longitude, latitude = transform(
                self.network.projection,
                "EPSG:4326",
                [p.x_m - self.network.offset_x_m for p in shape],
                [p.y_m - self.network.offset_y_m for p in shape],
            )
            shape = tuple(
                replace(p, latitude_deg=lat, longitude_deg=lon)
                for p, lat, lon in zip(shape, latitude, longitude, strict=True)
            )
        return replace(road, shape=shape, elevation_quality=quality)

    def height(self, road, point):
        candidates = []
        for lane in (road.lane_id, *road.alternative_lane_ids):
            line = self.road_shapes[lane]
            for a, b in zip(line, line[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                length2 = dx * dx + dy * dy
                if not length2:
                    continue
                fraction = max(
                    0, min(1, ((point.x_m - a[0]) * dx + (point.y_m - a[1]) * dy) / length2)
                )
                separation = math.hypot(
                    point.x_m - a[0] - fraction * dx, point.y_m - a[1] - fraction * dy
                )
                candidates.append((separation, a[2] + fraction * (b[2] - a[2])))
        if not candidates or min(candidates)[0] > 5.01:
            raise ValueError("Height cannot be matched to a roadway")
        return min(candidates)[1]

    def __call__(self, assignment, context, *, required_visit_lanes=None):
        if (
            assignment.geometry_bundle_sha256 != self.bundle_sha256
            or assignment.network_sha256 != self.network.sha256
        ):
            raise ValueError("Local geometry does not match the approved assignment")
        first_external = getattr(context, "external_index", 0)
        lane_id = getattr(context, "lane_id", None)
        key = (
            context.plan.revision,
            first_external,
            lane_id,
            tuple(sorted((required_visit_lanes or {}).items())),
        )
        if key not in self.cache:
            lane = self.network.lanes.get(lane_id) if lane_id is not None else None
            current_junction_lane = lane_id if lane and lane.internal else None
            current_lanes = (
                {first_external: lane_id}
                if lane_id is not None and current_junction_lane is None
                else {}
            )
            # SUMO keeps the departing road index until the bus enters the next road.
            # Pin the observed junction lane to that road's outgoing connection.
            prepared = prepare_plan(
                self.network,
                context.plan,
                enrich=self.enrich,
                vertical_datum=self.datum,
                elevation_origin=self.origin,
                structures=self.structures,
                current_lanes=current_lanes,
                current_junction_lane=current_junction_lane,
                first_external_index=first_external,
                required_visit_lanes=required_visit_lanes,
            )
            if first_external and getattr(assignment, "scenario", None) is not None:
                # Scenario road indices belong to the original assigned geometry.
                # Remap by ordered road visits; never silently move an effect to
                # an alternative junction path selected by the observed lane.
                from types import SimpleNamespace

                baseline = self(assignment, SimpleNamespace(plan=context.plan))
                old_indices = []
                for relative, new_start in enumerate(prepared.external_occurrences):
                    external = first_external + relative
                    old_start = baseline.external_occurrences[external]
                    old_end = (
                        baseline.external_occurrences[external + 1]
                        if external + 1 < len(baseline.external_occurrences)
                        else len(baseline.route.occurrences)
                    )
                    new_end = (
                        prepared.external_occurrences[relative + 1]
                        if relative + 1 < len(prepared.external_occurrences)
                        else len(prepared.route.occurrences)
                    )
                    old_indices.append(old_start)
                    old_roads = baseline.route.occurrences[old_start + 1 : old_end]
                    for road in prepared.route.occurrences[new_start + 1 : new_end]:
                        matches = [
                            o.index
                            for o in old_roads
                            if o.edge_id == road.edge_id and o.lane_id == road.lane_id
                        ]
                        old_indices.append(matches[0] if len(matches) == 1 else -1)
                prepared = replace(prepared, scenario_indices=tuple(old_indices))
            if self.mode == "raster_3d":
                occurrences = []
                for road in prepared.route.occurrences:
                    if occurrences:
                        previous = occurrences[-1].shape[-1]
                        first = road.shape[0]
                        if (previous.x_m, previous.y_m) == (first.x_m, first.y_m):
                            difference = abs(previous.elevation_m - first.elevation_m)
                            if difference > 0.02:
                                raise ValueError(
                                    "Prepared roadway elevation does not join at a lane boundary"
                                )
                            road = replace(
                                road,
                                shape=(
                                    replace(first, elevation_m=previous.elevation_m),
                                    *road.shape[1:],
                                ),
                            )
                    occurrences.append(road)
                for index, (road, following) in enumerate(zip(occurrences, occurrences[1:])):
                    exit_lane = (road.alternative_lane_ids or (road.lane_id,))[-1]
                    visibility = self.network.minor_visibility.get((exit_lane, following.lane_id))
                    if visibility is not None:
                        road = replace(road, yield_visibility_m=visibility)
                    if (exit_lane, following.lane_id) in self.network.signal_connections:
                        road = replace(
                            road,
                            signal_controlled=True,
                            fixed_signal=self.network.fixed_signals.get(
                                (exit_lane, following.lane_id)
                            ),
                        )
                    occurrences[index] = road
                prepared = replace(
                    prepared, route=replace(prepared.route, occurrences=tuple(occurrences))
                )
            if self.mode == "network_3d" and self.network.projection == "!":
                prepared = replace(
                    prepared,
                    route=replace(prepared.route, horizontal_frame="local-east-north-meters"),
                )
            if len(self.cache) >= 8:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = prepared
        return self.cache[key]
