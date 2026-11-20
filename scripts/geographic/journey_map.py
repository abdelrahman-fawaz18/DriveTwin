"""Project the prepared road network and named passenger stops for both maps."""

import math
import xml.etree.ElementTree as ET


def street_features(osm_path, bounds):
    west, south, east, north = bounds
    margin = max((east - west) * 0.18, (north - south) * 0.65, 0.005)
    minimum_lon, maximum_lon = west - margin, east + margin
    minimum_lat, maximum_lat = south - margin * 0.6, north + margin * 0.6
    root = ET.parse(osm_path).getroot()
    nodes = {
        node.attrib["id"]: (
            round(float(node.attrib["lon"]), 6),
            round(float(node.attrib["lat"]), 6),
        )
        for node in root.findall("node")
    }
    major = {"motorway", "trunk", "primary", "secondary", "tertiary"}
    roads = []
    for way in root.findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if "highway" not in tags:
            continue
        line = [
            nodes[node.attrib["ref"]] for node in way.findall("nd") if node.attrib["ref"] in nodes
        ]
        if len(line) < 2 or not any(
            minimum_lon <= lon <= maximum_lon and minimum_lat <= lat <= maximum_lat
            for lon, lat in line
        ):
            continue
        roads.append((way.attrib["id"], tags, line))
    # Keep major connections and named streets first in long-trip views.
    roads.sort(
        key=lambda road: (
            road[1]["highway"].removesuffix("_link") not in major,
            not road[1].get("name"),
            -len(road[2]),
            road[0],
        )
    )
    features, vertices = [], 0
    for identity, tags, line in roads:
        if len(features) >= 2600:
            break
        if vertices + len(line) > 21000:
            continue
        vertices += len(line)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": line},
                "properties": {"id": identity, "layer": "roads", "name": tags.get("name", "")},
            }
        )
    return features, vertices


def journey_features(
    network_path, routes_path, osm_path, mission, stops, title, *, station_bays=()
):
    from rasterio.warp import transform

    network = ET.parse(network_path).getroot()
    location = network.find("location")
    offset_x, offset_y = map(float, location.attrib["netOffset"].split(","))

    def shape(lane):
        return [tuple(map(float, point.split(",")[:2])) for point in lane.attrib["shape"].split()]

    def geo(points):
        lon, lat = transform(
            location.attrib["projParameter"],
            "EPSG:4326",
            [p[0] - offset_x for p in points],
            [p[1] - offset_y for p in points],
        )
        return [[round(x, 6), round(y, 6)] for x, y in zip(lon, lat, strict=True)]

    def feature(kind, identity, coordinates, layer, name, **properties):
        return {
            "type": "Feature",
            "geometry": {"type": kind, "coordinates": coordinates},
            "properties": {"id": identity, "layer": layer, "name": name, **properties},
        }

    def lane_point(lane, distance):
        points = shape(lane)
        lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        # SUMO's lane length can differ from its displayed centerline length.
        remaining = max(0, min(1, distance / float(lane.attrib["length"]))) * sum(lengths)
        for a, b, length in zip(points, points[1:], lengths):
            if length and remaining <= length:
                ratio = remaining / length
                return tuple(start + (end - start) * ratio for start, end in zip(a, b))
            remaining -= length
        return points[-1]

    edges = {
        edge.attrib["id"]: edge
        for edge in network.findall("edge")
        if edge.get("function") != "internal" and edge.find("lane") is not None
    }
    route = [geo(shape(edges[edge].find("lane"))) for edge in mission["route_edges"]]
    first = edges[mission["route_edges"][0]].find("lane")
    vehicle = ET.parse(routes_path).getroot().find("vehicle")
    departure = geo([lane_point(first, float(vehicle.attrib["departPos"]))])[0]
    endpoint = stops["endpoint"]
    lane = network.find(f".//lane[@id='{endpoint['lane_id']}']")
    destination = geo([lane_point(lane, endpoint["offset_m"])])[0]
    features = [
        feature("MultiLineString", mission["mission_id"], route, "route", title),
        feature("Point", "departure", departure, "endpoints", "Departure"),
        feature("Point", "destination", destination, "endpoints", "Destination"),
        *[
            feature(
                "Point",
                s["stop_id"],
                [s["longitude"], s["latitude"]],
                "stops",
                s["name"],
                duration_s=s["dwell_working_s"],
            )
            for s in stops["stops"]
        ],
    ]
    lanes = {lane.attrib["id"]: lane for edge in edges.values() for lane in edge.findall("lane")}
    detour_vertices = 0
    detours = [entry for entry in station_bays if entry.detour is not None]
    if detours:
        from evdt.energy.route_aware.sumo_routes import expand_route, read_network

        network_data = read_network(network_path.read_bytes())
        for entry in detours:
            detour = entry.detour
            path = expand_route(
                network_data,
                detour.edges,
                revision="0" * 64,
                required_lanes={detour.bay_edge_index: entry.bay.lane_id},
            )
            # The departure and rejoin roads also belong to the assigned route.
            lines = [
                geo([(p.x_m, p.y_m) for p in road.shape]) for road in path.context.occurrences[1:-1]
            ]
            detour_vertices += sum(map(len, lines))
            features.append(
                feature("MultiLineString", entry.bay.bay_id, lines, "detour", "Charging detour")
            )
    for entry in station_bays:
        bay = entry.bay
        lane = lanes.get(bay.lane_id)
        if lane is None or not 0 <= bay.start_offset_m < bay.end_offset_m <= float(
            lane.attrib["length"]
        ):
            raise ValueError("Assigned station differs from runtime lane geometry")
        features.append(
            feature(
                "Point",
                bay.bay_id,
                geo([lane_point(lane, (bay.start_offset_m + bay.end_offset_m) / 2)])[0],
                "stations",
                bay.bay_id,
                hypothetical=True,
                power_limit_W=bay.power_w,
                road_access="reviewed-sumo-lane",
            )
        )
    points = [p for line in route for p in line]
    bounds = [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]
    streets, vertices = street_features(osm_path, bounds)
    features.extend(streets)
    point_count = (
        vertices + detour_vertices + len(points) + len(stops["stops"]) + len(station_bays) + 2
    )
    if len(features) > 10000 or point_count > 50000:
        raise ValueError("Prepared map exceeds the dashboard geometry budget")
    return features, bounds


if __name__ == "__main__":
    import json
    import sys

    from live_inputs import local_path, read

    network, routes, osm, inputs = (local_path(name) for name in sys.argv[1:])
    data = read(inputs)
    features, bounds = journey_features(
        network, routes, osm, data["mission"], data["stops"], data["recipe"]["title"]
    )
    print(
        json.dumps(
            {"type": "FeatureCollection", "features": features, "bbox": bounds},
            separators=(",", ":"),
            allow_nan=False,
        )
    )
