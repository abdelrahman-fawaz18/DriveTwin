"""Check the unchanged Energy Worker on a prepared OSM route at departure."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/route_aware_energy")]

from fixtures import development_request  # noqa: E402
from geometry_provider import GeometryProvider  # noqa: E402

from evdt.energy.route_aware.context import SourcePlan  # noqa: E402
from evdt.energy.route_aware.forecast import parse_result, validate_result  # noqa: E402
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint  # noqa: E402


def receipt(path):
    return {
        "path": path.resolve().relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def record(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")


def check(folder, output, terrain):
    mission = json.loads((folder / "mission.json").read_bytes())
    network = receipt(folder / "route.net.xml")
    roads = receipt(folder / "roads.osm.xml")
    if network["sha256"] != mission["network_sha256"]:
        raise ValueError("Mission and network fingerprints differ")
    terrain_record = json.loads(terrain.read_bytes()) if terrain else None
    mode = "terrain" if terrain else "declared_flat"
    suffix = "-terrain" if terrain else "-flat"
    bundle = output / ("geometry" + suffix + ".json")
    record(
        bundle,
        {
            "version": "route-energy-geometry/2.0.0",
            "network": network,
            "mode": mode,
            "terrain": receipt(terrain) if terrain else None,
            "osm": roads,
            "origin": {
                "kind": "observed" if terrain else "declared",
                "source": terrain_record["provider"] if terrain else "OSM portability check",
                "reference": terrain_record["sha256"] if terrain else "Flat smoke-check assumption",
            },
            "vertical_datum": terrain_record["vertical_datum"]
            if terrain
            else "declared-local-height",
            "flat_height_m": None if terrain else 0,
        },
    )
    assignment, fixture = development_request(str(uuid4()))
    provider = GeometryProvider(bundle)
    assignment = replace(
        assignment,
        network_sha256=network["sha256"],
        geometry_bundle_sha256=provider.bundle_sha256,
    )
    plan = SourcePlan(network["sha256"], tuple(mission["route_edges"]), ())
    prepared = provider(assignment, SimpleNamespace(plan=plan))
    state = replace(
        fixture.state,
        route_revision=prepared.route.revision,
        occurrence_index=prepared.external_occurrences[0],
    )
    request = replace(
        fixture, route=prepared.route, state=state, assignment_sha256=fingerprint(assignment)
    )
    raw = canonical_bytes(request)
    (output / ("forecast-input" + suffix + ".json")).write_bytes(raw + b"\n")
    started = time.perf_counter()
    run = subprocess.run(
        [sys.executable, "-m", "evdt.energy.route_aware.worker"],
        input=raw,
        cwd=ROOT / "src",
        capture_output=True,
        timeout=30,
    )
    elapsed = time.perf_counter() - started
    if run.returncode:
        raise RuntimeError(run.stderr.decode("utf-8", errors="replace").strip())
    (output / ("forecast-result" + suffix + ".json")).write_bytes(run.stdout)
    result = parse_result(run.stdout)
    validate_result(request, result)
    return {
        "passed": result.status == "available",
        "scope": "declared departure input; no SUMO vehicle or accuracy reference",
        "elevation_mode": mode,
        "network_sha256": network["sha256"],
        "vehicle_profile_sha256": fingerprint(fixture.vehicle),
        "route_km": mission["route_length_m"] / 1000,
        "road_count": len(mission["route_edges"]),
        "request_bytes": len(raw),
        "result_status": result.status,
        "result_reason": result.reason,
        "elapsed_real_life_s": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--terrain-manifest", type=Path)
    args = parser.parse_args()
    suffix = "-terrain" if args.terrain_manifest else "-flat"
    output = args.output or args.folder
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    report_path = output / ("portability" + suffix + ".json")
    if report_path.exists():
        parser.error("Portability report already exists")
    try:
        report = check(args.folder, output, args.terrain_manifest)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        report = {"passed": False, "error": str(error)}
    record(report_path, report)
    print(json.dumps(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
