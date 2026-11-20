"""Prepare an OSM journey, observed elevation and mapped passenger stops."""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "scripts/route_aware_energy")]
from heights import prepare as prepare_heights  # noqa: E402
from prepare_osm_network import projected_anchors  # noqa: E402

from evdt.energy.route_aware.geometry import horizontal_length  # noqa: E402
from evdt.energy.route_aware.sumo_routes import expand_route, read_network  # noqa: E402

IMAGE = "sha256:4334958c68c76796c79099bffdb48e5c9ee422b65856a17db7c9e6bdd4616b90"


def source_fingerprints():
    paths = [ROOT / "scripts/geographic/prepare_osm_network.py"]
    paths += [
        ROOT / "scripts/geographic" / name
        for name in (
            "prepare.py",
            "heights.py",
            "road_profile.py",
            "grade_profile.py",
            "raster_catalog.py",
        )
    ]
    paths += [
        ROOT / "scripts/route_aware_energy" / name for name in ("prepare_route.py", "terrain.py")
    ]
    paths += list((ROOT / "src/evdt/energy/route_aware").glob("*.py"))
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def container(folder, stage):
    cmd = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--memory=512m",
        "--memory-swap=512m",
        "--cpus=1",
        "--pids-limit=64",
        "--tmpfs=/tmp:size=32m",
        "--entrypoint=python",
        "--volume",
        f"{folder}:/result",
        "--volume",
        f"{ROOT}/scripts/geographic/prepare_osm_network.py:/import.py:ro",
        "--volume",
        f"{ROOT}/src:/src:ro",
        IMAGE,
        "/import.py",
        "/result",
        stage,
    ]
    with (folder / (stage + "-preparation.log")).open("xb") as log:
        subprocess.run(cmd, stdout=log, stderr=log, check=True, timeout=240)


def nearest(shape, x, y):
    travelled = 0
    candidates = []
    for a, b in zip(shape, shape[1:]):
        dx, dy = b.x_m - a.x_m, b.y_m - a.y_m
        length = math.hypot(dx, dy)
        fraction = max(0, min(1, ((x - a.x_m) * dx + (y - a.y_m) * dy) / (length * length)))
        ox, oy = x - a.x_m - fraction * dx, y - a.y_m - fraction * dy
        candidates.append(
            (math.hypot(ox, oy), travelled + fraction * length, (dy * ox - dx * oy) / length)
        )
        travelled += length
    return min(candidates)


def stops(folder):
    from rasterio.warp import transform

    mission = json.loads((folder / "mission.json").read_bytes())
    network = read_network((folder / "elevated-route.net.xml").read_bytes())
    expanded = expand_route(network, tuple(mission["route_edges"]), revision=mission["mission_id"])
    recipe = json.loads((folder / "roads.json").read_bytes())["recipe"]
    candidates = []
    for node in ET.parse(folder / "roads.osm.xml").getroot().findall("node"):
        tags = {x.get("k"): x.get("v") for x in node.findall("tag")}
        if (
            tags.get("highway") == "bus_stop"
            or tags.get("public_transport") == "platform"
            and tags.get("bus") == "yes"
        ):
            candidates.append((node, tags))
    if candidates:
        xs, ys = transform(
            "EPSG:4326",
            network.projection,
            [float(n.get("lon")) for n, _ in candidates],
            [float(n.get("lat")) for n, _ in candidates],
        )
        xy = [(x + network.offset_x_m, y + network.offset_y_m) for x, y in zip(xs, ys)]
    else:
        xy = []
    selected = []
    seen = set()
    distance = 0
    previous = -100000
    for index, occurrence_index in enumerate(expanded.external_occurrences):
        road = expanded.context.occurrences[occurrence_index]
        length = horizontal_length(road.shape)
        found = []
        if road.geometry_basis == "lane_centerline":
            for (node, tags), (x, y) in zip(candidates, xy):
                separation, offset, side = nearest(road.shape, x, y)
                if separation <= 25 and side >= -2 and 15 < offset < length - 5:
                    found.append((offset, separation, node, tags))
        for offset, separation, node, tags in sorted(found, key=lambda v: v[:2]):
            if node.get("id") in seen or distance + offset - previous < recipe.get(
                "stop_spacing_m", 450
            ):
                continue
            selected.append(
                {
                    "stop_id": "osm-" + node.get("id"),
                    "external_index": index,
                    "lane_id": road.lane_id,
                    "offset_m": offset / length * road.reference_length_m,
                    "dwell_working_s": recipe.get("dwell_s", 20),
                    "name": tags.get("name", tags.get("ref", "Mapped bus stop")),
                    "osm_node": node.get("id"),
                    "longitude": float(node.get("lon")),
                    "latitude": float(node.get("lat")),
                    "snap_distance_m": separation,
                }
            )
            previous = distance + offset
            seen.add(node.get("id"))
        distance += length
    final = expanded.context.occurrences[expanded.external_occurrences[-1]]
    write(
        folder / "stops.json",
        {
            "stops": selected,
            "endpoint": {
                "lane_id": final.lane_id,
                "external_index": len(mission["route_edges"]) - 1,
                "offset_m": final.reference_length_m - 0.1,
            },
            "policy": "Actual mapped platforms on the right of travel, within 25 m; declared dwell and minimum stop spacing",
            "origin": "OpenStreetMap contributors",
        },
    )
    print(folder.name, "mapped passenger stops:", len(selected), flush=True)


def prepare(folder, raster_manifest=None):
    source = source_fingerprints()
    receipt = folder / "preparation-source.json"
    if receipt.exists():
        if json.loads(receipt.read_bytes()) != source:
            raise ValueError(
                "Preparation source changed; preserve this attempt and use a new folder"
            )
    else:
        write(receipt, source)
    if not (folder / "imported.net.xml").exists():
        container(folder, "import")
    if not (folder / "projected-anchors.json").exists():
        recipe = json.loads((folder / "roads.json").read_bytes())["recipe"]
        network = folder / "imported.net.xml"
        write(
            folder / "projected-anchors.json",
            {
                "network_sha256": hashlib.sha256(network.read_bytes()).hexdigest(),
                "xy": projected_anchors(network, recipe["anchors_lon_lat"]),
            },
        )
    if not (folder / "mission.json").exists():
        container(folder, "route")
    if not (folder / "heights.json").exists():
        prepare_heights(folder, raster_manifest)
    if not (folder / "stops.json").exists():
        stops(folder)
    if source != source_fingerprints():
        raise ValueError("Preparation source changed during the operation")
    if not (folder / "preparation.json").exists():
        write(
            folder / "preparation.json",
            {
                "source_sha256": source,
                "files": {
                    name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                    for name in (
                        "roads.osm.xml",
                        "roads.json",
                        "mission.json",
                        "stops.json",
                        "heights.json",
                        "geometry.json",
                        "elevated-route.net.xml",
                        "elevated-plant.net.xml",
                    )
                },
                "completed": True,
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--osm", type=Path, help="Local OSM XML export; copied with a fingerprint")
    parser.add_argument(
        "--journey", type=Path, help="Named destination and longitude/latitude anchors"
    )
    parser.add_argument("--raster-manifest", type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    if not folder.is_relative_to(ROOT / "evidence/energy-v2"):
        parser.error("Use project evidence/energy-v2 storage")
    if args.osm:
        if not args.journey:
            parser.error("--journey is required with --osm")
        folder.mkdir(parents=True, exist_ok=False)
        if args.osm.stat().st_size > 32 * 1024**2:
            parser.error("Select a bounded OSM corridor of at most 32 MiB")
        shutil.copyfile(args.osm, folder / "roads.osm.xml")
        write(
            folder / "roads.json",
            {
                "recipe": json.loads(args.journey.read_bytes()),
                "roads_sha256": hashlib.sha256(args.osm.read_bytes()).hexdigest(),
                "license": "ODbL-1.0",
                "attribution": "OpenStreetMap contributors",
            },
        )
    prepare(folder, args.raster_manifest)


if __name__ == "__main__":
    main()
