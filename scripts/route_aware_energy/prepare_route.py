"""Prepare a connected, terrain-enriched route from current static SUMO inputs."""

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import rasterio
from rasterio.warp import transform

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evdt.energy.route_aware.contracts import Origin, PlannedStop, RoadPoint  # noqa: E402
from evdt.energy.route_aware.elevation import ElevationGrid, ElevationMetadata, densify  # noqa: E402
from evdt.energy.route_aware.sumo_routes import expand_route, read_network  # noqa: E402
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint  # noqa: E402


def load_grid(manifest_path: Path) -> ElevationGrid:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = (ROOT / manifest["file"]).resolve()
    if not path.is_relative_to(ROOT / "data" / "downloads" / "energy-v2"):
        raise ValueError("Terrain raster must stay in the project terrain cache")
    if path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("Terrain raster exceeds its file budget")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest["sha256"]:
        raise ValueError("Terrain raster fingerprint differs from its manifest")
    with rasterio.open(path) as dataset:
        if dataset.count != 1 or dataset.width * dataset.height > 1_000_000:
            raise ValueError("Terrain raster dimensions exceed the grid budget")
        if dataset.transform.b or dataset.transform.d:
            raise ValueError("Rotated terrain grid needs explicit resampling")
        if dataset.crs.to_string() != manifest["horizontal_crs"]:
            raise ValueError("Terrain coordinate system differs from its manifest")
        values = dataset.read(1, masked=True)
        # A masked array can have a scalar false mask when all cells are valid.
        import numpy as np

        mask = np.ma.getmaskarray(values)
        flattened = tuple(
            None if missing else float(value)
            for value, missing in zip(values.data.flat, mask.flat, strict=True)
        )
        date = (
            datetime.fromtimestamp(manifest["downloaded_real_life_unix_s"], UTC).date().isoformat()
        )
        metadata = ElevationMetadata(
            manifest.get("dataset_id", "NRCan-HRDEM-clip"),
            manifest.get("source_url", "https://datacube.services.geo.ca/ows/elevation"),
            digest,
            manifest["horizontal_crs"],
            manifest["vertical_datum"],
            max(dataset.res),
            date,
            manifest["license"],
            "observed",
            "retrieval",
        )
        x, y = dataset.transform * (0.5, 0.5)
        return ElevationGrid(
            metadata,
            dataset.width,
            dataset.height,
            x,
            y,
            dataset.transform.a,
            dataset.transform.e,
            flattened,
        )


def structures_from_osm(
    path: Path, edge_ids: tuple[str, ...], *, max_bytes: int = 16 * 1024 * 1024
) -> dict[str, str]:
    raw = path.read_bytes()
    if (
        not 1 <= max_bytes <= 32 * 1024 * 1024
        or len(raw) > max_bytes
        or b"<!DOCTYPE" in raw
        or b"<!ENTITY" in raw
    ):
        raise ValueError("OSM input exceeds the supported local import budget")
    ways = {}
    from evdt.energy.route_aware.sumo_routes import _elements

    for way in _elements(raw, "way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if tags.get("bridge", "no") not in ("no", "false", "0"):
            ways[way.attrib["id"]] = "bridge"
        if tags.get("tunnel", "no") not in ("no", "false", "0"):
            ways[way.attrib["id"]] = "tunnel"
    return {edge: ways.get(edge.lstrip("-").split("#")[0], "surface") for edge in edge_ids}


def prepare(
    network_path: Path, mission_path: Path, osm_path: Path, terrain_manifest: Path, output: Path
) -> dict:
    network = read_network(network_path.read_bytes())
    mission = json.loads(mission_path.read_text(encoding="utf-8"))
    edges = tuple(mission["route_edges"])
    stops, lanes = {}, {}
    if "stops" in mission:
        for stop in mission["stops"]:
            index = stop["external_occurrence_index"]
            stops[index] = (
                *stops.get(index, ()),
                PlannedStop(stop["stop_id"], stop["offset_m"], stop["dwell_working_s"]),
            )
            lanes[index] = stop["lane_id"]
    elif "stop" in mission:
        stop = mission["stop"]
        edge = network.lanes[stop["lane"]].edge_id
        # The legacy mission explicitly schedules its single stop on the first visit.
        index = edges.index(edge)
        stops[index] = (PlannedStop("mission-stop-1", stop["end_pos_m"], stop["duration_s"]),)
        lanes[index] = stop["lane"]
    structures = structures_from_osm(osm_path, edges)
    expanded = expand_route(
        network,
        edges,
        revision=mission["mission_id"],
        stops_by_external_index=stops,
        required_lanes=lanes,
        structures=structures,
    )
    grid = load_grid(terrain_manifest)
    if network.projection in ("!", "-", "."):
        raise ValueError("Network lacks a supported geographic projection")
    occurrences = []
    for occurrence in expanded.context.occurrences:
        if occurrence.structure != "surface":
            raise ValueError("Structure needs a separately verified roadway-height profile")
        points = densify(occurrence.shape, max(20, grid.metadata.nominal_resolution_m))
        x = [p.x_m - network.offset_x_m for p in points]
        y = [p.y_m - network.offset_y_m for p in points]
        east, north = transform(network.projection, grid.metadata.horizontal_frame, x, y)
        longitude, latitude = transform(network.projection, "EPSG:4326", x, y)
        shape = tuple(
            RoadPoint(p.x_m, p.y_m, grid.sample(e, n), lat, lon)
            for p, e, n, lat, lon in zip(points, east, north, latitude, longitude, strict=True)
        )
        quality = "missing" if any(p.elevation_m is None for p in shape) else "terrain"
        occurrences.append(replace(occurrence, shape=shape, elevation_quality=quality))
    context = replace(
        expanded.context,
        vertical_datum=grid.metadata.vertical_datum,
        elevation_origin=Origin("observed", grid.metadata.dataset_id, grid.metadata.content_sha256),
        occurrences=tuple(occurrences),
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "route.json").write_bytes(canonical_bytes(context) + b"\n")
    report = {
        "route_sha256": fingerprint(context),
        "network_sha256": network.sha256,
        "mission_sha256": hashlib.sha256(mission_path.read_bytes()).hexdigest(),
        "osm_sha256": hashlib.sha256(osm_path.read_bytes()).hexdigest(),
        "terrain_sha256": grid.metadata.content_sha256,
        "projection": network.projection,
        "network_offset_m": [network.offset_x_m, network.offset_y_m],
        "external_occurrences": expanded.external_occurrences,
        "endpoint_snaps": expanded.endpoint_snaps,
        "assumed_lane_changes": sum(
            o.geometry_basis == "assumed_lane_change" for o in context.occurrences
        ),
        "occurrences": len(context.occurrences),
        "vertices": sum(len(o.shape) for o in context.occurrences),
        "missing_height_occurrences": sum(
            o.elevation_quality == "missing" for o in context.occurrences
        ),
        "structure_policy": "OSM bridge/tunnel flags require separate road height",
        "heading_policy": "geographic bearing between densified points",
        "terrain_role": "static road-context enrichment only; SUMO plant height is unchanged",
        "source_acquisition_date": None,
    }
    (output / "preparation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("network", "mission", "osm", "terrain-manifest", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.network, args.mission, args.osm, args.terrain_manifest, args.output),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
