"""Produce SUMO observations and exercise the authenticated charging handoff."""

import argparse
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evdt.config import ROOT
from scripts.charging.automatic_charging_cases import AUTO_CASES, exercise
from scripts.charging.collect_station_arrivals import DESIGN, VEHICLE, digest
from scripts.charging.qualify_station_arrival_forecast import REFERENCE, load, write
from scripts.charging.run_charging_advisor import inputs
from scripts.charging.run_charging_missions import run


def send(base, route, token, value=None, expected=200):
    request = Request(
        base + route,
        data=None if value is None else json.dumps(value, allow_nan=False).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as reply:
            status, payload = reply.status, json.load(reply)
    except HTTPError as exc:
        status, payload = exc.code, json.load(exc)
    if status != expected:
        raise RuntimeError(f"{route}: expected {expected}, got {status} {payload}")
    return payload


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interrupt", action="store_true")
    parser.add_argument("--automatic-case", choices=AUTO_CASES)
    args = parser.parse_args()
    net, mission, catalog, bus, _ = inputs()
    scenario_path = ROOT / load(DESIGN)["scenario"]
    energy = load(scenario_path / "plant.json")["energy"]
    original = load(ROOT / "qualification/charging/advisory-scenario-1.0.0.json")
    decision = original["decisions"][0]
    policy = load(Path("/fixture-secrets/run_policy.json"))
    source = policy["sources"][0]
    scope = {"run_id": policy["run_id"], "asset_id": source["asset_id"]}
    tokens = {
        role: (Path("/fixture-secrets") / name).read_text().strip()
        for role, name in {
            "operator": "command_operator",
            "executor": "command_executor",
            "observer": "command_observer",
            "station": "command_station_writer",
            "producer": "producer1",
            "reader": "charging_probe_reader",
        }.items()
    }
    command_id = str(uuid.uuid4())
    if args.automatic_case:
        tokens["policy"] = (
            (Path("/fixture-secrets") / "command_charging_policy").read_text().strip()
        )

    def call(path, role, value=None, status=200):
        return send("http://command-api:8000", "/v1/charging" + path, tokens[role], value, status)

    def handoff(connection, now, actual_wh, events):
        exclusive = send("http://command-api:8000", "/v1/commands", tokens["operator"], {}, 409)
        if exclusive["detail"] != "charging_profile_exclusive":
            raise ValueError("Another command actuator is enabled in the charging profile")
        simulator = connection.getVersion()
        expected = source["expected"]
        if (
            list(simulator) != [expected["traci_protocol"], expected["sumo_version"]]
            or connection.simulation.getDeltaT() != expected["step_s"]
            or connection.vehicle.getRoadID(VEHICLE)
            != mission["route_edges"][decision["departure_route_index"]]
            or connection.vehicle.getLanePosition(VEHICLE) != decision["departure_m"]
            or not connection.chargingstation.getIDList()
        ):
            raise ValueError("Actual departure and registered charging recipe differ")
        payload = {
            **expected,
            "manifest_version": "source-run/2.0.0",
            "mode": "simulation",
            "asset_id": scope["asset_id"],
            "identity": {
                **scope,
                "source_id": source["source_id"],
                "source_epoch": 1,
                "session_id": str(uuid.uuid4()),
            },
            "evidence_class": "assumed",
            "sampling": {"default_period_s": 1, "period_overrides_s": {}},
            "source_epoch_start_sim_s": 0,
            "minimum_sim_time_s": 0,
            "initial_state_sim_s": now,
            "battery_power_source": "SUMO battery charge and draw on completed steps",
            "insertion_policy": "Measure stationary SUMO state before any charging handoff",
            "driver_options_sha256": digest(args.output / "mission.rou.xml"),
        }
        departure = {
            **scope,
            "observation_id": str(uuid.uuid4()),
            "manifest_sha256": "0" * 64,
            "sim_s": now,
            "observed_at": timestamp(),
            **{
                key: value
                for key, value in decision.items()
                if key not in ("sim_s", "observed_sim_s")
            },
            "initial_energy_kwh": actual_wh / 1000,
            "speed_m_s": connection.vehicle.getSpeed(VEHICLE),
            "operator_override": False,
            "thermal_block": False,
            "local_protection_block": False,
        }
        denied = call("/departure", "observer", departure, 409)
        if denied["detail"] != "charging_source_unregistered":
            raise ValueError("Departure before registration was not rejected")
        manifest = send("http://registry:9000", "/v1/registrations", tokens["producer"], payload)
        departure.update(manifest_sha256=manifest["manifest_sha256"], observed_at=timestamp())
        stations = {
            **scope,
            "observation_id": str(uuid.uuid4()),
            "sim_s": now,
            "observed_at": timestamp(),
            "stations": [
                {
                    "station_id": name,
                    "observed_sim_s": now,
                    "state": "open"
                    if connection.chargingstation.getChargingPower(name) > 0
                    else "outage",
                    "occupied_bays": 0,
                    "queued_vehicles": 0,
                    "expected_wait_s": 0,
                }
                for name in connection.chargingstation.getIDList()
            ],
        }
        call("/departure", "executor", departure, 401)
        call("/stations", "observer", stations, 401)
        call("/recommendation", "reader", status=401)
        call("/stations", "station", {**stations, "run_id": str(uuid.uuid4())}, 403)
        for seconds in (-6, 6):
            time = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
            stale = call("/stations", "station", {**stations, "observed_at": time}, 409)
            if stale["detail"] != "charging_observation_stale_or_future":
                raise ValueError("Noncurrent station snapshot was not rejected")
        call("/stations", "station", stations)
        call("/departure", "observer", departure)
        recommendation = call("/recommendation", "operator")
        write(
            args.output / "registered-inputs.json",
            {
                "manifest": payload,
                "departure": departure,
                "stations": stations,
                "recommendation": recommendation,
                "unregistered_and_role_probes_passed": True,
            },
        )
        request = {
            **scope,
            "command_id": command_id,
            "decision_sha256": recommendation["decision_sha256"],
        }
        if args.automatic_case:
            return exercise(
                args.automatic_case,
                connection,
                args.output,
                call,
                scope,
                command_id,
                departure,
                stations,
                recommendation,
                timestamp,
                events,
            )
        not_approved = call(
            "/automatic/diversions", "operator", {**scope, "command_id": command_id}, 403
        )
        if not_approved["detail"] != "automatic_dispatch_not_approved":
            raise ValueError(
                "Default operator profile did not explicitly refuse automatic dispatch"
            )
        call("/diversions", "observer", request, 401)
        queued = call("/diversions", "operator", request)
        if call("/diversions", "operator", request) != queued:
            raise ValueError("Retry changed the admitted command")
        claim = {**scope, "command_id": command_id, "sim_s": now}
        call("/claims", "operator", claim, 401)
        if (
            call("/claims", "executor", {**claim, "sim_s": now + 1}, 409)["detail"]
            != "charging_expired_or_early"
        ):
            raise ValueError("Expired diversion was not rejected")
        reserved = call("/claims", "executor", claim)
        if (
            reserved != queued
            or call("/claims", "executor", claim, 409)["detail"] != "charging_already_reserved"
        ):
            raise ValueError("Reserved command was changed or handed out twice")
        write(args.output / "claim.json", claim)
        events.append({"event": "authenticated_claim", "correlation_id": command_id})
        return (None if args.interrupt else reserved["candidate"]), command_id

    route = [
        net.getEdge(edge) for edge in mission["route_edges"][decision["departure_route_index"] :]
    ]
    network = (ROOT / load(REFERENCE)["private_report"]).parent / "network.net.xml"
    result = run(
        args.output,
        "registered-automatic" if args.automatic_case else "registered-operator",
        network,
        route,
        decision,
        None,
        {},
        catalog["stations"],
        bus,
        energy,
        mission["step_s"],
        handoff=handoff,
    )
    if args.interrupt or args.automatic_case not in (None, "mission"):
        if result["terminal_reason"] != "handoff_interrupted" or result["route_changed"]:
            raise ValueError("Interrupted handoff did not preserve the original route")
    elif not (
        result["completed"] and result["target_reached"] and result["resumed_after_charging"]
    ):
        raise ValueError(f"Registered charging run did not complete: {result['terminal_reason']}")


if __name__ == "__main__":
    main()
