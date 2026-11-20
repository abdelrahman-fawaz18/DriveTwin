"""Prepare a declared local station bundle on a reviewed geographic route."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts/launcher")]

from charging_development import development_offers  # noqa: E402
from live_inputs import digest, local_path, read  # noqa: E402

from catalog import load, select  # noqa: E402
from evdt.energy.moving_charge_assignment import AssignedBay, StationAssignment  # noqa: E402
from evdt.energy.moving_charge_candidate import VisitPolicy  # noqa: E402
from evdt.energy.moving_charge_scenario import BayInterval, BaySchedule, StationScenario  # noqa: E402
from evdt.energy.route_aware.contracts import Curve, CurvePoint, Origin  # noqa: E402
from evdt.energy.route_aware.sumo_routes import read_network  # noqa: E402
from evdt.energy.route_aware.wire import canonical_bytes, parse_request  # noqa: E402


def prepare_template(profile, request_file, levels, *, first_bay_wait_s):
    reference = ROOT / profile["reference"]
    data = read(reference / "run-input.json")
    edges = tuple(data["mission"]["route_edges"])
    request = parse_request(request_file.read_bytes())
    network_meta = read(ROOT / profile["prepared"] / "geometry.json")["network"]
    reference_network = reference / "reference-network.net.xml"
    if (
        request.route.network_sha256 != network_meta["sha256"]
        or digest(reference_network)
        != profile["inputs"][reference_network.relative_to(ROOT).as_posix()]
        or request.state.scope.asset_id != "bus-001"
        or request.state.occurrence_index != 0
        or request.state.working_s > 1
    ):
        raise ValueError("A matching retained departure observation is required")
    occurrences = [road for road in request.route.occurrences if not road.edge_id.startswith(":")]
    if tuple(road.edge_id for road in occurrences) != edges:
        raise ValueError("Departure observation has a different ordered road sequence")
    indices = {road.index: n for n, road in enumerate(occurrences)}
    mandatory = {stop["external_index"] for stop in data["stops"]["stops"]}
    visits = Counter(edges)
    network = read_network(reference_network.read_bytes(), max_bytes=32 * 1024**2)
    bays, schedules = [], []
    if not 0 <= first_bay_wait_s < request.policy.max_plan_working_s:
        raise ValueError("The declared wait is outside the prepared journey horizon")
    if any(not request.vehicle.reserve_soc <= target <= 1 for target in levels):
        raise ValueError("Each target must lie between the vehicle reserve and full charge")
    for stop, _ in development_offers(request):
        index = indices[stop.occurrence_index]
        bay = stop.bay
        lane = network.lanes.get(bay.lane_id)
        if (
            lane is None
            or lane.edge_id != edges[index]
            or visits[lane.edge_id] != 1
            or index in mandatory
            or bay.end_offset_m >= lane.length_m
            or bay.platform_length_m < 12
        ):
            raise ValueError("Review and move the simulation bay to an unambiguous bus lane")
        bays.append(AssignedBay(bay, index))
        opening = first_bay_wait_s if len(bays) == 1 else 0
        intervals = (BayInterval(0, opening, "occupied", None),) if opening else ()
        schedules.append(
            BaySchedule(
                bay.bay_id,
                (
                    *intervals,
                    BayInterval(
                        opening, request.policy.max_plan_working_s, "available", bay.power_w
                    ),
                ),
            )
        )
    if not bays:
        raise ValueError("The prepared route has no eligible development bay")
    curve = Curve(
        "1",
        "W",
        (CurvePoint(0, 190_000), CurvePoint(0.8, 190_000), CurvePoint(1, 40_000)),
        Origin("declared", "Local SUMO charge curve", "Uncalibrated development profile"),
    )
    run = "00000000-0000-0000-0000-000000000000"
    assigned = StationAssignment(
        "moving-charge-assignment/1.0.0",
        run,
        "bus-001",
        request.route.network_sha256,
        edges,
        tuple(bays),
        StationScenario(profile["journey_id"] + "-charging-development", run, tuple(schedules)),
        VisitPolicy(0, data["recipe"]["auxiliary_w"], curve, 8, "simulated-dc", 12),
        levels,
        len(bays) * (1 + len(levels)),
        12,
    )
    template = json.loads(canonical_bytes(assigned))
    template["run_id"] = template["scenario"]["run_id"] = None
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trip", required=True)
    parser.add_argument(
        "--request",
        required=True,
        type=Path,
        help="Matching retained departure request from this journey's qualification",
    )
    parser.add_argument("--targets-percent", nargs="+", type=float, required=True)
    parser.add_argument("--first-bay-wait", type=int, default=360)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / ".private") or output.suffix != ".json":
        parser.error("Choose a fresh private assignment JSON inside the project")
    profile = select(load(), args.trip, ["energy"])
    request_file = local_path(args.request)
    result = prepare_template(
        profile,
        request_file,
        tuple(value / 100 for value in args.targets_percent),
        first_bay_wait_s=args.first_bay_wait,
    )
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(
        f"{len(result['bays'])} local simulated bays prepared. Review locations before attachment."
    )


if __name__ == "__main__":
    main()
