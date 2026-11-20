"""Guarded real-road reference and independently projected forecast evaluation."""

import hashlib
import json
import os
import subprocess
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from geometry_provider import GeometryProvider
from plant_network import corridor
from runtime import build_image, remove_owned, run_isolated
from study import cutoffs, metrics, score

from evdt.energy.route_aware import CONTRACT_VERSION
from evdt.energy.route_aware.context import SourcePlan, SourceStop
from evdt.energy.route_aware.contracts import (
    Conditions,
    LiveState,
    Origin,
    SourceScope,
    StopRef,
    VehicleProfile,
)
from evdt.energy.route_aware.policy import ForecastPolicy
from evdt.energy.route_aware.scenarios import RoadConditions, RouteScenario
from evdt.energy.route_aware.scheduling import Claim
from evdt.energy.route_aware.wire import (
    ForecastRequest,
    canonical_bytes,
    decode_record,
    fingerprint,
)

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "sha256:4334958c68c76796c79099bffdb48e5c9ee422b65856a17db7c9e6bdd4616b90"


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def reference(folder, inputs, project):
    receipt = corridor(
        inputs / "elevated-plant.net.xml",
        read(inputs / "mission.json")["route_edges"],
        folder / "reference-network.net.xml",
    )
    write(folder / "reference-network.json", receipt)
    name = project + "-reference"
    command = [
        "docker",
        "run",
        "--pull=never",
        "--name",
        name,
        "--label",
        "com.docker.compose.project=" + project,
        "--label",
        "com.docker.compose.service=reference",
        "--label",
        "com.docker.compose.oneoff=False",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--memory=512m",
        "--memory-swap=512m",
        "--cpus=1",
        "--pids-limit=64",
        "--tmpfs=/tmp:size=32m",
        "--volume",
        str(folder) + ":/evidence",
        "--volume",
        str(inputs) + ":/inputs:ro",
        "--volume",
        str(ROOT / "scripts/geographic/reference.py") + ":/reference.py:ro",
        "--entrypoint",
        "python",
        IMAGE,
        "/reference.py",
        "/evidence",
    ]
    try:
        with (folder / "reference.log").open("xb") as log:
            result = subprocess.run(command, stdout=log, stderr=log, timeout=540)
        info = read_inspect(name)
        write(
            folder / "reference-container.json",
            {
                "image": info["Image"],
                "exit_code": info["State"]["ExitCode"],
                "oom_killed": info["State"]["OOMKilled"],
            },
        )
        if result.returncode or info["State"]["OOMKilled"]:
            raise RuntimeError("SUMO reference failed; inspect reference.log and sumo.log")
        truth = read(folder / "reference.json")
        if not truth["complete"]:
            raise RuntimeError("SUMO reference incomplete")
        return truth
    finally:
        existing = subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"], timeout=10
        ).strip()
        if existing:
            info = read_inspect(name)
            if info["Config"]["Labels"].get("com.docker.compose.project") != project:
                raise RuntimeError("Foreign resource; cleanup refused")
            if info["State"]["Running"]:
                subprocess.run(
                    ["docker", "stop", "--time", "3", name],
                    check=True,
                    capture_output=True,
                    timeout=15,
                )
            subprocess.run(["docker", "rm", name], check=True, capture_output=True, timeout=15)


def read_inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name], timeout=10))[0]


def request_from_observation(
    provider, mission, stops, recipe, row, number, *, source_distance=False
):
    now = time.time()
    base = row["external_index"]
    source_stops = tuple(
        SourceStop(
            **{
                k: s[k]
                for k in ("stop_id", "external_index", "lane_id", "offset_m", "dwell_working_s")
            }
        )
        for s in stops
    )
    remaining = tuple(
        s
        for i, s in enumerate(source_stops)
        if i not in row["completed_stop_indices"] and s.external_index >= base
    )
    source_plan = SourcePlan(provider.network.sha256, tuple(mission["route_edges"]), remaining)
    assignment = SimpleNamespace(
        network_sha256=provider.network.sha256, geometry_bundle_sha256=provider.bundle_sha256
    )
    prepared = provider(
        assignment, SimpleNamespace(plan=source_plan, external_index=base, lane_id=row["lane_id"])
    )
    occurrence = prepared.occurrence_for(base)
    if prepared.route.occurrences[occurrence].lane_id != row["lane_id"]:
        raise ValueError("Observed lane differs from projected lane")
    completed = ()
    i = row["current_stop_index"]
    current = (
        StopRef(
            prepared.occurrence_for(source_stops[i].external_index),
            source_stops[i].stop_id,
        )
        if i is not None
        else None
    )
    vehicle = decode_record(
        VehicleProfile, read(ROOT / "qualification/energy/route-aware/vehicle-profile.json")
    )
    vehicle = replace(
        vehicle,
        capacity_kwh=recipe["capacity_kwh"],
        loaded_mass_kg=recipe["mass_kg"],
        max_speed_m_s=27.78,
    )
    state = LiveState(
        SourceScope(str(uuid4()), "bus-001", "osm-study-source", 1, str(uuid4())),
        str(uuid4()),
        fingerprint(row),
        row["working_s"],
        now,
        now,
        "running",
        prepared.route.revision,
        occurrence,
        row["lane_offset_m"],
        row["soc"],
        row["speed_m_s"],
        recipe["auxiliary_w"],
        "not_charging",
        298.15,
        completed,
        current,
        row["remaining_dwell_s"],
    )
    origin = Origin(
        "declared",
        "Study operating conditions on real OSM roads",
        "Declared before the trip; no future vehicle readings",
    )
    conditions = (
        Conditions(
            origin,
            now,
            0,
            14400,
            auxiliary_power_w=recipe["auxiliary_w"],
            rolling_multiplier=recipe["surface_multiplier"],
            speed_factor=recipe["speed_factor"],
        ),
    )
    localized = []
    for position, start in enumerate(prepared.external_occurrences):
        original_index = prepared.first_external_index + position
        factor = recipe["speed_factor"]
        for zone in recipe.get("speed_zones", []):
            if (
                int(zone["start_fraction"] * len(mission["route_edges"]))
                <= original_index
                < int(zone["end_fraction"] * len(mission["route_edges"]))
            ):
                factor = min(factor, zone["speed_factor"])
        if factor == recipe["speed_factor"]:
            continue
        stop = (
            prepared.external_occurrences[position + 1]
            if position + 1 < len(prepared.external_occurrences)
            else len(prepared.route.occurrences)
        )
        localized.extend(
            RoadConditions(i, (replace(conditions[0], speed_factor=factor),))
            for i in range(start, stop)
        )
    scenario = (
        RouteScenario("declared-road-restrictions", origin, now, tuple(localized))
        if localized
        else None
    )
    policy = decode_record(
        ForecastPolicy, read(ROOT / "qualification/energy/route-aware/geographic-policy.json")
    )
    if source_distance:
        policy = replace(policy, sumo_braking_source_distance=True)
    return ForecastRequest(
        CONTRACT_VERSION,
        number,
        now,
        "qualification",
        state,
        prepared.route,
        vehicle,
        conditions,
        policy,
        scenario=scenario,
    )


def run(output, *, shared=None, journey=None, source_distance=False):
    from qualify_restarts import monitor

    journey = read(output / "journey.json") if journey is None else journey
    if isinstance(journey, list):
        return batch(output, journey, source_distance=source_distance)
    inputs = (ROOT / journey["folder"]).resolve()
    if (
        not inputs.is_relative_to(ROOT / "evidence/energy-v2")
        or hashlib.sha256((inputs / "heights.json").read_bytes()).hexdigest() != journey["sha256"]
    ):
        raise ValueError("Prepared geography changed")
    if "inputs" in journey and any(
        hashlib.sha256((inputs / name).read_bytes()).hexdigest() != digest
        for name, digest in journey["inputs"].items()
    ):
        raise ValueError("Prepared journey input changed")
    heights = read(inputs / "heights.json")
    mission = read(inputs / "mission.json")
    stops = read(inputs / "stops.json")
    if (
        hashlib.sha256((inputs / "elevated-plant.net.xml").read_bytes()).hexdigest()
        != heights["plant_network_sha256"]
    ):
        raise ValueError("Reference network changed")
    defaults = read(ROOT / "qualification/energy/route-aware/osm-geographic-study.json")["defaults"]
    recipe = {**defaults, **read(inputs / "roads.json")["recipe"]}
    write(
        output / "run-input.json",
        {"recipe": recipe, "mission": mission, "stops": stops, "height_receipt": heights},
    )
    project = (
        shared[0] if shared else "evdt-e2c-" + hashlib.sha256(output.name.encode()).hexdigest()[:12]
    )
    report = {
        "version": "osm-energy-evaluation/1.0.0",
        "passed": False,
        "cleanup_verified": False,
        "trips": [],
        "journey": journey,
    }
    owner = str(uuid4())
    claim = None
    previous = os.environ.get("EVDT_ENERGY_RESOURCE_PROJECT")
    os.environ["EVDT_ENERGY_RESOURCE_PROJECT"] = project
    try:
        with (
            nullcontext()
            if shared
            else monitor(
                project,
                output,
                allow_replacement=True,
                finite_services=("energy-worker", "reference"),
            )
        ):
            image = shared[1] if shared else build_image(output)
            report["worker_image"] = image
            truth = reference(output, inputs, project)
            provider = GeometryProvider(inputs / "geometry.json")
            trip = {
                "id": recipe["id"],
                "duration_working_s": truth["rows"][-1]["working_s"],
                "distance_m": truth["rows"][-1]["distance_m"],
                "forecasts": [],
            }
            report["trips"].append(trip)
            for number, index in enumerate(cutoffs(truth["rows"]), 1):
                request = request_from_observation(
                    provider,
                    mission,
                    stops["stops"],
                    recipe,
                    truth["rows"][index],
                    number,
                    source_distance=source_distance,
                )
                tick = time.monotonic()
                claim = Claim(number, owner, request, tick, tick + 30)
                result = run_isolated(image, claim, deadline_monotonic_s=tick + 30)
                elapsed = time.monotonic() - tick
                (output / f"input-{index}.json").write_bytes(canonical_bytes(request))
                (output / f"result-{index}.json").write_bytes(canonical_bytes(result))
                trip["forecasts"].append(score(request, result, truth["rows"], index, elapsed))
                claim = None
                write(output / "study-progress.json", report)
            report["metrics"] = metrics(report["trips"])
            report["passed"] = report["metrics"]["passed"]
    except (Exception, KeyboardInterrupt) as exc:
        report.update(passed=False, error=repr(exc))
    finally:
        if claim:
            remove_owned(claim.fence, claim.owner)
        leftovers = subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
            timeout=10,
        )
        report["cleanup_verified"] = not leftovers.strip()
        report["passed"] &= report["cleanup_verified"]
        if previous is None:
            os.environ.pop("EVDT_ENERGY_RESOURCE_PROJECT", None)
        else:
            os.environ["EVDT_ENERGY_RESOURCE_PROJECT"] = previous
        write(output / "study-result.json", report)
    return report


def batch(output, journeys, *, source_distance=False):
    """One host admission, one sampler, and at most one bus or worker at a time."""
    from qualify_restarts import monitor

    if not 1 <= len(journeys) <= 10:
        raise ValueError("A geographic session accepts one to ten sequential journeys")
    project = "evdt-e2c-" + hashlib.sha256(output.name.encode()).hexdigest()[:12]
    report = {
        "version": "osm-energy-batch/1.0.0",
        "passed": False,
        "cleanup_verified": False,
        "journeys": journeys,
        "results": [],
        "trips": [],
    }
    try:
        with monitor(
            project, output, allow_replacement=True, finite_services=("energy-worker", "reference")
        ):
            image = build_image(output)
            for index, journey in enumerate(journeys):
                folder = output / f"journey-{index + 1:02d}"
                folder.mkdir()
                print("Real-road journey:", Path(journey["folder"]).name, flush=True)
                result = run(
                    folder,
                    shared=(project, image),
                    journey=journey,
                    source_distance=source_distance,
                )
                report["results"].append(
                    {
                        "folder": folder.name,
                        "passed": result["passed"],
                        "cleanup_verified": result["cleanup_verified"],
                        "error": result.get("error"),
                    }
                )
                report["trips"].extend(result["trips"])
                write(output / "study-progress.json", report)
                if not result["passed"]:
                    break
            report["metrics"] = metrics(report["trips"])
            report["passed"] = len(report["results"]) == len(journeys) and all(
                r["passed"] for r in report["results"]
            )
    except (Exception, KeyboardInterrupt) as exc:
        report.update(passed=False, error=repr(exc))
    finally:
        leftovers = subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
            timeout=10,
        )
        report["cleanup_verified"] = not leftovers.strip()
        report["passed"] &= report["cleanup_verified"]
        write(output / "study-result.json", report)
    return report
