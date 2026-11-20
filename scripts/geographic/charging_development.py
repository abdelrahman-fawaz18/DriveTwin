"""Run offline Charging Advisor development cases on retained real-road inputs."""

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evdt.energy.moving_charge_candidate import OnRouteStop, VisitPolicy  # noqa: E402
from evdt.energy.moving_charge_search import plan_charge  # noqa: E402
from evdt.energy.moving_charge_station import (  # noqa: E402
    STATION_VERSION,
    STATUS_VERSION,
    StationBay,
    StationStatus,
)
from evdt.energy.route_aware.contracts import Curve, CurvePoint, Origin  # noqa: E402
from evdt.energy.route_aware.forecast import forecast  # noqa: E402
from evdt.energy.route_aware.geometry import reference_length  # noqa: E402
from evdt.energy.route_aware.wire import canonical_bytes, parse_request  # noqa: E402

ROAD_CLASSES = {
    "highway.residential",
    "highway.unclassified",
    "highway.service",
    "highway.tertiary",
    "highway.secondary",
    "highway.busway",
}


def development_offers(request):
    eligible = []
    distance = 0.0
    for road in request.route.occurrences[request.state.occurrence_index :]:
        length = reference_length(road)
        if (
            request.state.occurrence_index < road.index < len(request.route.occurrences) - 1
            and road.road_class in ROAD_CLASSES
            and road.structure == "surface"
            and road.geometry_basis == "lane_centerline"
            and road.speed_limit_m_s <= 16.67
            and not road.stops
            and length >= 45
        ):
            eligible.append((road, distance + length / 2))
        distance += length

    # Choose one early and one later lane using geometry alone. The site
    # locations, power, and queues are declared laboratory inputs on real roads.
    selected = {}
    for fraction in (0.10, 0.35):
        options = [item for item in eligible if item[0].lane_id not in selected]
        if options:
            road, _ = min(options, key=lambda item: abs(item[1] - fraction * distance))
            selected[road.lane_id] = road
    clock = request.state.working_s
    wall = request.requested_real_life_s
    offers = []
    for index, road in enumerate(sorted(selected.values(), key=lambda road: road.index)):
        end = reference_length(road) / 2 + 7.5
        bay = StationBay(
            STATION_VERSION,
            f"development-site-{index + 1}",
            f"bay-{index + 1}",
            "simulated-dc",
            15,
            road.lane_id,
            end - 15,
            end,
            (120_000, 180_000)[index],
            0.93,
            10,
            request.route.network_sha256,
            "Laboratory stop on a bus lane; separate station access review required",
            f"OpenStreetMap road occurrence {road.index}: {road.edge_id}",
        )
        offers.append(
            (
                OnRouteStop(bay, road.index, end),
                StationStatus(
                    STATUS_VERSION,
                    bay.bay_id,
                    "geographic-development",
                    1,
                    wall,
                    wall,
                    clock,
                    "occupied" if index == 0 else "available",
                    clock + request.policy.max_plan_working_s,
                    clock + 360 if index == 0 else clock,
                    bay.power_w,
                ),
            )
        )
    return tuple(offers)


def run_case(path):
    source = path.read_bytes()
    original = parse_request(source)
    baseline = forecast(original)
    if baseline.summary is None or baseline.summary.arrival_soc is None:
        raise ValueError("A feasible historical geographic baseline is required")
    consumption = max(0.01, original.state.soc - baseline.summary.minimum_soc)
    floor = original.vehicle.reserve_soc
    # Deliberately alter the charge input to create a controlled reserve challenge.
    charge = floor + consumption * 0.70
    scenario = replace(original, state=replace(original.state, soc=charge))
    offers = development_offers(scenario)
    equipment = original.state.auxiliary_power_w
    curve = Curve(
        "1",
        "W",
        (CurvePoint(0, 190_000), CurvePoint(0.8, 190_000), CurvePoint(1, 40_000)),
        Origin("declared", "SUMO development station profile", "Uncalibrated charge curve"),
    )
    policy = VisitPolicy(0, equipment, curve, 5, "simulated-dc", 12)
    levels = tuple(sorted({min(0.98, floor + consumption * scale) for scale in (0.75, 1.1, 1.5)}))
    selection = plan_charge(
        scenario,
        offers,
        policy=policy,
        target_soc_levels=levels,
        station_scenario_id="geographic-development",
        now_real_life_s=scenario.requested_real_life_s,
        max_candidate_jobs=12,
        budget_real_life_s=25,
    )
    default = plan_charge(
        original,
        offers,
        policy=policy,
        target_soc_levels=levels,
        station_scenario_id="geographic-development",
        now_real_life_s=original.requested_real_life_s,
        max_candidate_jobs=12,
        budget_real_life_s=25,
    )
    return {
        "original_request_sha256": hashlib.sha256(source).hexdigest(),
        "declared_charge_soc": charge,
        "declared_offer_count": len(offers),
        "declared_target_levels": levels,
        "preset_decision": json.loads(canonical_bytes(default)),
        "detailed_decision": json.loads(canonical_bytes(selection)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference = args.reference.resolve(strict=True)
    output = args.output.resolve()
    if not reference.is_relative_to(ROOT / "evidence/energy-v2") or not output.is_relative_to(
        ROOT / "evidence/energy-v2"
    ):
        parser.error("Use retained local evidence and a fresh output under evidence/energy-v2")
    summary = json.loads((reference / "study-result.json").read_bytes())
    if not summary["passed"] or not summary["cleanup_verified"]:
        raise ValueError("Completed geographic reference evidence is required")
    paths = sorted(reference.glob("journey-*/input-0.json"))
    journeys = json.loads((reference / "journey.json").read_bytes())
    expected = {f"journey-{index:02d}" for index in range(1, len(journeys) + 1)}
    if not expected or {path.parent.name for path in paths} != expected:
        raise ValueError("Retained departure-request inventory differs from the study")
    output.mkdir()
    for path in paths:
        result = run_case(path)
        (output / f"{path.parent.name}.json").write_bytes(canonical_bytes(result) + b"\n")
        print(
            f"{path.parent.name}: {result['detailed_decision']['status']}"
            f" from {result['declared_offer_count']} declared bays"
            f"; preset {result['preset_decision']['status']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
