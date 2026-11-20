"""Build and provision whitelisted, immutable mission context without running physics."""

import argparse
import hashlib
import json
import math
import os
import socket
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from psycopg.types.json import Jsonb


def at_offset(shape, offset):
    length = sum(math.dist(a, b) for a, b in zip(shape, shape[1:]))
    if not 0 <= offset <= length + 0.1:
        raise ValueError("Stop offset outside known lane geometry")
    for a, b in zip(shape, shape[1:]):
        segment = math.dist(a, b)
        if segment and offset <= segment:
            return tuple(a[i] + (b[i] - a[i]) * offset / segment for i in (0, 1))
        offset -= segment
    return shape[-1]


def feature(kind, identity, coordinates, **properties):
    return {
        "type": "Feature",
        "geometry": {"type": kind, "coordinates": coordinates},
        "properties": {"id": identity, **properties},
    }


def build(scenario, osm, run, asset, work):
    import sumo
    import traci

    from evdt.monitoring.network import build_network

    net = ET.parse(build_network(scenario, osm, work)).getroot()
    mission = json.loads((scenario / "mission.json").read_text())
    stations = json.loads((scenario / "stations.json").read_text())
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (work / "map-conversion.log").open("w") as log:
        process = subprocess.Popen(
            [
                str(Path(sumo.SUMO_HOME) / "bin/sumo"),
                "--net-file",
                str(work / "network.net.xml"),
                "--remote-port",
                str(port),
                "--no-step-log",
                "true",
            ],
            stdout=log,
            stderr=log,
        )
        connection = None
        try:
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )

            def geo(xy):
                return list(connection.simulation.convertGeo(*xy))

            lanes, edges = {}, {}
            roads = []
            for edge in net.findall("edge"):
                if edge.get("function") == "internal":
                    continue
                shapes = []
                for lane in edge.findall("lane"):
                    shape = [tuple(map(float, p.split(","))) for p in lane.attrib["shape"].split()]
                    lanes[lane.attrib["id"]] = shape
                    allowed = lane.get("allow")
                    if (allowed is None or "bus" in allowed.split()) and "bus" not in lane.get(
                        "disallow", ""
                    ).split():
                        shapes.append(shape)
                if shapes:
                    line = [geo(xy) for xy in shapes[0]]
                    edges[edge.attrib["id"]] = line
                    roads.append(
                        feature(
                            "LineString",
                            edge.attrib["id"],
                            line,
                            layer="roads",
                            name=edge.get("name", "Road"),
                        )
                    )
            route = [edges[name] for name in mission["route_edges"]]
            stop = mission["stop"]
            features = roads + [
                feature(
                    "MultiLineString",
                    mission["mission_id"],
                    route,
                    layer="route",
                    name="Assigned trip",
                ),
                feature(
                    "Point",
                    "mission-stop-1",
                    geo(at_offset(lanes[stop["lane"]], stop["end_pos_m"])),
                    layer="stops",
                    name="Scheduled mission stop",
                    duration_s=stop["duration_s"],
                ),
            ]
            for station in stations["stations"]:
                features.append(
                    feature(
                        "Point",
                        station["station_id"],
                        [station["longitude"], station["latitude"]],
                        layer="stations",
                        name=station["station_id"],
                        hypothetical=True,
                        power_limit_W=station["power_limit_W"],
                        road_access=station["road_access"],
                        availability="unverified",
                    )
                )
            coordinates = [p for line in edges.values() for p in line]
            return {
                "type": "FeatureCollection",
                "context_version": "1.0.0",
                "run_id": run,
                "asset_id": asset,
                "mission_id": mission["mission_id"],
                "features": features,
                "bbox": [
                    min(p[0] for p in coordinates),
                    min(p[1] for p in coordinates),
                    max(p[0] for p in coordinates),
                    max(p[1] for p in coordinates),
                ],
                "attribution": "OpenStreetMap contributors · ODbL-1.0",
                "input_sha256": {
                    name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in {
                        "roads.osm.xml": osm,
                        "mission.json": scenario / "mission.json",
                        "stations.json": scenario / "stations.json",
                        "import-review.json": scenario / "import-review.json",
                    }.items()
                },
            }
        finally:
            if connection is not None:
                connection.close(wait=False)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def provision(conn, path):
    if path is None:
        return
    raw = Path(path).read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError("Map context exceeds local profile")
    context = json.loads(raw)
    run = os.environ["EVDT_RUN_ID"]
    policy = conn.execute(
        "SELECT policy FROM m_run_definitions WHERE run_id=%s", (run,)
    ).fetchone()["policy"]
    owners = [s for s in policy["sources"] if s["asset_id"] == context["asset_id"]]
    if context["run_id"] != run or not owners or context["type"] != "FeatureCollection":
        raise ValueError("Map context scope mismatch")
    if set(context["input_sha256"]) != {
        "roads.osm.xml",
        "mission.json",
        "stations.json",
        "import-review.json",
    } or any(
        s["expected"]["input_sha256"].get(k) != v
        for s in owners
        for k, v in context["input_sha256"].items()
    ):
        raise ValueError("Map context inputs differ from trusted run")
    digest = hashlib.sha256(raw).hexdigest()
    old = conn.execute(
        "SELECT sha256 FROM m_map_contexts WHERE run_id=%s AND asset_id=%s",
        (run, context["asset_id"]),
    ).fetchone()
    if old and old["sha256"] != digest:
        raise ValueError("Published map context is immutable")
    conn.execute(
        "INSERT INTO m_map_contexts VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (run, context["asset_id"], digest, raw, Jsonb(context)),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--asset-id", default="bus-001")
    parser.add_argument("--scenario", type=Path, default=Path("/scenario"))
    parser.add_argument("--osm", type=Path, default=Path("/input/roads.osm.xml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        context = build(args.scenario, args.osm, args.run_id, args.asset_id, Path(folder))
    with args.output.open("xb") as stream:
        stream.write(
            (
                json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
            ).encode()
        )
    print("Static map context prepared without stepping a vehicle", flush=True)


if __name__ == "__main__":
    main()
