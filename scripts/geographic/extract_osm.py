"""Prepare bounded OSM road subsets from local PBF extracts."""

import argparse
import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import osmium

MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}
DRIVABLE = MAJOR | {"unclassified", "residential", "service", "living_street", "busway"}
ROAD_TAGS = {
    "access",
    "bridge",
    "bus",
    "highway",
    "junction",
    "lanes",
    "lanes:backward",
    "lanes:forward",
    "layer",
    "maxspeed",
    "maxspeed:backward",
    "maxspeed:forward",
    "motor_vehicle",
    "motorcar",
    "name",
    "oneway",
    "oneway:bus",
    "psv",
    "ref",
    "service",
    "surface",
    "smoothness",
    "construction",
    "tunnel",
    "vehicle",
    "width",
}


def add_tags(element, pairs):
    for key, value in sorted(pairs):
        ET.SubElement(element, "tag", k=key, v=value)


def point_inside(lon, lat, bounds):
    west, south, east, north = bounds
    return west <= lon <= east and south <= lat <= north


class Clip(osmium.SimpleHandler):
    def __init__(self, recipe):
        super().__init__()
        self.recipe = recipe
        self.nodes = {}
        self.ways = {}
        self.restrictions = []
        self.node_tags = {}
        self.stops = {}

    def node(self, node):
        if not node.location.valid():
            return
        if not point_inside(node.lon, node.lat, self.recipe["bounds"]):
            return
        tags = [
            (t.k, t.v)
            for t in node.tags
            if t.k in {"highway", "public_transport", "bus", "name", "ref", "ele", "crossing"}
        ]
        if tags:
            self.node_tags[node.id] = tags
        if (
            node.tags.get("highway") == "bus_stop"
            or node.tags.get("public_transport") == "platform"
            and node.tags.get("bus") == "yes"
        ):
            self.stops[node.id] = (node.lon, node.lat)

    def way(self, way):
        highway = way.tags.get("highway", "").removesuffix("_link")
        if highway not in DRIVABLE or any(not ref.location.valid() for ref in way.nodes):
            return
        nodes = [(ref.ref, ref.lon, ref.lat) for ref in way.nodes]
        if not any(point_inside(lon, lat, self.recipe["bounds"]) for _, lon, lat in nodes):
            return
        if self.recipe.get("corridor_m"):
            anchors = self.recipe["anchors_lon_lat"]
            reference = math.radians(sum(p[1] for p in anchors) / len(anchors))

            def metric(point):
                return (point[0] * 111320 * math.cos(reference), point[1] * 110540)

            line = [metric(p) for p in anchors]

            def distance(lon, lat):
                x, y = metric((lon, lat))
                best = float("inf")
                for (ax, ay), (bx, by) in zip(line, line[1:]):
                    dx, dy = bx - ax, by - ay
                    den = dx * dx + dy * dy
                    t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / den)) if den else 0
                    best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
                return best

            if not any(distance(lon, lat) <= self.recipe["corridor_m"] for _, lon, lat in nodes):
                return
        if self.recipe.get("major_roads_only") and highway not in MAJOR:
            if not any(
                abs(lon - x) < 0.012 and abs(lat - y) < 0.009
                for _, lon, lat in nodes
                for x, y in self.recipe["anchors_lon_lat"]
            ):
                return
        self.nodes.update((ref, (lon, lat)) for ref, lon, lat in nodes)
        self.ways[way.id] = (
            [ref for ref, _, _ in nodes],
            [(tag.k, tag.v) for tag in way.tags if tag.k in ROAD_TAGS],
        )

    def relation(self, relation):
        if relation.tags.get("type") != "restriction":
            return
        members = [(m.type, m.ref, m.role) for m in relation.members]
        if any(kind == "w" and ref not in self.ways for kind, ref, _ in members):
            return
        if any(kind == "n" and ref not in self.nodes for kind, ref, _ in members):
            return
        tags = [
            (tag.k, tag.v)
            for tag in relation.tags
            if tag.k in {"type", "except", "restriction"} or tag.k.startswith("restriction:")
        ]
        self.restrictions.append((relation.id, members, tags))

    def write(self, path):
        if not self.ways:
            raise ValueError("No drivable OSM roads found in the requested bounds")
        root = ET.Element("osm", version="0.6", generator="DriveTwin OSM road subset")
        for ref, (lon, lat) in sorted({**self.stops, **self.nodes}.items()):
            element = ET.SubElement(root, "node", id=str(ref), lon=str(lon), lat=str(lat))
            add_tags(element, self.node_tags.get(ref, ()))
        for ref, (nodes, tags) in sorted(self.ways.items()):
            element = ET.SubElement(root, "way", id=str(ref))
            for node in nodes:
                ET.SubElement(element, "nd", ref=str(node))
            add_tags(element, tags)
        for ref, members, tags in sorted(self.restrictions):
            element = ET.SubElement(root, "relation", id=str(ref))
            for kind, member, role in members:
                ET.SubElement(
                    element,
                    "member",
                    type={"n": "node", "w": "way"}[kind],
                    ref=str(member),
                    role=role,
                )
            add_tags(element, tags)
        with path.open("xb") as out:
            ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, required=True)
    parser.add_argument("--recipes", type=Path, required=True)
    parser.add_argument("--area", required=True)
    parser.add_argument("--route", help="Select a single route recipe")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    clips = [
        Clip(row)
        for row in recipes["routes"]
        if row["extract"] == args.area and (not args.route or row["id"] == args.route)
    ]
    if not clips:
        parser.error("No routes select this extract")
    reader = osmium.FileProcessor(str(args.extract)).with_locations("flex_mem")
    for item in reader:
        if item.is_node():
            for clip in clips:
                clip.node(item)
        elif item.is_way():
            for clip in clips:
                clip.way(item)
        elif item.is_relation() and all(member.type in {"w", "n"} for member in item.members):
            for clip in clips:
                clip.relation(item)
    with args.extract.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    for clip in clips:
        directory = args.output / clip.recipe["id"]
        directory.mkdir(parents=True, exist_ok=False)
        road_file = directory / "roads.osm.xml"
        clip.write(road_file)
        summary = {
            "recipe": clip.recipe,
            "extract_sha256": digest,
            "roads_sha256": hashlib.sha256(road_file.read_bytes()).hexdigest(),
            "road_ways": len(clip.ways),
            "turn_restrictions": len(clip.restrictions),
            "license": "ODbL-1.0",
            "attribution": "OpenStreetMap contributors, extract by BBBike",
        }
        (directory / "roads.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{directory.name}: {len(clip.ways)} OSM ways", flush=True)


if __name__ == "__main__":
    main()
