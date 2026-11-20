"""Render retained geographic inputs and measured journeys as a local desktop atlas."""

import argparse
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/route_aware_energy")]
from geometry_provider import GeometryProvider  # noqa: E402

from evdt.energy.route_aware.context import SourcePlan  # noqa: E402


def read(path):
    return json.loads(path.read_bytes())


def trajectory(folder):
    """Read a retained collector result; never generate or interpolate observations."""
    truth, study = read(folder / "reference.json"), read(folder / "study-result.json")
    rows = truth["rows"]
    forecasts = []
    for item in study["trips"][0]["forecasts"]:
        row = next(r for r in rows if r["working_s"] == item["cutoff_working_s"])
        forecasts.append(
            {
                **item,
                "measured_soc": row["soc"],
                "predicted_arrival_soc": rows[-1]["soc"] + item["arrival_soc_error"]
                if item.get("arrival_soc_error") is not None
                else None,
            }
        )
    return {
        "passed": study["passed"],
        "complete": truth["complete"],
        "arrival_soc": rows[-1]["soc"],
        "duration_working_s": rows[-1]["working_s"],
        "observations": [[r["longitude"], r["latitude"]] for r in rows],
        "charge": [[r["working_s"], r["soc"]] for r in rows],
        "remaining_energy": [
            [r["working_s"], (r["energy_j"] - rows[-1]["energy_j"]) / 3_600_000] for r in rows
        ],
        "forecasts": forecasts,
        "maximum_observed_draw_w": truth["maximum_observed_draw_w"],
        "reference_sha256": hashlib.sha256((folder / "reference.json").read_bytes()).hexdigest(),
        "folder": folder.relative_to(ROOT).as_posix(),
    }


def route_data(folder, context_folder, result=None):
    recipe = read(folder / "roads.json")["recipe"]
    provider, mission = GeometryProvider(folder / "geometry.json"), read(folder / "mission.json")
    context = provider(
        SimpleNamespace(
            network_sha256=provider.network.sha256, geometry_bundle_sha256=provider.bundle_sha256
        ),
        SimpleNamespace(
            plan=SourcePlan(provider.network.sha256, tuple(mission["route_edges"]), ())
        ),
    )
    route, profile, distance, previous = [], [], 0.0, None
    for road in context.route.occurrences:
        for point in road.shape:
            if previous:
                distance += math.hypot(point.x_m - previous.x_m, point.y_m - previous.y_m)
            route.append([point.longitude_deg, point.latitude_deg])
            profile.append([distance, point.elevation_m])
            previous = point
    factor = math.cos(math.radians(sum(p[1] for p in route) / len(route)))
    xs, ys = [p[0] * factor for p in route], [p[1] for p in route]
    west, east, south, north = min(xs), max(xs), min(ys), max(ys)
    span = max((east - west) / 1.55, north - south) * 1.18
    cx, cy = (west + east) / 2, (south + north) / 2
    bounds = [cx - span * 0.775, cy - span / 2, cx + span * 0.775, cy + span / 2]

    def inside(points):
        return bool(points) and (
            max(p[0] * factor for p in points) >= bounds[0]
            and min(p[0] * factor for p in points) <= bounds[2]
            and max(p[1] for p in points) >= bounds[1]
            and min(p[1] for p in points) <= bounds[3]
        )

    root = ET.parse(folder / "roads.osm.xml").getroot()
    nodes = {n.get("id"): [float(n.get("lon")), float(n.get("lat"))] for n in root.findall("node")}
    roads = []
    for way in root.findall("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        if "highway" not in tags:
            continue
        points = [nodes[n.get("ref")] for n in way.findall("nd") if n.get("ref") in nodes]
        if inside(points):
            roads.append([points, tags["highway"], tags.get("name", "")])
    backdrop = read(context_folder / (recipe["extract"] + "-context.json"))
    backdrop = {
        "water": [p for p in backdrop["water"] if inside(p[0])],
        "parks": [p for p in backdrop["parks"] if inside(p[0])],
        "places": [p for p in backdrop["places"] if inside([[p[1], p[2]]])],
    }
    output = dict(
        recipe=recipe,
        folder=folder.relative_to(ROOT).as_posix(),
        route=route,
        profile=profile,
        roads=roads,
        context=backdrop,
        bounds=bounds,
        factor=factor,
        stops=read(folder / "stops.json")["stops"],
        distance_km=distance / 1000,
        ascent=sum(max(0, b[1] - a[1]) for a, b in zip(profile, profile[1:])),
        descent=sum(max(0, a[1] - b[1]) for a, b in zip(profile, profile[1:])),
    )
    if result:
        truth = read(result / "reference.json")
        if truth["network_sha256"] != read(folder / "heights.json")["plant_network_sha256"]:
            raise ValueError("Recorded trajectory uses another prepared map")
        output["result"] = trajectory(result)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--results", type=Path, nargs="*", default=[])
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".private"):
        parser.error("Atlas output must remain in ignored .private storage")
    results = {}
    for session in args.results:
        if (session / "worker-result.json").exists():
            report = read(session / "worker-result.json")
            children = list(session.glob("journey-*/study-result.json")) or [
                session / "study-result.json"
            ]
        else:
            # An explicit child selects one retained journey from a batch.
            # Its parent's source and cleanup checks still apply.
            report = read(session.parent / "worker-result.json")
            if session.name not in {r["folder"] for r in report.get("results", [])}:
                raise ValueError("Selected journey is absent from its guarded parent batch")
            children = [session / "study-result.json"]
        for child in children:
            study = read(child)
            if study["trips"]:
                identity = study["trips"][0]["id"]
                if identity in results:
                    raise ValueError("Select one explicit result per journey")
                results[identity] = child.parent.resolve()
        if not report["source_unchanged"] or not report["cleanup_verified"]:
            raise ValueError("Session source or cleanup verification failed")
    payload = [
        route_data(ROOT / item["folder"], args.context, results.get(item["id"]))
        for item in read(args.candidate / "candidate.json")["inputs"]
    ]
    output.mkdir(parents=True, exist_ok=False)
    template = Path(__file__).with_name("atlas.html").read_text(encoding="utf-8")
    serialized = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    (output / "index.html").write_text(template.replace("PAYLOAD", serialized), encoding="utf-8")
    (output / "inputs.json").write_text(
        json.dumps(
            [
                dict(
                    id=p["recipe"]["id"],
                    folder=p["folder"],
                    result=p.get("result", {}).get("folder"),
                )
                for p in payload
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{len(payload)} real-road journeys rendered; source data remain unchanged.")


if __name__ == "__main__":
    main()
