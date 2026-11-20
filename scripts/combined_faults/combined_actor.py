"""Exercise one registered bus through the monitoring and operator charging services."""

import argparse
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from combined_thermal import Temperatures
from thermal_advisory import Reading, Sample
from thermal_intervention import Interlocks, MissionBoundary

from evdt.command_timing import ClockLease, host_clock
from evdt.config import ROOT
from evdt.contract_profile import Rejected
from evdt.monitoring.registration_client import RegistrationClient
from evdt.monitoring.run_policy import encoded
from evdt.monitoring.transport import open_spool
from scripts.charging.collect_station_arrivals import DESIGN, VEHICLE, digest
from scripts.charging.qualify_station_arrival_forecast import REFERENCE, load, write
from scripts.charging.registered_charging_actor import send, timestamp
from scripts.charging.run_charging_advisor import inputs
from scripts.charging.run_charging_missions import run

RECIPE = ROOT / "qualification/combined-faults/combined-faults.json"
SECRETS = Path("/fixture-secrets")
OUT = Path("/qualification")
MOTOR = "traction.motor.temperature"


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def wait_for(predicate, reason, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError(reason)


class RegisteredBus:
    def __init__(self, decision, recipe, case):
        self.recipe, self.case, self.decision = recipe, case, decision
        self.spool = open_spool()
        self.scope = {
            "run_id": self.spool.identity.run_id,
            "asset_id": self.spool.identity.asset_id,
        }
        self.policy = load(SECRETS / "run_policy.json")["sources"][0]["expected"]
        require(
            self.policy["input_sha256"]["p6-combined-recipe"] == digest(RECIPE),
            "Recipe pin differs",
        )
        self.registration = RegistrationClient(
            {
                **self.spool.identity.__dict__,
                "session_id": self.spool.start_session(require_fresh=True),
                "source_epoch": 1,
            },
            SECRETS / "producer1",
        )
        self.tokens = {
            role: (SECRETS / file).read_text().strip()
            for role, file in {
                "operator": "command_operator",
                "executor": "command_executor",
                "observer": "command_observer",
                "station": "command_station_writer",
                "reader": "reader",
            }.items()
        }
        self.originals = []
        self.report = {}
        self.command_id = str(uuid4())
        self.route_handed_off = False
        self.timeline = []
        self.local_protection = False
        self.last_fresh_motor_ns = None

    def mark(self, stage, connection):
        self.timeline.append(
            {
                "stage": stage,
                "elapsed_ns": time.monotonic_ns(),
                "sim_s": connection.simulation.getTime(),
            }
        )

    def call(self, path, role, body=None, status=200):
        return send(
            "http://command-api:8000", "/v1/charging" + path, self.tokens[role], body, status
        )

    def monitor(self, path, role="reader", status=200, *, asset=None):
        asset = asset or self.scope["asset_id"]
        return send(
            "http://api:8000", f"/v1/{path}?asset_id={asset}", self.tokens[role], expected=status
        )

    def register(self, connection, now, expected_edge):
        require(
            list(connection.getVersion())
            == [self.policy["traci_protocol"], self.policy["sumo_version"]]
            and connection.simulation.getDeltaT() == self.policy["step_s"]
            and connection.vehicle.getRoadID(VEHICLE) == expected_edge
            and connection.vehicle.getLanePosition(VEHICLE) == self.decision["departure_m"]
            and connection.vehicle.getSpeed(VEHICLE) == 0,
            "Simulator or parked departure differs from the registered recipe",
        )
        manifest = {
            **self.policy,
            "manifest_version": "source-run/2.0.0",
            "mode": "simulation",
            "asset_id": self.scope["asset_id"],
            "identity": self.registration.identity,
            "evidence_class": "assumed",
            "sampling": {"default_period_s": 1, "period_overrides_s": {}},
            "source_epoch_start_sim_s": 0,
            "minimum_sim_time_s": 0,
            "initial_state_sim_s": now,
            "battery_power_source": "SUMO battery charge and draw on completed steps",
            "insertion_policy": "Phase 6 measures the registered parked bus before any route change",
            "driver_options_sha256": digest(OUT / "mission/mission.rou.xml"),
        }
        self.registration.register(encoded(manifest))
        write(OUT / "registered-manifest.json", manifest)
        self.monitor("snapshot", "operator", 401)
        self.monitor("snapshot", status=403, asset="bus-002")
        self.call("/recommendation", "reader", status=401)
        self.call(
            "/automatic/diversions", "operator", {**self.scope, "command_id": self.command_id}, 403
        )
        result = send("http://command-api:8000", "/v1/commands", self.tokens["operator"], {}, 409)
        require(result["detail"] == "charging_profile_exclusive", "Competing command route enabled")

    def emit(self, connection, motor_k):
        now = connection.simulation.getTime()
        self.registration.clock("running", now)
        samples = [
            {
                "signal_id": signal,
                "unit": unit,
                "value": value,
                "quality": "good",
                "provenance": "simulated-observation",
            }
            for signal, unit, value in (
                ("motion.speed", "m/s", connection.vehicle.getSpeed(VEHICLE)),
                (MOTOR, "K", motor_k),
            )
        ]
        event = self.spool.enqueue(samples, now, manifest_sha256=self.registration.manifest_hash)
        self.originals.append(json.loads(event.raw))
        write(OUT / f"observation-{len(self.originals):02}.json", self.originals[-1])
        return event.event_id

    def fresh_motor(self, event_id):
        def current():
            row = next(r for r in self.monitor("snapshot")["items"] if r["signal_id"] == MOTOR)
            return (
                row
                if (
                    row["freshness"] == "fresh"
                    and row["quality"] == "good"
                    and row["observation"]["event_id"] == event_id
                )
                else None
            )

        wait_for(current, "Fresh registered observation did not reach the read API")
        row = current()
        require(row is not None, "Registered observation expired before admission")
        self.last_fresh_motor_ns = time.monotonic_ns() - round(row["age_s"] * 10**9)
        return row["observation"]["sample"]["value"]

    def publish_departure(self, connection, *, protected=False):
        now = connection.simulation.getTime()
        departure = {
            **self.scope,
            **{k: v for k, v in self.decision.items() if k not in ("sim_s", "observed_sim_s")},
            "observation_id": str(uuid4()),
            "manifest_sha256": self.registration.manifest_hash,
            "sim_s": now,
            "observed_at": timestamp(),
            "initial_energy_kwh": float(
                connection.vehicle.getParameter(VEHICLE, "device.battery.chargeLevel")
            )
            / 1000,
            "speed_m_s": connection.vehicle.getSpeed(VEHICLE),
            "operator_override": False,
            "thermal_block": False,
            "local_protection_block": protected,
        }
        stations = {
            **self.scope,
            "observation_id": str(uuid4()),
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
        self.call("/stations", "station", stations)
        self.call("/departure", "observer", departure)
        write(OUT / f"departure-{departure['observation_id']}.json", departure)
        write(OUT / f"station-{stations['observation_id']}.json", stations)

    def claim(self, now, *, refusal=None):
        body = {**self.scope, "command_id": self.command_id, "sim_s": now}
        response = self.call("/claims", "executor", body, 409 if refusal else 200)
        if refusal:
            require(
                response["detail"] == refusal,
                f"Expected charging refusal {refusal}, got {response}",
            )
        return response

    def vehicle_state(self, connection, station):
        return {
            "sim_s": connection.simulation.getTime(),
            "route": list(connection.vehicle.getRoute(VEHICLE)),
            "speed_m_s": connection.vehicle.getSpeed(VEHICLE),
            "charge_wh": float(
                connection.vehicle.getParameter(VEHICLE, "device.battery.energyCharged")
            ),
            "station_power_w": connection.chargingstation.getChargingPower(station),
        }

    def authorize_route(self, connection, payload):
        if self.route_handed_off:
            raise Rejected("route_already_handed_off")
        proposal = payload["proposal"]
        require(
            (proposal["run_id"], proposal["vehicle_id"], proposal["correlation_id"])
            == (*self.scope.values(), self.command_id)
            and payload["departure_manifest_sha256"] == self.registration.manifest_hash
            and "authority" not in payload,
            "Charging handoff has different authority or lineage",
        )
        state = self.vehicle_state(connection, proposal["station_id"])
        if state["station_power_w"] <= 0:
            raise Rejected("physical_station_unavailable")
        if self.local_protection:
            raise Rejected("local_protection")
        if (
            self.last_fresh_motor_ns is None
            or time.monotonic_ns() - self.last_fresh_motor_ns >= 5_000_000_000
        ):
            raise Rejected("monitoring_observation_stale")
        if state["speed_m_s"] != 0:
            raise Rejected("moving_departure")
        if not proposal["valid_from_sim_s"] <= state["sim_s"] < proposal["expires_sim_s"]:
            raise Rejected("charging_expired_or_early")
        ClockLease.decode(proposal["wall_lease"]).verify(host_clock())
        self.route_handed_off = True
        return payload["candidate"]

    def outage(self, connection, station, now, reserved):
        (OUT / "stop-broker").touch()
        wait_for((OUT / "broker-down").is_file, "Broker stop was not confirmed", seconds=25)
        self.mark("broker_down", connection)
        connection.simulationStep()
        thermal = Temperatures(*self.recipe["thermal"]["initial_k"]).step(
            self.recipe["thermal"], degraded=True, boost_w_k=0
        )
        self.emit(connection, thermal.motor)
        require(
            self.spool.counts() == {"accepted": 1, "pending": 1},
            "Outage packet escaped or was lost",
        )
        time.sleep(self.recipe["outage_wait_s"])
        require(
            all(row["freshness"] == "stale" for row in self.monitor("snapshot")["items"]),
            "The existing monitoring observation did not become stale",
        )
        require(
            len(self.monitor("observations")["items"]) == 1,
            "New data appeared while broker was down",
        )
        self.mark("stale_snapshot", connection)
        if reserved is None:
            refusal = self.recipe["cases"][self.case]["before_effect_refusal"]
            self.claim(now, refusal=refusal)
        connection.chargingstation.setChargingPower(station, 0.0)
        self.mark("charger_power_removed", connection)
        if reserved is not None:
            try:
                self.authorize_route(connection, reserved)
            except Rejected as exc:
                refusal = str(exc)
            else:
                raise ValueError("Interrupted handoff unexpectedly became actionable")
            require(refusal == self.recipe["cases"][self.case]["before_effect_refusal"], refusal)
        (OUT / "restore-broker").touch()
        wait_for((OUT / "broker-restored").is_file, "Broker recovery was not confirmed", seconds=35)
        self.mark("broker_restored", connection)
        connection.simulationStep()
        require(
            self.fresh_motor(self.emit(connection, thermal.motor)) == thermal.motor,
            "Recovered reading differs",
        )
        self.mark("fresh_snapshot", connection)
        self.publish_departure(connection)
        recovery = self.recipe["cases"][self.case]["recovery_refusal"]
        self.claim(connection.simulation.getTime(), refusal=recovery)
        self.mark("recovery_refused", connection)
        old = self.call("/diversions/" + self.command_id, "operator")
        require(
            old["state"] == ("reserved_unknown" if reserved is not None else "queued")
            and old["effect"] is None,
            "Recovered command changed its durable outcome",
        )
        self.report.update(
            before_effect_refusal=refusal,
            recovery_refusal=recovery,
            previous_command_state=old["state"],
            stale_snapshot_verified=True,
            outage_pending_count=1,
            timeline=self.timeline,
        )

    def charge_or_refuse(self, connection, now):
        # The SUMO bus remains parked through all pre-actuation checks.
        thermal = self.recipe["thermal"]
        temperatures = Temperatures(*thermal["initial_k"])
        if self.case != "charge":
            temperatures = temperatures.step(thermal, degraded=True, boost_w_k=0)
            self.mark("cooling_degradation_injected", connection)
        motor = temperatures.motor
        require(self.fresh_motor(self.emit(connection, motor)) == motor, "Live motor value changed")
        wait_for(
            lambda: self.spool.counts() == {"accepted": 1, "pending": 0}, "Initial receipt missing"
        )
        self.publish_departure(connection)
        recommendation = self.call("/recommendation", "operator")
        request = {
            **self.scope,
            "command_id": self.command_id,
            "decision_sha256": recommendation["decision_sha256"],
        }
        self.call("/diversions", "observer", request, 401)
        queued = self.call("/diversions", "operator", request)
        self.mark("operator_request_queued", connection)
        station = queued["proposal"]["station_id"]
        before = self.vehicle_state(connection, station)
        if self.case == "charge":
            reserved = self.claim(now)
            require(reserved == queued, "Durable claim does not match the operator request")
            return self.authorize_route(connection, reserved)
        connection.vehicle.setSpeed(VEHICLE, 0)
        if self.case == "protected":
            connection.simulationStep()
            protection = load(
                ROOT / "qualification/thermal/synthetic-intervention-protocol-1.0.0.json"
            )["protection"]
            motor = protection["engage_k"]
            observed = self.fresh_motor(self.emit(connection, motor))
            self.local_protection = observed >= protection["engage_k"]
            self.publish_departure(connection, protected=self.local_protection)
            refusal = self.recipe["cases"][self.case]["before_effect_refusal"]
            self.claim(connection.simulation.getTime(), refusal=refusal)
            self.report.update(before_effect_refusal=refusal, protection_motor_k=observed)
        else:
            reserved = self.claim(now) if self.case == "interrupted-outage" else None
            self.outage(connection, station, now, reserved)
        after = self.vehicle_state(connection, station)
        require(
            not self.route_handed_off
            and before["route"] == after["route"]
            and before["speed_m_s"] == after["speed_m_s"] == 0
            and before["charge_wh"] == after["charge_wh"] == 0,
            "Refused command changed simulator motion, route or charge",
        )
        self.report.update(before=before, after=after, no_effect=True)
        return None

    def verify_monitoring(self):
        expected = len(self.originals)
        wait_for(
            lambda: self.spool.counts() == {"accepted": expected, "pending": 0},
            "Queue did not reconcile",
        )
        history = self.monitor("observations")
        require(
            history["run_id"] == self.scope["run_id"]
            and [row["payload"] for row in history["items"]] == self.originals,
            "Persisted originals differ",
        )
        write(OUT / "accepted-observations.json", history)
        self.report.update(accepted_messages=expected, final_queue=self.spool.counts())


def cool(bus, connection):
    policy = bus.recipe["thermal"]
    thermal = ROOT / "qualification/thermal"
    settings = load(thermal / "synthetic-intervention-protocol-1.0.0.json")
    fault = load(thermal / "synthetic-fault-protocol-1.0.0.json")
    selected = load(thermal / "synthetic-advisory-release-1.0.0.json")["selected"]
    boost = settings["boost"]
    actual = reference = Temperatures(*policy["initial_k"])
    connection.vehicle.setSpeed(VEHICLE, 0)
    with closing(sqlite3.connect(OUT / "cooling-journal.sqlite")) as journal:
        boundary = MissionBoundary(
            tuple(bus.scope.values()),
            journal,
            {**fault["common_rules"], **selected},
            fault["warmup_s"],
            boost["duration_s"],
        )
        for tick in range(policy["maximum_trigger_tick"] + 1):
            connection.simulationStep(tick + 1)
            clock = host_clock()
            sample = Sample(
                tick,
                Reading(actual.motor, tick),
                Reading(actual.housing, tick),
                reference.motor,
                reference.housing,
            )
            degraded = tick >= policy["degradation_tick"]
            if boundary.observe(sample, clock).state == "cooling_consistent":
                break
            actual = actual.step(policy, degraded=degraded, boost_w_k=0)
            reference = reference.step(policy, degraded=False, boost_w_k=0)
        else:
            raise ValueError("Cooling degradation did not corroborate in both probes")
        before_route = list(connection.vehicle.getRoute(VEHICLE))
        observed = bus.fresh_motor(bus.emit(connection, actual.motor))
        require(observed == sample.motor.temperature_k, "Registered thermal evidence differs")
        interlocks = Interlocks(local_protection=actual.motor >= settings["protection"]["engage_k"])
        clock = host_clock()
        reservation = boundary.reserve(boundary.scope, tick, clock, interlocks)
        boundary.grant(reservation, tick, host_clock(), interlocks)
        try:
            boundary.reserve(boundary.scope, tick, host_clock(), interlocks)
        except Rejected as exc:
            require(str(exc) == "thermal_grant_spent", "Unexpected repeated cooling response")
        else:
            raise ValueError("Cooling grant could be reserved twice")
        matched = actual
        with (OUT / "cooling-trace.jsonl").open("x", encoding="utf-8") as trace:
            for elapsed in range(policy["effect_window_s"]):
                step = tick + elapsed
                clock = host_clock()
                if elapsed:
                    boundary.observe(
                        Sample(
                            step,
                            Reading(actual.motor, step),
                            Reading(actual.housing, step),
                            reference.motor,
                            reference.housing,
                        ),
                        clock,
                    )
                protected = actual.motor >= settings["protection"]["engage_k"]
                pumped = boundary.pump_on(step, clock, Interlocks(local_protection=protected))
                require(pumped and not protected, "Cooling ended early or overrode protection")
                actual = actual.step(
                    policy, degraded=True, boost_w_k=boost["housing_conductance_w_per_k"]
                )
                matched = matched.step(policy, degraded=True, boost_w_k=0)
                reference = reference.step(policy, degraded=False, boost_w_k=0)
                connection.simulationStep(step + 2)
                trace.write(
                    json.dumps(
                        {
                            "elapsed_s": elapsed + 1,
                            "motor_k": actual.motor,
                            "housing_k": actual.housing,
                            "matched_motor_k": matched.motor,
                            "pump_w": boost["aux_power_w"] if pumped else 0,
                            "speed_m_s": connection.vehicle.getSpeed(VEHICLE),
                            "route": list(connection.vehicle.getRoute(VEHICLE)),
                        },
                        allow_nan=False,
                    )
                    + "\n"
                )
        require(list(connection.vehicle.getRoute(VEHICLE)) == before_route, "Cooling changed route")
        bus.fresh_motor(bus.emit(connection, actual.motor))
        bus.report.update(
            cooling_grant_tick=tick,
            two_probe_evidence=True,
            no_route_change=True,
            repeated_cooling_refused=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=load(RECIPE)["cases"])
    args = parser.parse_args()
    recipe = load(RECIPE)
    net, mission, catalog, parameters, _ = inputs()
    decision = load(ROOT / "qualification/charging/advisory-scenario-1.0.0.json")["decisions"][0]
    energy = load(ROOT / load(DESIGN)["scenario"] / "plant.json")["energy"]
    network = (ROOT / load(REFERENCE)["private_report"]).parent / "network.net.xml"
    edges = mission["route_edges"][decision["departure_route_index"] :]
    bus = RegisteredBus(decision, recipe, args.case)

    def handoff(connection, now, _energy, _events):
        bus.register(connection, now, edges[0])
        if args.case == "cool":
            cool(bus, connection)
            return None, bus.command_id
        return bus.charge_or_refuse(connection, now), bus.command_id

    measured = run(
        OUT / "mission",
        "p6-combined",
        network,
        [net.getEdge(edge) for edge in edges],
        decision,
        None,
        {},
        catalog["stations"],
        parameters,
        energy,
        mission["step_s"],
        handoff=handoff,
    )
    bus.verify_monitoring()
    if args.case == "charge":
        require(
            all(
                measured[k]
                for k in ("completed", "route_changed", "target_reached", "resumed_after_charging")
            )
            and not measured["reserve_violation"],
            "Authorized diversion did not charge and complete",
        )
    else:
        require(not measured["route_changed"], "Isolated refusal or cooling changed the route")
    write(
        OUT / "case.json",
        {"case": args.case, **bus.scope, "command_id": bus.command_id, **bus.report},
    )


if __name__ == "__main__":
    main()
