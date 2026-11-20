"""Inventory mapped charging sites from a retained OpenStreetMap PBF extract."""

import argparse
import hashlib
import json
import math
from pathlib import Path

ATTRIBUTION = "OpenStreetMap contributors"
VERSION = "osm-charging-inventory/1.0.0"


def is_site(item):
    return item.tags.get("amenity") == "charging_station"


def relevant_tags(item):
    return {
        tag.k: tag.v
        for tag in item.tags
        if tag.k
        in {
            "access",
            "bicycle",
            "bus",
            "capacity",
            "fee",
            "hgv",
            "maxheight",
            "maxlength",
            "maxstay",
            "motorcar",
            "name",
            "network",
            "opening_hours",
            "operator",
            "psv",
            "ref",
            "maxweight",
        }
        or tag.k.startswith(("socket:", "charging_station:"))
    }


def region_bounds(value, region):
    recipes = [row for row in value["routes"] if row["extract"] == region]
    if not recipes:
        raise ValueError("No reviewed road bounds for this region")
    return {row["id"]: tuple(row["bounds"]) for row in recipes}


def mean_position(points):
    count = len(points)
    return tuple(math.fsum(axis) / count for axis in zip(*points, strict=True))


def collect(path, bounds):
    import osmium

    found = {}
    members = {}
    member_ways = set()

    def keep(item, kind, point=None):
        found[(kind, item.id)] = (point, relevant_tags(item), item.version)

    # The first pass uses no global location index. Keep only station geometry
    # references; the second pass resolves those nodes in the local extract.
    for item in osmium.FileProcessor(str(path)):
        if not is_site(item):
            continue
        if item.is_node() and item.location.valid():
            keep(item, "node", (item.lon, item.lat))
        elif item.is_way():
            keep(item, "way")
            members[("way", item.id)] = tuple(node.ref for node in item.nodes)
        elif item.is_relation():
            keep(item, "relation")
            refs = tuple((m.type, m.ref) for m in item.members if m.type in {"n", "w"})
            members[("relation", item.id)] = refs
            member_ways.update(ref for kind, ref in refs if kind == "w")

    way_nodes = {}
    for item in osmium.FileProcessor(str(path)).with_filter(
        osmium.filter.EntityFilter(osmium.osm.WAY)
    ):
        if item.id in member_ways:
            way_nodes[item.id] = tuple(node.ref for node in item.nodes)
    references = {}
    for identity, refs in members.items():
        if identity[0] == "way":
            references[identity] = tuple(dict.fromkeys(refs))
        else:
            if not all(kind == "n" or ref in way_nodes for kind, ref in refs):
                continue
            references[identity] = tuple(
                dict.fromkeys(
                    node
                    for kind, ref in refs
                    for node in ((ref,) if kind == "n" else way_nodes[ref])
                )
            )

    locations = {}
    wanted = set().union(*references.values())
    for item in osmium.FileProcessor(str(path)).with_filter(
        osmium.filter.EntityFilter(osmium.osm.NODE)
    ):
        if item.id in wanted and item.location.valid():
            locations[item.id] = (item.lon, item.lat)

    excluded_geometry = []
    rows = []
    for (kind, identity), (point, tags, revision) in sorted(found.items()):
        osm_id = f"{kind}/{identity}"
        basis = "mapped_node"
        if point is None:
            refs = references.get((kind, identity), ())
            if not refs or any(ref not in locations for ref in refs):
                excluded_geometry.append(osm_id)
                continue
            point = mean_position([locations[ref] for ref in refs])
            basis = "outline_vertex_mean"
        lon, lat = point
        corridors = sorted(
            name
            for name, (west, south, east, north) in bounds.items()
            if west <= lon <= east and south <= lat <= north
        )
        if corridors:
            rows.append(
                {
                    "osm_id": osm_id,
                    "osm_version": revision,
                    "longitude": lon,
                    "latitude": lat,
                    "location_basis": basis,
                    "route_bounds": corridors,
                    "tags": tags,
                }
            )
    return rows, excluded_geometry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", required=True, type=Path)
    parser.add_argument("--recipes", required=True, type=Path)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bounds = region_bounds(json.loads(args.recipes.read_bytes()), args.region)
    with args.extract.open("rb") as source:
        sha256 = hashlib.file_digest(source, "sha256").hexdigest()
    rows, unresolved = collect(args.extract, bounds)
    result = {
        "version": VERSION,
        "region": args.region,
        "attribution": ATTRIBUTION,
        "license": "ODbL-1.0",
        "extract_sha256": sha256,
        "bounds": bounds,
        "sites": rows,
        "missing_outline_geometry": unresolved,
    }
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(f"{args.region}: {len(rows)} mapped sites, {len(unresolved)} unresolved outlines")


if __name__ == "__main__":
    main()
