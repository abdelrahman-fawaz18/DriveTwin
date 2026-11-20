"""Compare three policies on the same simulator charger-power timeline."""

import argparse
from pathlib import Path

from evdt.config import ROOT
from evdt.energy.station_arrival import RESERVE_KWH
from scripts.charging.collect_station_arrivals import DESIGN, VEHICLE, digest
from scripts.charging.qualify_station_arrival_forecast import REFERENCE, RELEASE, load, write
from scripts.charging.run_charging_advisor import inputs, replay
from scripts.charging.run_charging_missions import SEED, STATIONFINDER, run

DEPARTURE_S = 60
ARMS = ("advisor", "nearest", "stationfinder")
# Times are simulation seconds. Only the world driver receives this schedule.
CASES = {
    "departure-outage": {20: 0, 40: 1, 50: 0, 750: 1},
    "in-service-outage": {150: 0, 750: 1},
}


def exercise(output, *, matched_departure=False):
    net, mission, catalog, bus, release = inputs()
    network = (ROOT / load(REFERENCE)["private_report"]).parent / "network.net.xml"
    recipe = ROOT / "qualification/charging/advisory-scenario-1.0.0.json"
    sources = (
        Path(__file__),
        ROOT / "scripts/charging/run_charging_missions.py",
        RELEASE,
        recipe,
        ROOT / "scripts/charging/run_charging_advisor.py",
        ROOT / "src/evdt/energy/charging_advisor.py",
    )
    before = {str(path.relative_to(ROOT)): digest(path) for path in sources}
    energy = load(ROOT / load(DESIGN)["scenario"] / "plant.json")["energy"]
    original = load(recipe)
    departure = original["decisions"][0]
    stations = catalog["stations"]
    ages = {row["station_id"]: row["status_max_age_sim_s"] for row in stations}
    depot_power = next(row["power_per_bay_w"] for row in stations if row["station_id"] == "depot-a")
    route = [
        net.getEdge(edge) for edge in mission["route_edges"][departure["departure_route_index"] :]
    ]
    reference = run(
        output / "reference",
        "no-charge-reference",
        network,
        route,
        departure,
        None,
        {},
        stations,
        bus,
        energy,
        mission["step_s"],
        departure_s=DEPARTURE_S,
    )
    outcomes = {}
    for case, schedule in CASES.items():
        (output / case).mkdir()
        outcomes[case] = {}
        for arm in ARMS:
            scenario = {**original, "decisions": [], "station_updates": []}

            def power_snapshot(conn):
                return {
                    name: conn.chargingstation.getChargingPower(name)
                    for name in sorted(conn.chargingstation.getIDList())
                }

            def sample(conn, now, energy_kwh):
                scenario["decisions"].append(
                    {
                        **departure,
                        "sim_s": now,
                        "observed_sim_s": now,
                        "initial_energy_kwh": energy_kwh,
                    }
                )
                for identity, power in power_snapshot(conn).items():
                    scenario["station_updates"].append(
                        {
                            "station_id": identity,
                            "observed_sim_s": now,
                            "available_sim_s": now,
                            "state": "open" if power > 0 else "outage",
                            "occupied_bays": 0,
                            "queued_vehicles": 0,
                            "expected_wait_s": 0,
                        }
                    )

            def world(conn, now, events):
                if now in schedule:
                    power = depot_power * schedule[now]
                    conn.chargingstation.setChargingPower("depot-a", power)
                    if conn.chargingstation.getChargingPower("depot-a") != power:
                        raise ValueError("Physical station power did not change")
                    events.append(
                        {
                            "event": "station_power",
                            "sim_s": now,
                            "station": "depot-a",
                            "power_w": power,
                        }
                    )
                if now == DEPARTURE_S:
                    events.append(
                        {
                            "event": "shared_departure_power",
                            "sim_s": now,
                            "stations_w": power_snapshot(conn),
                        }
                    )
                if not matched_departure and now <= DEPARTURE_S and now % 10 == 0:
                    sample(conn, now, departure["initial_energy_kwh"])

            def choose(conn, now, actual_wh, events):
                sample(conn, now, actual_wh / 1000)
                if matched_departure and (
                    len(scenario["decisions"]) != 1
                    or power_snapshot(conn)
                    != next(
                        e["stations_w"] for e in events if e["event"] == "shared_departure_power"
                    )
                ):
                    raise ValueError("Custom decision did not use just the shared departure state")
                records = replay(scenario, net, mission, catalog, bus, release)
                key = "recommended_station" if arm == "advisor" else "nearest_feasible_station"
                sequence = [record[key] for record in records]
                for record, station in zip(records, sequence, strict=True):
                    events.append(
                        {
                            "event": "parked_recommendation",
                            "sim_s": record["decision_sim_s"],
                            "station": station,
                        }
                    )
                path = output / case / arm / "station-decisions.json"
                write(path, {"inputs": scenario, "records": records})
                selected = sequence[-1]
                row = next(
                    (
                        candidate
                        for candidate in records[-1]["candidates"]
                        if candidate["station_id"] == selected
                    ),
                    None,
                )
                return ({**row, "station_max_age_s": ages[selected]} if row else None), {
                    "run_id": f"p4-dynamic-{case}-{arm}",
                    "vehicle_id": VEHICLE,
                    "release_sha256": digest(RELEASE),
                    "decision_sha256": digest(path),
                }

            result = run(
                output / case / arm,
                arm,
                network,
                route,
                departure,
                None,
                {},
                stations,
                bus,
                energy,
                mission["step_s"],
                departure_s=DEPARTURE_S,
                world=world,
                choose=None if arm == "stationfinder" else choose,
            )
            result["delay_vs_reference_s"] = (
                round(result["terminal_sim_s"] - reference["terminal_sim_s"], 3)
                if (result["completed"] and reference["completed"])
                else None
            )
            selected = [
                row["station"]
                for row in result["events"]
                if row["event"] == "parked_recommendation"
            ]
            result["parked_recommendation_changes"] = sum(
                a != b for a, b in zip(selected, selected[1:])
            )
            result["external_rescue_interventions"] = 0
            result["arm_settings"] = (
                STATIONFINDER if arm == "stationfinder" else {"mode": "fixture"}
            )
            write(output / case / arm / "comparison.json", result)
            outcomes[case][arm] = result
            print(f"{case}/{arm}: {result['terminal_reason']}", flush=True)

    evidence_complete = reference["completed"] and all(
        row["terminal_reason"] != "fixture_error"
        and all(
            field in row
            for field in (
                "minimum_kwh",
                "reserve_violation",
                "depleted",
                "charge_delivered_kwh",
                "replans",
                "terminal_sim_s",
            )
        )
        and [
            (item["sim_s"], item["power_w"])
            for item in row["events"]
            if item["event"] == "station_power"
        ]
        == [
            (t, depot_power * state)
            for t, state in CASES[case].items()
            if t < row["terminal_sim_s"]
        ]
        for case, arms in outcomes.items()
        for row in arms.values()
    )
    advisor = outcomes["departure-outage"]["advisor"]
    selections = [
        row["station"] for row in advisor["events"] if row["event"] == "parked_recommendation"
    ]
    bounded_parked_fallback = (
        evidence_complete
        and len(selections) == 8
        and selections[0] == "depot-a"
        and selections[2] not in (None, "depot-a")
        and selections[-1] != "depot-a"
        and advisor["parked_recommendation_changes"] == 1
        and advisor["completed"]
        and advisor["target_reached"]
        and not advisor["reserve_violation"]
    )
    shared_start = all(
        len(witnesses) == len(ARMS)
        and witnesses[1:] == witnesses[:-1]
        and witnesses[0]["sim_s"] == DEPARTURE_S
        and witnesses[0]["stations_w"]
        == {
            row["station_id"]: (
                0
                if case == "departure-outage" and row["station_id"] == "depot-a"
                else row["power_per_bay_w"]
            )
            for row in stations
        }
        for case, arms in outcomes.items()
        for witnesses in [
            [
                event
                for row in arms.values()
                for event in row["events"]
                if event["event"] == "shared_departure_power"
            ]
        ]
    )
    matched_start_passed = (
        evidence_complete
        and shared_start
        and len(selections) == 1
        and selections[0] not in (None, "depot-a")
        and advisor["completed"]
        and advisor["target_reached"]
        and not advisor["reserve_violation"]
    )
    if before != {str(path.relative_to(ROOT)): digest(path) for path in sources}:
        raise ValueError("Dynamic comparison source changed during measurement")
    record = {
        "passed_for_measured_fixture": matched_start_passed
        if matched_departure
        else bounded_parked_fallback,
        "comparison_mode": "common-departure-only" if matched_departure else "parked-history",
        "shared_departure_power_verified": shared_start,
        "evidence_complete": evidence_complete,
        "bounded_parked_fallback": None if matched_departure else bounded_parked_fallback,
        "network_sha256": digest(network),
        "seed": SEED,
        "departure_sim_s": DEPARTURE_S,
        "step_s": mission["step_s"],
        "reserve_kwh": RESERVE_KWH,
        "power_cases": CASES,
        "reference_terminal_sim_s": reference.get("terminal_sim_s"),
        "outcomes": outcomes,
        "comparison_limits": [
            "Same physical charger power, empty stations, battery, reserve guard, clock and network.",
            "Native Stationfinder has its own search timing, internal state and charge objective.",
            "Advisor and nearest act at departure. No moving-bus forecasting or actuation is qualified.",
            "Does not compare queue or delayed-status feeds; charging availability is physical power.",
        ],
        "source_sha256": before,
    }
    write(output / "qualification.json", record)
    return record["passed_for_measured_fixture"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--matched-departure",
        action="store_true",
        help="Give the custom arms only the physical state at departure",
    )
    args = parser.parse_args()
    args.output.mkdir()
    return 0 if exercise(args.output, matched_departure=args.matched_departure) else 1


if __name__ == "__main__":
    raise SystemExit(main())
