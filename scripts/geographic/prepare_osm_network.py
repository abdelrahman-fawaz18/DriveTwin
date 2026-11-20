"""Import and route an OSM road subset with the project's pinned SUMO tools."""

import argparse
import hashlib
import heapq
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def top_level_elements(path):
    """Stream direct XML children without retaining a whole regional network."""
    parser = ET.iterparse(path, events=("start", "end"))
    _, root = next(parser)
    depth = 1
    for event, element in parser:
        if event == "start":
            depth += 1
        else:
            depth -= 1
            if depth == 1:
                yield element
                root.remove(element)


def find_route(network, anchors, lane_network=None):
    """Snap projected anchors to nearby bus-legal roads and join SUMO connections."""
    if lane_network is not None:
        return find_lane_route(network, lane_network, anchors)
    paths = {}
    for ordinal, (x, y) in enumerate(anchors):
        neighbors = sorted(
            (
                (dist, edge.getID(), edge)
                for edge, dist in network.getNeighboringEdges(x, y, 200)
                if edge.allows("bus") and edge.getFunction() == ""
            ),
            key=lambda entry: entry[:2],
        )[:6]
        if not neighbors:
            raise ValueError(f"No bus road within 200 m of route anchor {ordinal + 1}")
        next_paths = {}
        for dist, edge_id, edge in neighbors:
            if not paths and ordinal == 0:
                next_paths[edge_id] = (dist * 5, (edge,), (dist,))
                continue
            for score, old_path, snaps in paths.values():
                route, length = network.getShortestPath(old_path[-1], edge, vClass="bus")
                if not route:
                    continue
                candidate = (score + length + dist * 5, (*old_path, *route[1:]), (*snaps, dist))
                if edge_id not in next_paths or candidate[0] < next_paths[edge_id][0]:
                    next_paths[edge_id] = candidate
        if not next_paths:
            raise ValueError(f"No directed bus route reaches anchor {ordinal + 1}")
        paths = next_paths
    _, path, snaps = min(paths.values(), key=lambda candidate: candidate[0])
    if len(path) < 2:
        raise ValueError("Route anchors selected fewer than two directed roads")
    return path, snaps


def find_lane_route(network, lane_network, anchors):
    """Shortest path through legal lane connections, including upstream lane choice."""
    from evdt.energy.route_aware.sumo_routes import _lane_change

    lanes = lane_network.lanes
    by_edge = {}
    for lane in lanes.values():
        if not lane.internal:
            by_edge.setdefault(lane.edge_id, []).append(lane)
    adjacency = {}

    def successors(identity):
        if identity not in adjacency:
            entry = lanes[identity]
            exits = [entry] + [lane for lane in by_edge[entry.edge_id] if _lane_change(entry, lane)]
            found = []
            for exit_lane in exits:
                pending = [(exit_lane.lane_id, 0.0, frozenset())]
                while pending:
                    current, cost, visited = pending.pop()
                    if len(visited) > 32:
                        raise ValueError("Junction connection exceeds its budget")
                    for dest in lane_network.connections.get(current, ()):
                        other = lanes[dest]
                        if dest in visited:
                            continue
                        if other.internal:
                            pending.append((dest, cost + other.length_m, visited | {dest}))
                        else:
                            found.append(
                                (dest, entry.length_m + cost + (0.01 if exit_lane != entry else 0))
                            )
            adjacency[identity] = found
        return adjacency[identity]

    candidates = []
    for ordinal, (x, y) in enumerate(anchors):

        def separation(edge_lanes):
            best = float("inf")
            for lane in edge_lanes:
                for a, b in zip(lane.shape, lane.shape[1:]):
                    dx, dy = b.x_m - a.x_m, b.y_m - a.y_m
                    denominator = dx * dx + dy * dy
                    fraction = (
                        max(0, min(1, ((x - a.x_m) * dx + (y - a.y_m) * dy) / denominator))
                        if denominator
                        else 0
                    )
                    best = min(
                        best, math.hypot(x - a.x_m - fraction * dx, y - a.y_m - fraction * dy)
                    )
            return best

        nearby = sorted(
            (distance, edge)
            for edge, edge_lanes in by_edge.items()
            if (ordinal or min(lane.length_m for lane in edge_lanes) >= 15)
            and (distance := separation(edge_lanes)) <= 200
        )[:6]
        if not nearby:
            raise ValueError(f"No bus road within 200 m of anchor {ordinal + 1}")
        candidates.append({lane.lane_id: d for d, edge in nearby for lane in by_edge[edge]})
    frontier = {
        lane: (distance * 5, (lane,), (distance,)) for lane, distance in candidates[0].items()
    }
    for targets in candidates[1:]:
        queue = [(value[0], lane) for lane, value in frontier.items()]
        heapq.heapify(queue)
        best = {lane: value[0] for lane, value in frontier.items()}
        predecessors = {lane: None for lane in frontier}
        origins = {lane: lane for lane in frontier}
        settled = set()
        found = {}
        while queue and len(found) < len(targets):
            score, lane = heapq.heappop(queue)
            if lane in settled or score != best[lane]:
                continue
            settled.add(lane)
            if lane in targets:
                chain = []
                current = lane
                while predecessors[current] is not None:
                    chain.append(current)
                    current = predecessors[current]
                _, prefix, snaps = frontier[origins[lane]]
                path = (*prefix, *reversed(chain))
                found[lane] = (score + targets[lane] * 5, path, (*snaps, targets[lane]))
            for target, length in successors(lane):
                value = score + length
                if target not in best or value < best[target]:
                    best[target] = value
                    predecessors[target] = lane
                    origins[target] = origins[lane]
                    heapq.heappush(queue, (value, target))
        if not found:
            raise ValueError("No legal lane-connected bus route reaches the next anchor")
        frontier = found
    _, path, snaps = min(frontier.values())
    return tuple(lanes[lane] for lane in path), snaps


def forecast_subset(imported, edge_ids, destination):
    """Copy original lane geometry/connections without asking netconvert to rebuild them."""
    tree = ET.parse(imported)
    original = tree.getroot()
    keep = set(edge_ids)
    connections = original.findall("connection")
    for _ in range(32):
        before = len(keep)
        for c in connections:
            if c.get("from") in keep and c.get("to") in keep and c.get("via"):
                keep.add(c.get("via").rsplit("_", 1)[0])
        if len(keep) == before:
            break
    result = ET.Element("net", version=original.get("version", "1.20"))
    signals = {c.get("tl") for c in connections if c.get("from") in keep and c.get("to") in keep}
    for node in original:
        if (
            node.tag == "location"
            or node.tag == "tlLogic"
            and node.get("id") in signals
            or node.tag == "edge"
            and node.get("id") in keep
            or node.tag == "connection"
            and node.get("from") in keep
            and node.get("to") in keep
        ):
            result.append(node)
    ET.ElementTree(result).write(destination, encoding="utf-8", xml_declaration=True)


def projected_anchors(network, anchors):
    from rasterio.warp import transform

    with network.open("rb") as stream:
        location = next(
            node for _, node in ET.iterparse(stream, events=("start",)) if node.tag == "location"
        )
    projection = location.attrib["projParameter"]
    dx, dy = map(float, location.attrib["netOffset"].split(","))
    longitude, latitude = zip(*anchors, strict=True)
    x, y = transform("EPSG:4326", projection, longitude, latitude)
    return [[east + dx, north + dy] for east, north in zip(x, y, strict=True)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("stage", choices=("import", "project", "route"))
    parser.add_argument("--imported-network", type=Path)
    args = parser.parse_args()
    folder = args.folder
    inputs = json.loads((folder / "roads.json").read_bytes())
    osm_path = folder / "roads.osm.xml"
    if hashlib.sha256(osm_path.read_bytes()).hexdigest() != inputs["roads_sha256"]:
        raise ValueError("Road subset fingerprint differs from its receipt")
    imported = args.imported_network or (folder / "imported.net.xml")
    binary = Path(sys.executable).parent
    recipe = inputs["recipe"]
    if args.stage == "import":
        if imported.exists():
            raise ValueError("Network import output already exists")
        unmerged = folder / "unmerged.net.xml"
        command = [
            str(binary / "netconvert"),
            "--osm-files",
            str(osm_path),
            "--output-file",
            str(unmerged),
            "--keep-edges.by-vclass",
            "bus",
            "--geometry.remove",
            "false",
            "--junctions.join",
            "true",
            "--ramps.guess",
            "true",
            "--tls.guess-signals",
            "true",
            "--tls.discard-simple",
            "true",
            "--tls.join",
            "true",
            "--no-turnarounds",
            "true",
        ]
        with (folder / "netconvert.log").open("xb") as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=90)
            # Preserve OSM structure boundaries while simplifying ordinary roads.
            # Otherwise a bridge can inherit the identity of its ground approach.
            structures = set()
            for element in top_level_elements(osm_path):
                if element.tag == "way":
                    tags = {t.get("k"): t.get("v") for t in element.findall("tag")}
                    if any(
                        tags.get(key, "no") not in ("no", "false", "0")
                        for key in ("bridge", "tunnel")
                    ):
                        structures.add(element.get("id"))
            protected = []
            for element in top_level_elements(unmerged):
                if element.tag == "edge":
                    identity = element.get("id", "")
                    if identity.lstrip("-").split("#")[0] in structures:
                        protected.append(identity)
            keep = folder / "preserved-structures.txt"
            keep.write_text("\n".join(protected) + "\n", encoding="utf-8")
            simplify = [
                str(binary / "netconvert"),
                "--sumo-net-file",
                str(unmerged),
                "--output-file",
                str(imported),
                "--geometry.remove",
                "true",
            ]
            if protected:
                simplify += ["--geometry.remove.keep-edges.input-file", str(keep)]
            subprocess.run(simplify, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=90)
        return
    digest = hashlib.sha256(imported.read_bytes()).hexdigest()
    anchor_path = folder / "projected-anchors.json"
    if args.stage == "project":
        with anchor_path.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "network_sha256": digest,
                    "xy": projected_anchors(imported, recipe["anchors_lon_lat"]),
                },
                stream,
            )
        return

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
    from evdt.energy.route_aware.sumo_routes import expand_route, read_network

    anchor_data = json.loads(anchor_path.read_bytes())
    if anchor_data["network_sha256"] != digest:
        raise ValueError("Projected anchors do not match the imported network")
    network_path = folder / "route.net.xml"
    if network_path.exists():
        raise ValueError("Route network output already exists")
    lane_network = read_network(imported.read_bytes(), max_bytes=64 * 1024 * 1024)
    path, snaps = find_lane_route(None, lane_network, anchor_data["xy"])
    edges = [lane.edge_id for lane in path]
    length = sum(lane.length_m for lane in path)
    del path, lane_network
    selection = folder / "route-edges.txt"
    with selection.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(dict.fromkeys(edges)) + "\n")
    forecast_subset(imported, edges, network_path)
    expand_route(read_network(network_path.read_bytes()), tuple(edges), revision=recipe["id"])
    record = {
        "mission_id": recipe["id"],
        "network_sha256": hashlib.sha256(network_path.read_bytes()).hexdigest(),
        "imported_network_sha256": digest,
        "routing": "legal lane graph; original lane connections preserved",
        "forecast_network_use": "geometry projection only; SUMO runs imported.net.xml",
        "route_edges": edges,
        "route_length_m": length,
        "anchor_snaps_m": snaps,
        "netconvert": subprocess.check_output(
            [str(binary / "netconvert"), "--version"], text=True, timeout=10
        ).splitlines()[0],
    }
    (folder / "mission.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"{recipe['id']}: {len(edges)} directed roads, {record['route_length_m'] / 1000:.2f} km")


if __name__ == "__main__":
    main()
