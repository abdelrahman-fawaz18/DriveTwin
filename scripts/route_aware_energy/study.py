"""Independent trip-family evaluation with an immutable reserved-evaluation claim."""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from geometry_provider import GeometryProvider  # noqa: E402
from reference_sumo import validate_progress  # noqa: E402
from runtime import build_image, remove_owned, run_isolated, verify_image  # noqa: E402

from evdt.energy.route_aware import CONTRACT_VERSION  # noqa: E402
from evdt.energy.route_aware.context import SourcePlan, SourceStop  # noqa: E402
from evdt.energy.route_aware.contracts import (  # noqa: E402
    Conditions,
    LiveState,
    Origin,
    SourceScope,
    StopRef,
    VehicleProfile,
)
from evdt.energy.route_aware.forecast import _motion  # noqa: E402
from evdt.energy.route_aware.geometry import remaining_route  # noqa: E402
from evdt.energy.route_aware.ledger import integrate  # noqa: E402
from evdt.energy.route_aware.policy import ForecastPolicy  # noqa: E402
from evdt.energy.route_aware.scheduling import Claim  # noqa: E402
from evdt.energy.route_aware.wire import (  # noqa: E402
    ForecastRequest,
    canonical_bytes,
    decode_record,
    fingerprint,
)

FRESH_B4 = "b4-refresh-v1"
EXPANDED_ROUTES = "expanded-routes-v1"


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def candidate():
    paths = [
        *ROOT.glob("src/evdt/energy/route_aware/*.py"),
        *ROOT.glob("scripts/route_aware_energy/*.py"),
        *ROOT.glob("qualification/energy/route-aware/*.json"),
        ROOT / "containers/energy-route-worker.Dockerfile",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def recipes(partition, *, fresh_b4=False, expanded_routes=False):
    manifest = read(ROOT / "qualification/energy/route-aware/study-recipes.json")
    trips = manifest["trips"]
    if len({trip["id"] for trip in trips}) != len(trips):
        raise ValueError("Duplicate trip identity")
    families = {}
    for trip in trips:
        if families.setdefault(trip["family"], trip["partition"]) != trip["partition"]:
            raise ValueError("Related family crossed study partitions")
    if fresh_b4:
        replacement = read(
            ROOT / f"qualification/energy/route-aware/study-recipes-{FRESH_B4}.json"
        )["trips"]
        if len(replacement) != 3 or any(trip["partition"] != "reserved" for trip in replacement):
            raise ValueError("Fresh B4 requires three reserved route families")
        for key in ("id", "family", "seed"):
            identities = {trip[key] for trip in replacement}
            if len(identities) != len(replacement) or identities.intersection(
                trip[key] for trip in trips
            ):
                raise ValueError(f"Fresh B4 repeats an existing trip {key}")
        trips = [trip for trip in trips if trip["partition"] != "reserved"] + replacement
    elif expanded_routes:
        replacement = read(
            ROOT / f"qualification/energy/route-aware/study-recipes-{EXPANDED_ROUTES}.json"
        )["trips"]
        if (
            any(
                sum(trip["partition"] == name for trip in replacement) != 3
                for name in ("development", "validation", "reserved")
            )
            or len(replacement) != 9
        ):
            raise ValueError("Expanded study requires three fresh trips in each partition")
        prior = (
            trips
            + read(ROOT / f"qualification/energy/route-aware/study-recipes-{FRESH_B4}.json")[
                "trips"
            ]
        )
        for key in ("id", "family", "seed"):
            identities = {trip[key] for trip in replacement}
            if len(identities) != len(replacement) or identities.intersection(
                trip[key] for trip in prior
            ):
                raise ValueError(f"Expanded study repeats an existing trip {key}")
        trips = replacement
    return [{**manifest["defaults"], **trip} for trip in trips if trip["partition"] == partition]


def reference(folder, project, recipe, log, *, image=None):
    write(folder / "recipe.json", recipe)
    if image is None:
        image = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", "evdt-energy-collector:p2"], timeout=10
            )
        )[0]["Id"]
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
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--tmpfs",
        "/tmp:size=32m",
        "--volume",
        str(folder) + ":/evidence",
        "--volume",
        str(ROOT / "scripts/route_aware_energy/reference_sumo.py") + ":/reference.py:ro",
        "--entrypoint",
        "python",
        image,
        "/reference.py",
        "/evidence",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=180)
        log.write(completed.stdout + completed.stderr)
        log.flush()
        info = json.loads(subprocess.check_output(["docker", "inspect", name], timeout=10))[0]
        if completed.returncode or info["State"]["OOMKilled"]:
            raise RuntimeError("Independent SUMO trip failed; inspect reference log")
        result = read(folder / "reference.json")
        if not result["complete"]:
            raise RuntimeError("Incomplete independent trip")
        return result, image
    finally:
        exists = subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"], timeout=10
        ).strip()
        if exists:
            info = json.loads(subprocess.check_output(["docker", "inspect", name], timeout=10))[0]
            if info["Config"]["Labels"].get("com.docker.compose.project") != project:
                raise RuntimeError("Foreign reference container; cleanup refused")
            subprocess.run(
                ["docker", "rm", "-f", name], check=True, capture_output=True, timeout=15
            )


def reuse_reference(output, folder, recipe):
    receipt = output / "reference-reuse.json"
    if not receipt.exists():
        return None
    prior_folder = (ROOT / read(receipt)["folder"]).resolve()
    if prior_folder.parent != ROOT / "evidence/energy-v2":
        raise ValueError("Reference reuse must identify a retained study folder")
    prior = read(prior_folder / "study-result.json")
    if prior["partition"] != recipe["partition"] or not prior["cleanup_verified"]:
        raise ValueError("Reference partition or prior cleanup mismatch")
    script = "scripts/route_aware_energy/reference_sumo.py"
    if prior["candidate"][script] != candidate()[script]:
        raise ValueError("Reference generation source changed")
    trip = next((item for item in prior["trips"] if item["id"] == recipe["id"]), None)
    if trip is None:
        return None
    original = prior_folder / recipe["id"]
    raw = (original / "reference.json").read_bytes()
    result = json.loads(raw)
    if (
        read(original / "recipe.json") != recipe
        or not result["complete"]
        or hashlib.sha256(raw).hexdigest() != trip["reference_sha256"]
        or hashlib.sha256((original / "network.net.xml").read_bytes()).hexdigest()
        != result["network_sha256"]
    ):
        raise ValueError("Reference recipe, completeness, or byte fingerprints disagree")
    for name in ("recipe.json", "reference.json", "network.net.xml"):
        shutil.copyfile(original / name, folder / name)
    write(
        folder / "reuse.json",
        {
            "source": original.relative_to(ROOT).as_posix(),
            "reference_sha256": trip["reference_sha256"],
            "reference_script_sha256": prior["candidate"][script],
            "purpose": "Reuse complete independent truth; prior failed forecast outcome is unchanged",
        },
    )
    return result, trip["reference_image"]


def projected_request(folder, recipe, truth, row, number):
    # Shared engineering parameters are declared before the trip. No future state
    # or hidden reference-device parameter is included in this projection.
    now = time.time()
    origin = Origin("synthetic", "Independent route family", recipe["family"])
    bundle = folder / "geometry.json"
    if not bundle.exists():
        write(
            bundle,
            {
                "version": "route-energy-geometry/2.0.0",
                "network": {
                    "path": (folder / "network.net.xml").relative_to(ROOT).as_posix(),
                    "sha256": truth["network_sha256"],
                },
                "mode": "network_3d",
                "terrain": None,
                "osm": None,
                "origin": json.loads(canonical_bytes(origin)),
                "vertical_datum": "synthetic-east-north-height",
                "flat_height_m": None,
            },
        )
    provider = GeometryProvider(bundle)
    stops = tuple(decode_record(SourceStop, stop) for stop in truth["stops"])
    source_plan = SourcePlan(truth["network_sha256"], tuple(truth["edges"]), stops)
    assignment = SimpleNamespace(
        geometry_bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        network_sha256=truth["network_sha256"],
    )
    prepared = provider(assignment, SimpleNamespace(plan=source_plan))
    index = prepared.external_occurrences[row["external_index"]]
    if prepared.route.occurrences[index].lane_id != row["lane_id"]:
        raise ValueError("Cutoff must lie on its declared external lane")
    completed = tuple(
        StopRef(prepared.external_occurrences[stops[i].external_index], stops[i].stop_id)
        for i in row["completed_stop_indices"]
    )
    current = row["current_stop_index"]
    current_ref = (
        StopRef(
            prepared.external_occurrences[stops[current].external_index], stops[current].stop_id
        )
        if current is not None
        else None
    )
    vehicle = decode_record(
        VehicleProfile, read(ROOT / "qualification/energy/route-aware/vehicle-profile.json")
    )
    vehicle = replace(
        vehicle, capacity_kwh=recipe["plant_capacity_kwh"], loaded_mass_kg=recipe["plant_mass_kg"]
    )
    state = LiveState(
        SourceScope(str(uuid4()), "bus-001", "study-source", 1, str(uuid4())),
        str(uuid4()),
        fingerprint(row),
        row["working_s"],
        now,
        now,
        "running",
        prepared.route.revision,
        index,
        row["lane_offset_m"],
        row["soc"],
        row["speed_m_s"],
        recipe["auxiliary_w"],
        "not_charging",
        298.15,
        completed,
        current_ref,
        row["remaining_dwell_s"],
    )
    conditions = (Conditions(origin, now, 0, 14400, auxiliary_power_w=recipe["auxiliary_w"]),)
    return ForecastRequest(
        CONTRACT_VERSION,
        number,
        now,
        "qualification",
        state,
        prepared.route,
        vehicle,
        conditions,
        # The independent collector uses SUMO's default Euler update at 0.2 s.
        ForecastPolicy(sumo_euler_step_working_s=0.2),
    )


def cutoffs(rows):
    targets = [
        rows[0]["working_s"],
        30,
        60,
        120,
        *(rows[-1]["working_s"] * f for f in (0.25, 0.5, 0.75)),
    ]
    indices = set()
    for target in targets:
        found = next(
            (
                i
                for i, row in enumerate(rows)
                if row["working_s"] >= target
                and not row["lane_id"].startswith(":")
                and not row.get("stop_entry_transition", False)
            ),
            None,
        )
        if found is not None and found < len(rows) - 2:
            indices.add(found)
    return sorted(indices)


def score(request, result, rows, index, elapsed):
    initial, final = rows[index], rows[-1]
    net = initial["energy_j"] - final["energy_j"]
    draw = final["draw_j"] - initial["draw_j"]
    minimum_soc = min(initial["soc"], *(row["interval_minimum_soc"] for row in rows[index + 1 :]))
    item = {
        "cutoff_working_s": initial["working_s"],
        "remaining_reference_working_s": final["working_s"] - initial["working_s"],
        "reference_net_j": net,
        "reference_draw_j": draw,
        "reference_minimum_soc": minimum_soc,
        "status": result.status,
        "reason": result.reason,
        "elapsed_real_life_s": elapsed,
        "baseline_j": None,
    }
    earlier = next(
        (row for row in reversed(rows[:index]) if row["working_s"] <= initial["working_s"] - 30),
        None,
    )
    if earlier and initial["distance_m"] - earlier["distance_m"] >= 1:
        item["baseline_j"] = (
            (earlier["energy_j"] - initial["energy_j"])
            / (initial["distance_m"] - earlier["distance_m"])
            * remaining_route(request.route, request.state).distance_m
        )
    if result.summary is None:
        return item
    summary = result.summary
    error = summary.ledger.net_battery_j - net
    item.update(
        predicted_net_j=summary.ledger.net_battery_j,
        signed_error_j=error,
        normalized_absolute_error=abs(error) / max(3_600_000, 0.1 * draw),
        normalized_worst_error=abs(error) / max(7_200_000, 0.2 * draw),
        predicted_minimum_soc=summary.minimum_soc,
        arrival_soc_error=None
        if summary.arrival_soc is None
        else summary.arrival_soc - final["soc"],
        false_safe=minimum_soc < request.vehicle.reserve_soc - 1e-6
        and summary.minimum_soc >= request.vehicle.reserve_soc
        and summary.ledger.motion_feasible,
    )
    route = remaining_route(request.route, request.state)
    plan = _motion(request, route)
    fine = integrate(
        route,
        plan,
        request.state,
        request.vehicle,
        request.conditions,
        replace(request.policy, integration_step_working_s=0.1),
        scenario=request.scenario,
    )
    item["convergence_relative"] = abs(fine.net_battery_j - summary.ledger.net_battery_j) / max(
        36_000, abs(fine.net_battery_j)
    )
    item["distance_closure_m"] = abs(plan.distance_m - route.distance_m)
    item["distance_closure_limit_m"] = max(0.1, 0.0001 * route.distance_m)
    return item


def metrics(trips):
    criteria = read(ROOT / "qualification/energy/route-aware/study-criteria.json")
    rows = [row for trip in trips for row in trip["forecasts"]]
    available = [row for row in rows if "signed_error_j" in row]
    complete = len(rows) == len(available) and bool(rows)
    trip_means = (
        [
            sum(row["normalized_absolute_error"] for row in trip["forecasts"])
            / len(trip["forecasts"])
            for trip in trips
        ]
        if complete
        else []
    )
    elapsed = sorted(row["elapsed_real_life_s"] for row in rows)
    p95 = elapsed[math.ceil(0.95 * len(elapsed)) - 1] if elapsed else None
    common = [row for row in available if row["baseline_j"] is not None]
    result = {
        "trips": len(trips),
        "cutoffs": len(rows),
        "available_cutoffs": len(available),
        "trip_balanced_normalized_mae": sum(trip_means) / len(trip_means) if trip_means else None,
        "worst_normalized_error": max(
            (row["normalized_worst_error"] for row in available), default=None
        ),
        "false_safe_count": sum(row["false_safe"] for row in available),
        "trip_balanced_signed_error_j": sum(
            sum(row["signed_error_j"] for row in trip["forecasts"]) / len(trip["forecasts"])
            for trip in trips
        )
        / len(trips)
        if complete
        else None,
        "max_absolute_arrival_soc_error": max(
            (
                abs(row["arrival_soc_error"])
                for row in available
                if row.get("arrival_soc_error") is not None
            ),
            default=None,
        ),
        "distance_closure_passed": complete
        and all(row["distance_closure_m"] <= row["distance_closure_limit_m"] for row in available),
        "max_convergence_relative": max(
            (row["convergence_relative"] for row in available), default=None
        ),
        "worker_p95_real_life_s": p95,
        "baseline_common_cutoffs": len(common),
        "baseline_common_mae_j": sum(
            abs(row["baseline_j"] - row["reference_net_j"]) for row in common
        )
        / len(common)
        if common
        else None,
        "model_common_mae_j": sum(abs(row["signed_error_j"]) for row in common) / len(common)
        if common
        else None,
        "model_loses_to_baseline_cutoffs": sum(
            abs(row["signed_error_j"]) > abs(row["baseline_j"] - row["reference_net_j"])
            for row in common
        ),
    }
    result["passed"] = bool(
        complete
        and result["trip_balanced_normalized_mae"]
        <= criteria["trip_mean_normalized_absolute_error_max"]
        and result["worst_normalized_error"] <= criteria["worst_normalized_absolute_error_max"]
        and result["false_safe_count"] <= criteria["false_safe_reserve_count_max"]
        and result["max_convergence_relative"] <= criteria["numerical_convergence_max_relative"]
        and result["distance_closure_passed"]
        and p95 < criteria["worker_target_p95_real_life_s"]
        and all(row["elapsed_real_life_s"] <= criteria["worker_limit_real_life_s"] for row in rows)
    )
    return result


def check_freeze_evidence(
    result, guarded, partition, expected_candidate, *, fresh_b4=False, expanded_routes=False
):
    if (
        result["partition"] != partition
        or not result["passed"]
        or not result["cleanup_verified"]
        or result["candidate"] != expected_candidate
        or guarded.get("case") != "study-" + partition
        or guarded.get("passed") is not True
        or guarded.get("runtime_host_guard", {}).get("first_sample_validated") is not True
        or guarded.get("runtime_host_guard", {}).get("failure") is not None
        or guarded.get("worker", {}).get("source_unchanged") is not True
        or guarded.get("fresh_b4", False) != fresh_b4
        or guarded.get("expanded_routes", False) != expanded_routes
    ):
        raise ValueError("Freeze requires complete guarded evidence for this exact candidate")
    if fresh_b4 or expanded_routes:
        expected_ids = [
            trip["id"]
            for trip in recipes(partition, fresh_b4=fresh_b4, expanded_routes=expanded_routes)
        ]
        if (
            result.get("study_id") != (EXPANDED_ROUTES if expanded_routes else FRESH_B4)
            or guarded["worker"] != {**result, "source_unchanged": True}
            or [trip["id"] for trip in result["trips"]] != expected_ids
            or any(len(trip["forecasts"]) != 7 for trip in result["trips"])
        ):
            raise ValueError("Freeze requires all seven cutoffs for each study trip")


def study_directory(fresh_b4, *, expanded_routes=False):
    root = ROOT / ".private/energy-v2"
    if expanded_routes:
        return root / EXPANDED_ROUTES
    return root / FRESH_B4 if fresh_b4 else root


def fresh_worker_image(output, frozen, *, expanded_routes=False):
    if frozen is not None:
        image = frozen["worker_image"]
        verify_image(image)
        return image

    # Rebuilding identical layers can still create a new provenance attestation.
    receipt_name = (
        f"worker-image-{fingerprint(candidate())}.json" if expanded_routes else "worker-image.json"
    )
    receipt = study_directory(True, expanded_routes=expanded_routes) / receipt_name
    if receipt.is_file():
        image = read(receipt)["worker_image"]
        verify_image(image)
        return image

    image = build_image(output)
    verify_image(image)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open("x", encoding="utf-8") as stream:
        identity = {"worker_image": image}
        if expanded_routes:
            identity["candidate"] = candidate()
        json.dump(identity, stream)
    return image


def consume_reservation(output, frozen, *, fresh_b4, expanded_routes=False):
    freeze_hash = fingerprint(frozen)
    name = (
        "reserved-claim.json"
        if fresh_b4 or expanded_routes
        else f"reserved-claim-{freeze_hash}.json"
    )
    path = study_directory(fresh_b4, expanded_routes=expanded_routes) / name
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "consumed": True,
                "freeze_sha256": freeze_hash,
                "output": output.relative_to(ROOT).as_posix(),
                "started_real_life_s": time.time(),
            },
            stream,
            indent=2,
        )
    return path.relative_to(ROOT).as_posix()


def run(output, *, case, fresh_b4=False, expanded_routes=False):
    from qualify_restarts import monitor

    partition = case.removeprefix("study-")
    before = candidate()
    project = "evdt-e2c-" + hashlib.sha256(output.name.encode()).hexdigest()[:12]
    report = {
        "version": "route-aware-study/2.0.1",
        "partition": partition,
        "passed": False,
        "cleanup_verified": False,
        "candidate": before,
        "trips": [],
    }
    strict_study = fresh_b4 or expanded_routes
    if strict_study:
        report["study_id"] = EXPANDED_ROUTES if expanded_routes else FRESH_B4
    owner = str(uuid4())
    claim = None
    original_project = os.environ.get("EVDT_ENERGY_RESOURCE_PROJECT")
    os.environ["EVDT_ENERGY_RESOURCE_PROJECT"] = project
    try:
        selected = recipes(partition, fresh_b4=fresh_b4, expanded_routes=expanded_routes)
        frozen = None
        if partition == "reserved":
            frozen = read(
                study_directory(fresh_b4, expanded_routes=expanded_routes) / "freeze.json"
            )
            if frozen["candidate"] != before or (
                strict_study
                and (
                    frozen["study_id"] != report["study_id"]
                    or frozen["guarded_source"] != read(output / "source.json")
                    or (output / "reference-reuse.json").exists()
                )
            ):
                raise ValueError("Reserved candidate differs from frozen source/configuration")
        with monitor(
            project, output, allow_replacement=True, finite_services=("energy-worker", "reference")
        ):
            image = (
                fresh_worker_image(output, frozen, expanded_routes=expanded_routes)
                if strict_study
                else build_image(output)
            )
            report["worker_image"] = image
            pinned_reference = None
            if frozen is not None:
                if strict_study:
                    pinned_reference = frozen["reference_image"]
                    subprocess.check_output(
                        ["docker", "image", "inspect", pinned_reference], timeout=10
                    )
                report["reserved_claim"] = consume_reservation(
                    output, frozen, fresh_b4=fresh_b4, expanded_routes=expanded_routes
                )
            with (output / "reference.log").open("xb") as log:
                for recipe in selected:
                    folder = output / recipe["id"]
                    folder.mkdir()
                    print("Independent reference:", recipe["id"], flush=True)
                    reused = reuse_reference(output, folder, recipe)
                    truth, reference_image = (
                        reused
                        if reused is not None
                        else reference(folder, project, recipe, log, image=pinned_reference)
                    )
                    trip = {
                        "id": recipe["id"],
                        "family": recipe["family"],
                        "reference_image": reference_image,
                        "reference_sha256": hashlib.sha256(
                            (folder / "reference.json").read_bytes()
                        ).hexdigest(),
                        "duration_working_s": truth["rows"][-1]["working_s"],
                        "forecasts": [],
                    }
                    report["trips"].append(trip)
                    for row in truth["rows"]:
                        validate_progress(row, truth["stops"])
                        if "interval_minimum_soc" not in row:
                            raise ValueError("Reference lacks full-step minimum-charge accounting")
                    indices = cutoffs(truth["rows"])
                    if strict_study and len(indices) != 7:
                        raise ValueError(f"Expected seven distinct cutoffs for {recipe['id']}")
                    for index in indices:
                        number = sum(len(item["forecasts"]) for item in report["trips"]) + 1
                        request = projected_request(
                            folder, recipe, truth, truth["rows"][index], number
                        )
                        tick = time.monotonic()
                        claim = Claim(number, owner, request, tick, tick + 30)
                        result = run_isolated(image, claim, deadline_monotonic_s=tick + 30)
                        elapsed = time.monotonic() - tick
                        (folder / f"input-{index}.json").write_bytes(
                            canonical_bytes(request) + b"\n"
                        )
                        (folder / f"result-{index}.json").write_bytes(
                            canonical_bytes(result) + b"\n"
                        )
                        trip["forecasts"].append(
                            score(request, result, truth["rows"], index, elapsed)
                        )
                        claim = None
                        write(output / "study-progress.json", report)
            report["metrics"] = metrics(report["trips"])
            report["passed"] = report["metrics"]["passed"] and candidate() == before
    except (Exception, KeyboardInterrupt) as exc:
        report.update(passed=False, error=repr(exc))
    finally:
        try:
            if claim:
                remove_owned(claim.fence, claim.owner)
            leftovers = subprocess.check_output(
                ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
                timeout=10,
            )
            if leftovers.strip():
                raise RuntimeError("Study-owned resources remain; cleanup review required")
            report["cleanup_verified"] = True
        except Exception as exc:
            report.update(passed=False, cleanup_error=repr(exc))
        if original_project is None:
            os.environ.pop("EVDT_ENERGY_RESOURCE_PROJECT", None)
        else:
            os.environ["EVDT_ENERGY_RESOURCE_PROJECT"] = original_project
        write(output / "study-result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "inspect"])
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--development", type=Path)
    parser.add_argument("--result", type=Path)
    study = parser.add_mutually_exclusive_group()
    study.add_argument("--fresh-b4", action="store_true")
    study.add_argument("--expanded-routes", action="store_true")
    args = parser.parse_args()
    if args.action == "inspect":
        result = read(ROOT / args.result / "study-result.json")
        print(
            json.dumps(
                {
                    key: result.get(key)
                    for key in (
                        "study_id",
                        "partition",
                        "passed",
                        "cleanup_verified",
                        "metrics",
                        "error",
                    )
                },
                indent=2,
            )
        )
        return
    records = {}
    frozen = {"version": "route-aware-freeze/2.0.0", "candidate": candidate()}
    reference_images, worker_images = set(), set()
    strict_study = args.fresh_b4 or args.expanded_routes
    if strict_study:
        from qualify import source

        frozen.update(
            study_id=EXPANDED_ROUTES if args.expanded_routes else FRESH_B4, guarded_source=source()
        )
    for name, folder in (("development", args.development), ("validation", args.validation)):
        path = (ROOT / folder / "study-result.json").resolve()
        if not path.is_relative_to(ROOT / "evidence/energy-v2"):
            raise ValueError("Use a retained private study result")
        result = read(path)
        guard_path = path.parent / "qualification.json"
        check_freeze_evidence(
            result,
            read(guard_path),
            name,
            frozen["candidate"],
            fresh_b4=args.fresh_b4,
            expanded_routes=args.expanded_routes,
        )
        if strict_study:
            if read(path.parent / "source.json") != frozen["guarded_source"]:
                raise ValueError("Guarded source changed since the study runs")
            reference_images.update(trip["reference_image"] for trip in result["trips"])
            worker_images.add(result["worker_image"])
        records[name] = {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "guarded_qualification_sha256": hashlib.sha256(guard_path.read_bytes()).hexdigest(),
        }
    if strict_study:
        if len(reference_images) != 1 or len(worker_images) != 1:
            raise ValueError("Fresh B4 requires one verified image for each role")
        frozen.update(
            version="route-aware-freeze/2.0.1",
            reference_image=next(iter(reference_images)),
            worker_image=next(iter(worker_images)),
        )
    frozen.update(evidence=records, created_real_life_s=time.time())
    folder = study_directory(args.fresh_b4, expanded_routes=args.expanded_routes)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "freeze.json").open("x", encoding="utf-8") as stream:
        json.dump(frozen, stream, indent=2)
    print("Candidate frozen. Reserved outcomes remain unconsumed.")


if __name__ == "__main__":
    main()
