"""Develop, freeze, and score one reserved station-forecast evaluation."""

import argparse
import json
import math
import re
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from statistics import fmean

import sumolib
from traci.exceptions import FatalTraCIError, TraCIException

from evdt.config import ROOT
from evdt.energy.station_arrival import CAPACITY_KWH, JOULES_PER_KWH, StationApproach, forecast
from scripts.charging.collect_station_arrivals import DESIGN, approach_route, collect, digest

REFERENCE = ROOT / "docs/verification/station-arrival-references.json"
RELEASE = ROOT / "qualification/charging/station-arrival-release-1.0.0.json"
OUTPUT = Path("/output")
MODEL = ROOT / "src/evdt/energy/station_arrival.py"
TARGETS = {
    "count": 6,
    "arrival_mae_kwh": 0.9,
    "minimum_mae_kwh": 0.9,
    "max_absolute_error_kwh": 1.5,
    "max_optimistic_minimum_error_kwh": 1.2,
    "arrival_soc_mae_pp": 0.26,
    "max_interval_width_kwh": 3.5,
    "minimum_joint_coverage_count": 5,
    "minimum_correct_feasible_count": 2,
    "maximum_false_exclusions": 1,
}


def load(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def reference():
    recorded = load(REFERENCE)
    path = ROOT / recorded["private_report"]
    if not recorded["passed"] or digest(path) != recorded["private_report_sha256"]:
        raise ValueError("The development reference report has changed")
    data = load(path)
    if (
        not data["passed"]
        or data["partition"] != "development"
        or data["reserved_collected"]
        or data["source_sha256"] != recorded["source_sha256"]
        or any(digest(ROOT / name) != sha for name, sha in data["source_sha256"].items())
    ):
        raise ValueError("The reference scope or source inventory has changed")
    for row in data["drives"]:
        folder = path.parent / row["case"]["id"]
        if (
            digest(folder / "plant-trace.jsonl") != row["trace_sha256"]
            or digest(folder / "approach.rou.xml") != row["route_sha256"]
        ):
            raise ValueError("A development trace or route has changed")
    network = path.parent / "network.net.xml"
    if digest(network) != data["network_sha256"]:
        raise ValueError("The archived network has changed")
    return path, network, data


def make_approach(net, drive, stop, bus):
    route = approach_route(net, drive, stop, bus["length"])
    selected, distance = net.getShortestPath(
        route[0],
        route[-1],
        vClass="bus",
        fromPos=drive["departure_m"],
        toPos=stop["end_m"],
    )
    if selected != route:
        raise ValueError("Inconsistent directed approach")
    lengths = [edge.getLength() for edge in route]
    lengths[0] -= drive["departure_m"]
    lengths[-1] -= route[-1].getLength() - stop["end_m"]
    connector_m = distance - sum(lengths)
    if min(lengths) < 0 or connector_m < -1e-6:
        raise ValueError("Route length does not match its planned edge sequence")
    free_flow_s = max(0.0, connector_m) / 8 + sum(
        length / min(edge.getSpeed(), bus["maximum_speed"])
        for length, edge in zip(lengths, route, strict=True)
    )
    signals = sum(
        any(
            connection.getTLSID()
            and connection.getFromLane().allows("bus")
            and connection.getToLane().allows("bus")
            for connection in before.getConnections(after)
        )
        for before, after in zip(route, route[1:])
    )
    approach = StationApproach(
        initial_energy_kwh=bus["usable_battery_energy"] * drive["initial_soc"],
        auxiliary_power_w=drive["auxiliary_power_w"],
        distance_m=distance,
        free_flow_s=free_flow_s,
        traffic_signals=signals,
        expected_wait_s=drive["wait_s"],
    )
    return route, approach


def predict(drives, routes, model):
    predictions = []
    for drive in drives:
        identity = drive["id"]
        route, approach = routes[identity]
        row = {
            "id": identity,
            "station": drive["station"],
            "departure_edge": drive["departure_edge"],
            "departure_m": drive["departure_m"],
            "departure_sim_s": 0,
            "input_source": "declared route and synthetic departure fixture",
            "route_edges": [edge.getID() for edge in route],
            "input": asdict(approach),
        }
        for name in ("baseline", "candidate"):
            row[name] = forecast(
                approach,
                traction_kwh_per_km=model[name]["traction_kwh_per_km"],
                route_aware=name == "candidate",
                radius_kwh=model[name]["radius_kwh"],
            )
        predictions.append(row)
    return predictions


def score(predictions, outcomes, name):
    rows = []
    for prediction in predictions:
        identity = prediction["id"]
        outcome = outcomes.get(identity)
        if outcome is None or "failure" in outcome:
            rows.append({"id": identity, "available": False})
            continue
        point = prediction[name]
        arrival = outcome["arrival_energy_kwh"]
        minimum = outcome["minimum_through_wait_energy_kwh"]
        rows.append(
            {
                "id": identity,
                "available": True,
                "arrival_error_kwh": point["arrival_kwh"] - arrival,
                "minimum_error_kwh": point["minimum_kwh"] - minimum,
                "arrival_soc_error_pp": point["arrival_soc_pct"] - 100 * outcome["arrival_soc"],
                "joint_interval_covered": (
                    point["arrival_interval_kwh"][0] <= arrival <= point["arrival_interval_kwh"][1]
                    and point["minimum_interval_kwh"][0]
                    <= minimum
                    <= point["minimum_interval_kwh"][1]
                ),
                "interval_width_kwh": point["minimum_interval_kwh"][1]
                - point["minimum_interval_kwh"][0],
                "predicted_feasible": point["reserve_feasible"],
                "measured_feasible": outcome["reserve_maintained"],
            }
        )
    available = [row for row in rows if row["available"]]
    metrics = {"available": len(available)}
    if available:
        metrics.update(
            arrival_mae_kwh=fmean(abs(row["arrival_error_kwh"]) for row in available),
            minimum_mae_kwh=fmean(abs(row["minimum_error_kwh"]) for row in available),
            max_absolute_error_kwh=max(
                max(abs(row["arrival_error_kwh"]), abs(row["minimum_error_kwh"]))
                for row in available
            ),
            max_optimistic_minimum_error_kwh=max(
                0.0, *(row["minimum_error_kwh"] for row in available)
            ),
            arrival_soc_mae_pp=fmean(abs(row["arrival_soc_error_pp"]) for row in available),
            max_interval_width_kwh=max(row["interval_width_kwh"] for row in available),
            joint_coverage_count=sum(row["joint_interval_covered"] for row in available),
            correct_feasible_count=sum(
                row["predicted_feasible"] and row["measured_feasible"] for row in available
            ),
            false_feasible_count=sum(
                row["predicted_feasible"] and not row["measured_feasible"] for row in available
            ),
            false_exclusion_count=sum(
                not row["predicted_feasible"] and row["measured_feasible"] for row in available
            ),
        )
    return {"metrics": metrics, "cases": rows}


def passes(result, targets):
    metrics = result["metrics"]
    return (
        metrics["available"] == targets["count"]
        and all(
            metrics[key] <= targets[key] + 1e-12
            for key in (
                "arrival_mae_kwh",
                "minimum_mae_kwh",
                "max_absolute_error_kwh",
                "max_optimistic_minimum_error_kwh",
                "arrival_soc_mae_pp",
                "max_interval_width_kwh",
            )
        )
        and metrics["joint_coverage_count"] >= targets["minimum_joint_coverage_count"]
        and metrics["correct_feasible_count"] >= targets["minimum_correct_feasible_count"]
        and metrics["false_feasible_count"] == 0
        and metrics["false_exclusion_count"] <= targets["maximum_false_exclusions"]
    )


def develop(drives, routes, outcomes):
    model = {}
    for name in ("baseline", "candidate"):
        route_aware = name == "candidate"
        distances = []
        traction = []
        for drive in drives:
            identity = drive["id"]
            approach = routes[identity][1]
            distances.append(approach.distance_m / 1000)
            auxiliary = approach.auxiliary_power_w * approach.drive_time_s(route_aware)
            traction.append(
                approach.initial_energy_kwh
                - outcomes[identity]["arrival_energy_kwh"]
                - auxiliary / JOULES_PER_KWH
            )
        rate = sum(x * y for x, y in zip(distances, traction, strict=True)) / sum(
            distance**2 for distance in distances
        )
        errors = []
        for drive in drives:
            identity = drive["id"]
            point = forecast(routes[identity][1], traction_kwh_per_km=rate, route_aware=route_aware)
            errors.extend(
                (
                    abs(point["arrival_kwh"] - outcomes[identity]["arrival_energy_kwh"]),
                    abs(
                        point["minimum_kwh"] - outcomes[identity]["minimum_through_wait_energy_kwh"]
                    ),
                )
            )
        model[name] = {
            "traction_kwh_per_km": rate,
            "radius_kwh": math.ceil(10 * (max(errors) + 0.95)) / 10,
        }
    predictions = predict(drives, routes, model)
    comparisons = {name: score(predictions, outcomes, name) for name in model}
    return model, comparisons


def run_reserved(output, release, drives, routes, stations, network, bus, energy, step_s):
    predictions = predict(drives, routes, release["model"])
    claim = OUTPUT / f"station-arrival-reserved-{digest(DESIGN)}.json"
    write(claim, {"release_sha256": digest(RELEASE), "output": output.name})
    prediction_path = output / "predictions.json"
    write(prediction_path, predictions)
    prediction_sha = digest(prediction_path)
    write(
        output / "prediction-seal.json",
        {
            "release_sha256": digest(RELEASE),
            "predictions_sha256": prediction_sha,
        },
    )

    outcomes = {}
    for drive in drives:
        identity = drive["id"]
        try:
            outcome = collect(
                drive,
                stations[drive["station"]],
                routes[identity][0],
                release["domain"],
                network,
                bus,
                energy,
                step_s,
                output / identity,
            )
            if outcome["charge_received_wh"] != 0:
                raise ValueError("Reserved drive received unexpected charger energy")
            outcome["reserve_maintained"] = (
                outcome["minimum_through_wait_energy_kwh"] >= release["reserve_kwh"]
            )
        except (ValueError, RuntimeError, OSError, TraCIException, FatalTraCIError) as exc:
            outcome = {"failure": str(exc)}
        outcomes[identity] = outcome
        write(output / (identity + "-result.json"), outcome)
        print(f"Recorded {identity}", flush=True)
    if digest(prediction_path) != prediction_sha:
        raise ValueError("Predictions changed after being sealed")
    comparisons = {name: score(predictions, outcomes, name) for name in release["model"]}
    result = {
        "passed": passes(comparisons["candidate"], release["targets"]),
        "release_sha256": digest(RELEASE),
        "predictions_sha256": prediction_sha,
        "outcomes": outcomes,
        "comparisons": comparisons,
    }
    write(output / "qualification.json", result)
    return result["passed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("develop", "reserved"))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+", args.run_id) or not OUTPUT.samefile(ROOT / "evidence/p4"):
        parser.error("Use a simple run ID and the canonical /output evidence mount")
    output = OUTPUT / args.run_id
    design = load(DESIGN)
    if version("eclipse-sumo") != design["domain"]["simulator_version"]:
        raise ValueError("Simulator package differs from the drive design")
    scenario = ROOT / design["scenario"]
    bus = {
        key: item["value"]
        for key, item in load(scenario / "bus-parameters.json")["parameters"].items()
    }
    energy = load(scenario / "plant.json")["energy"]
    stations = {
        row["station_id"]: row["approach"] for row in load(ROOT / design["catalog"])["stations"]
    }
    development_path, network, data = reference()
    net = sumolib.net.readNet(str(network), withInternal=True)
    development = [drive for drive in design["drives"] if drive["partition"] == "development"]
    reserved = [drive for drive in design["drives"] if drive["partition"] == "reserved"]
    if (
        [row["case"] for row in data["drives"]] != development
        or [drive["id"] for drive in reserved] != data["reserved_drive_ids"]
        or bus["usable_battery_energy"] != CAPACITY_KWH
    ):
        raise ValueError("Design and preserved development inventory differ")
    routes = {
        drive["id"]: make_approach(net, drive, stations[drive["station"]], bus)
        for drive in development
    }
    outcomes = {row["case"]["id"]: row for row in data["drives"]}
    model, comparisons = develop(development, routes, outcomes)
    if not passes(comparisons["candidate"], TARGETS):
        raise ValueError(
            f"Development candidate misses predeclared targets: {comparisons['candidate']['metrics']}"
        )
    release = {
        "version": "station-arrival-release/1.0.0",
        "model": model,
        "targets": TARGETS,
        "domain": design["domain"],
        "reserve_kwh": data["reserve_kwh"],
        "reserved_ids": data["reserved_drive_ids"],
        "input_fields": list(StationApproach.__dataclass_fields__),
        "development_report_sha256": digest(development_path),
        "network_sha256": digest(network),
        "source_sha256": {
            **data["source_sha256"],
            **{
                path.relative_to(ROOT).as_posix(): digest(path)
                for path in (MODEL, Path(__file__), REFERENCE)
            },
        },
        "development_metrics": {name: result["metrics"] for name, result in comparisons.items()},
    }
    if args.mode == "reserved" and load(RELEASE) != release:
        raise ValueError(
            "Current implementation, development inputs or targets differ from the frozen release"
        )
    output.mkdir()
    if args.mode == "develop":
        write(output / "release.json", release)
        write(output / "development-comparison.json", comparisons)
        print(json.dumps(release["development_metrics"], indent=2))
        return 0
    routes = {
        drive["id"]: make_approach(net, drive, stations[drive["station"]], bus)
        for drive in reserved
    }
    step_s = load(scenario / "mission.json")["step_s"]
    passed = run_reserved(output, release, reserved, routes, stations, network, bus, energy, step_s)
    print(f"Reserved evaluation passed: {passed}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
