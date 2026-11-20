"""Freeze a geographic candidate and consume independent journey evaluations."""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
INPUTS = (
    "roads.osm.xml",
    "roads.json",
    "mission.json",
    "stops.json",
    "heights.json",
    "geometry.json",
    "elevated-route.net.xml",
    "elevated-plant.net.xml",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files():
    paths = list((ROOT / "src/evdt/energy/route_aware").glob("*.py"))
    paths += list((ROOT / "scripts/geographic").glob("*.py"))
    paths += list((ROOT / "scripts/route_aware_energy").glob("*.py"))
    paths += list((ROOT / "qualification/energy/route-aware").glob("*.json"))
    paths += [
        ROOT / p
        for p in (
            "scripts/geographic/prepare_osm_network.py",
            "scripts/geographic/extract_osm.py",
            "containers/energy-route-worker.Dockerfile",
            "environments/dataflow-images.json",
            "environments/energy-geospatial-requirements.txt",
        )
    ]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def under_evidence(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT / "evidence/energy-v2"):
        raise ValueError("Study artifacts must stay in evidence/energy-v2")
    return path


def freeze(folder, journeys):
    if len(journeys) != 10 or len(set(journeys)) != 10:
        raise ValueError("Declare ten distinct prepared journeys before evaluation")
    inputs = []
    for name in journeys:
        path = under_evidence(name)
        hashes = {f: digest(path / f) for f in INPUTS}
        preparation = json.loads((path / "preparation.json").read_bytes())
        if not preparation["completed"] or preparation["files"] != hashes:
            raise ValueError("Journey preparation is incomplete or its inputs changed")
        if any(
            digest(ROOT / name) != value for name, value in preparation["source_sha256"].items()
        ):
            raise ValueError("Journey preparation source is no longer current")
        recipe = json.loads((path / "roads.json").read_bytes())["recipe"]
        if any(i["id"] == recipe["id"] for i in inputs):
            raise ValueError("Journey identities must be distinct")
        inputs.append(dict(id=recipe["id"], folder=path.relative_to(ROOT).as_posix(), files=hashes))
    folder.mkdir(parents=True, exist_ok=False)
    record = dict(
        version="geographic-energy-candidate/1.0.0",
        created=datetime.now(UTC).isoformat(),
        source=source_files(),
        inputs=inputs,
        criteria="qualification/energy/route-aware/study-criteria.json",
        scope="Independent SUMO battery comparison on real OSM roads and attributed raster elevations. Declared vehicle and road conditions; no map-specific fitted coefficients.",
        traffic_signals="Native SUMO operation retained. Optional declared fixed-cycle timing projects stop/start and dwell; no adaptive signals, queue prediction, or timing optimization.",
        independence="Trips are collected after freeze. Freeze alone does not establish independence: record earlier inspected outcomes and implementation corrections separately. Repeated known trips are regression evidence; only unconsumed route families can support a new reserved comparison.",
    )
    (folder / "candidate.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (folder / "claims").mkdir()
    print("Candidate frozen; no reserved energy outcomes consumed.")


def verify(folder):
    record = json.loads((folder / "candidate.json").read_bytes())
    if record["source"] != source_files():
        raise ValueError("Frozen source or common configuration changed")
    for item in record["inputs"]:
        path = under_evidence(item["folder"])
        if item["files"] != {name: digest(path / name) for name in INPUTS}:
            raise ValueError("Frozen journey inputs changed: " + item["id"])
    return record


def evaluate(folder, indices):
    record = verify(folder)
    if not indices or len(set(indices)) != len(indices) or any(not 1 <= i <= 10 for i in indices):
        raise ValueError("Select unique journey numbers from 1 through 10")
    selected = [record["inputs"][i - 1] for i in indices]
    paths = [folder / "claims" / (item["id"] + ".json") for item in selected]
    if any(path.exists() for path in paths):
        raise ValueError("This reserved evaluation was already claimed; preserve the result")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    output = "evidence/energy-v2/geographic-evaluation-" + stamp
    for path, item in zip(paths, selected):
        with path.open("x", encoding="utf-8") as stream:
            json.dump(
                dict(
                    candidate_sha256=digest(folder / "candidate.json"), id=item["id"], output=output
                ),
                stream,
            )
    command = [
        sys.executable,
        "scripts/route_aware_energy/qualify.py",
        "--case",
        "map-journey",
        "--output",
        output,
        "--journey",
        *(i["folder"] for i in selected),
    ]
    result = subprocess.run(command, cwd=ROOT, check=False)
    compatible = record["source"] == source_files()
    summary = dict(
        output=output, returncode=result.returncode, source_compatible=compatible, indices=indices
    )
    (folder / ("session-" + stamp + ".json")).write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    if not compatible:
        raise ValueError("Candidate changed during evaluation")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "verify", "evaluate"))
    parser.add_argument("folder")
    parser.add_argument("--journey", nargs="+")
    parser.add_argument("--indices", type=int, nargs="+")
    args = parser.parse_args()
    folder = under_evidence(args.folder)
    if args.action == "freeze":
        freeze(folder, args.journey or [])
    elif args.action == "verify":
        verify(folder)
        print("Frozen source, common configuration, and all ten journey inputs match.")
    else:
        return evaluate(folder, args.indices)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
