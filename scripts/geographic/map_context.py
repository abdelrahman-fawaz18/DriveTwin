"""Extract actual OSM water, parks, and place labels for a local route atlas.

This display-only tool uses pyosmium. None of its output enters the predictor.
"""

import argparse
import hashlib
import json
from pathlib import Path

import osmium


class Context(osmium.SimpleHandler):
    def __init__(self, bounds):
        super().__init__()
        self.bounds = bounds
        self.water = []
        self.parks = []
        self.places = []

    def inside(self, points):
        w, s, e, n = self.bounds
        return (
            bool(points)
            and max(p[0] for p in points) >= w
            and min(p[0] for p in points) <= e
            and max(p[1] for p in points) >= s
            and min(p[1] for p in points) <= n
        )

    def node(self, node):
        if (
            node.tags.get("place")
            in {"city", "town", "village", "suburb", "neighbourhood", "borough"}
            and node.tags.get("name")
            and self.inside([(node.lon, node.lat)])
        ):
            self.places.append([node.tags["name"], node.lon, node.lat, node.tags["place"]])

    def area(self, area):
        water = area.tags.get("natural") == "water" or area.tags.get("waterway") == "riverbank"
        park = (
            area.tags.get("leisure") in {"park", "nature_reserve"}
            or area.tags.get("landuse") == "forest"
        )
        if not (water or park):
            return
        for outer in area.outer_rings():
            points = [(n.lon, n.lat) for n in outer if n.location.valid()]
            if len(points) < 4 or not self.inside(points):
                continue
            rings = [points]
            for inner in area.inner_rings(outer):
                rings.append([(n.lon, n.lat) for n in inner if n.location.valid()])
            (self.water if water else self.parks).append(rings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--bounds", type=float, nargs=4, required=True)
    args = parser.parse_args()
    handler = Context(args.bounds)
    handler.apply_file(str(args.pbf), locations=True, idx="flex_mem")
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "source_sha256": hashlib.sha256(args.pbf.read_bytes()).hexdigest(),
                "attribution": "OpenStreetMap contributors",
                "bounds": args.bounds,
                "water": handler.water,
                "parks": handler.parks,
                "places": handler.places,
            },
            stream,
            separators=(",", ":"),
        )
    print(
        f"Map context: {len(handler.water)} water areas, {len(handler.parks)} parks, {len(handler.places)} place labels"
    )


if __name__ == "__main__":
    main()
