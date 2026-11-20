"""One SUMO scheduler/energy owner and dependent, explicitly assumed sensors."""

import hashlib
import importlib.metadata
import json
import math
import os
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from evdt.monitoring.contracts import monitoring_contract
from evdt.monitoring.coolant_adapter import optional_boundary
from evdt.monitoring.formats import operational_format
from evdt.monitoring.pacing import WorkingClock, time_scale
from evdt.monitoring.plant import DependentPlant, EnergyLedger, PlantConfig, battery_step_power
from evdt.monitoring.recipes import inputs
from evdt.monitoring.sensors import SensorBank


@dataclass(frozen=True)
class Frame:
    sim_time_s: float
    samples: list[dict[str, Any]]
    annotations: dict[str, str] = field(default_factory=dict)


class ObservationSource(Protocol):
    def frames(self) -> Iterator[Frame]: ...


class SumoVehicleSource:
    def __init__(
        self,
        scenario: Path,
        osm: Path,
        work: Path,
        asset: str,
        maximum_samples: int,
        paced: bool = True,
        run_identity: dict[str, Any] | None = None,
        on_manifest=None,
        on_clock=None,
        pause_at_s: float | None = None,
        pause_seconds: float = 0,
        catalog_version: str = "1.2.0",
        start_clock=None,
        on_route_context=None,
        time_scale_factor: float = 1,
        pause_control=None,
        on_held_charge=None,
    ) -> None:
        self.scenario, self.osm, self.work = scenario, osm, work
        self.asset, self.maximum_samples, self.paced = asset, maximum_samples, paced
        self.time_scale = time_scale(time_scale_factor)
        if self.time_scale != 1 and (not paced or start_clock is not None):
            raise ValueError("Time scaling requires an independent paced source")
        self.run_identity = run_identity or {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "session_id": "00000000-0000-0000-0000-000000000002",
            "asset_id": asset,
            "source_id": "sumo-adapter",
            "source_epoch": 1,
        }
        self.on_manifest, self.on_clock = on_manifest, on_clock
        self.pause_at_s, self.pause_seconds = pause_at_s, pause_seconds
        self.pause_control = pause_control
        self.manifest_sha256 = ""
        self.catalog_version = catalog_version
        self.start_clock = start_clock
        self.on_route_context = on_route_context
        self.on_held_charge = on_held_charge
        self.step_trace: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}

    def frames(self) -> Iterator[Frame]:
        # Installed only in the optional simulator image; backend code has no SUMO dependency.
        import sumo
        import traci

        self.work.mkdir(parents=True, exist_ok=True)
        mission = json.loads((self.scenario / "mission.json").read_text())
        journey = None
        if (self.scenario / "prepared-journey.json").is_file():
            from evdt.monitoring.prepared_journey import PreparedJourney

            journey = PreparedJourney(self.scenario)
        live_command = None
        moving_charge = None
        charge_recovery = False
        charge_outage = None
        command_step = None
        if credential := os.environ.get("EVDT_SPEED_COMMAND_TOKEN_FILE"):
            from evdt.command_effect import APPLY_AT_S
            from evdt.live_speed_command import OPERATOR_PROFILE, LiveSpeedCommand

            command_step = round(APPLY_AT_S / mission["step_s"])
            if (
                not self.paced
                or self.time_scale != 1
                or not math.isclose(command_step * mission["step_s"], APPLY_AT_S)
            ):
                raise ValueError("Live speed commands require a paced scheduler and aligned step")
            live_command = LiveSpeedCommand(
                self.run_identity["run_id"],
                self.asset,
                Path(credential),
                os.environ.get("EVDT_SPEED_COMMAND_PROFILE", OPERATOR_PROFILE),
            )
        if charge_path := os.environ.get("EVDT_MOVING_CHARGE_ASSIGNMENT"):
            from evdt.monitoring.charging_stop import write_stations
            from evdt.monitoring.moving_charge_executor import MovingChargeExecutor

            if (
                journey is None
                or not self.paced
                or self.time_scale != 1
                or live_command is not None
                or self.on_route_context is None
            ):
                raise ValueError(
                    "Moving charging control requires the dedicated 1× geographic profile"
                )
            moving_charge = MovingChargeExecutor(
                self.run_identity["run_id"],
                self.asset,
                charge_path,
                os.environ["EVDT_MOVING_CHARGE_EXECUTOR_KEY"],
            )
            if (
                moving_charge.assignment.network_sha256 != journey.spec["forecast_network_sha256"]
                or moving_charge.assignment.baseline_edges != tuple(journey.spec["route_edges"])
                or moving_charge.assignment_sha256
                != journey.spec["moving_charge_assignment_sha256"]
            ):
                raise ValueError("Moving charging control differs from the prepared input")
            charge_recovery = journey.spec.get("moving_charge_recovery") is True
            moving_charge.queue_trial = journey.spec.get("qualification_charge_queue") is True
            charge_outage = journey.spec.get("qualification_charger_outage")
            if charge_recovery and (self.on_clock is None or self.on_held_charge is None):
                raise ValueError("Charging recovery needs the registered source clock and receipt")
            if charge_outage and not charge_recovery:
                raise ValueError("A qualification outage requires the dedicated recovery profile")
            write_stations(
                self.work / "charging.add.xml",
                (entry.bay for entry in moving_charge.assignment.bays),
            )
        parameters = json.loads((self.scenario / "bus-parameters.json").read_text())
        p = {name: spec["value"] for name, spec in parameters["parameters"].items()}
        config = PlantConfig.load(self.scenario / "plant.json")
        raw_config = json.loads((self.scenario / "plant.json").read_text())
        if config.auxiliary_power_w != p["baseline_auxiliary_power"]:
            raise ValueError("Total auxiliary load disagrees with bus assumptions")
        plant = DependentPlant(config)
        contract = monitoring_contract()
        format_spec = operational_format(self.catalog_version)
        sensors = SensorBank(
            contract.readers[self.catalog_version].catalog,
            mission["step_s"],
            raw_config["sampling"],
        )
        boundary = optional_boundary(self.scenario)
        capacity_j = p["usable_battery_energy"] * 3_600_000
        initial_j = capacity_j * p["initial_soc"]
        ledger = EnergyLedger(initial_j, capacity_j)
        binary = Path(sumo.SUMO_HOME) / "bin"
        from evdt.monitoring.network import build_network

        if journey is None:
            build_network(self.scenario, self.osm, self.work)
        routes = ET.Element("routes")
        vehicle_type = ET.SubElement(
            routes,
            "vType",
            id="synthetic-bus",
            vClass="bus",
            length=str(p["length"]),
            width=str(p["width"]),
            maxSpeed=str(p["maximum_speed"]),
            accel=str(p["acceleration"]),
            decel=str(p["comfortable_deceleration"]),
            emergencyDecel=str(p["emergency_deceleration"]),
            tau=str(p["driver_tau"]),
            sigma=str(p["driver_sigma"]),
            height=str(p["height"]),
            mass=str(p["curb_mass"]),
            emissionClass="Energy/unknown",
        )
        energy_parameters = {
            "has.battery.device": "true",
            "device.battery.capacity": capacity_j / 3600,
            "loading": p["passenger_load"],
            "frontSurfaceArea": raw_config["energy"]["front_area_m2"],
            "airDragCoefficient": raw_config["energy"]["air_drag_coefficient"],
            "rotatingMass": raw_config["energy"]["rotating_mass_kg"],
            "radialDragCoefficient": raw_config["energy"]["radial_drag_coefficient"],
            "rollDragCoefficient": raw_config["energy"]["rolling_drag_coefficient"],
            "constantPowerIntake": config.auxiliary_power_w,
            "propulsionEfficiency": config.propulsion_efficiency,
            "recuperationEfficiency": config.recuperation_efficiency,
        }
        for key, value in energy_parameters.items():
            ET.SubElement(vehicle_type, "param", key=key, value=str(value))
        ET.SubElement(routes, "route", id="mission", edges=" ".join(mission["route_edges"]))
        vehicle = ET.SubElement(
            routes,
            "vehicle",
            id=self.asset,
            type="synthetic-bus",
            route="mission",
            depart="0",
            departSpeed="0",
        )
        stop = mission["stop"]
        ET.SubElement(
            vehicle, "param", key="device.battery.chargeLevel", value=str(initial_j / 3600)
        )
        ET.SubElement(
            vehicle,
            "stop",
            lane=stop["lane"],
            endPos=str(stop["end_pos_m"]),
            duration=str(stop["duration_s"]),
        )
        ET.ElementTree(routes).write(
            self.work / "routes.rou.xml", encoding="utf-8", xml_declaration=True
        )
        if journey is not None:
            journey.install(self.work, self.asset)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        log = (self.work / "sumo.log").open("w")
        process = subprocess.Popen(
            [
                str(binary / "sumo"),
                "--net-file",
                str(self.work / "network.net.xml"),
                "--route-files",
                str(self.work / "routes.rou.xml"),
                "--remote-port",
                str(port),
                "--step-length",
                str(mission["step_s"]),
                "--seed",
                str(mission["seed"]),
                "--no-step-log",
                "true",
                "--duration-log.disable",
                "true",
                "--precision",
                "12",
                *(["--time-to-teleport", "-1"] if journey else []),
                *(
                    [
                        "--additional-files",
                        str(self.work / "charging.add.xml"),
                        "--chargingstations-output",
                        str(self.work / "charging-stations.xml"),
                    ]
                    if moving_charge
                    else []
                ),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        connection = None
        try:
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            actual_version = connection.getVersion()
            if "1.28.0" not in actual_version[1]:
                raise RuntimeError("Qualify a new SUMO version explicitly")
            if abs(connection.simulation.getDeltaT() - mission["step_s"]) > 1e-9:
                raise RuntimeError("SUMO step differs from configured scheduler")
            manifest = {
                "manifest_version": format_spec["manifest_version"],
                "mode": "simulation",
                "identity": self.run_identity,
                "asset_id": self.asset,
                "model_version": config.model_version,
                "evidence_class": "assumed",
                "sumo_version": actual_version[1],
                "traci_protocol": actual_version[0],
                "packages": {
                    name: importlib.metadata.version(name)
                    for name in ("eclipse-sumo", "traci", "sumolib", "sumo-data")
                },
                "step_s": mission["step_s"],
                "paced": self.paced,
                "initial_energy_j": initial_j,
                "capacity_j": capacity_j,
                "sampling": {
                    key: raw_config["sampling"][key]
                    for key in ("default_period_s", "period_overrides_s")
                },
                "signals": sensors.timings(),
                "heartbeat_deadline_s": 5,
                "minimum_sim_time_s": 0,
                "driver_options_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "pause_at_s": self.pause_at_s,
                            "pause_seconds": self.pause_seconds,
                            **(
                                {"speed_command_profile": live_command.profile}
                                if live_command
                                else {}
                            ),
                            **(
                                {"moving_charge_assignment": moving_charge.assignment_sha256}
                                if moving_charge
                                else {}
                            ),
                            **({"moving_charge_recovery": True} if charge_recovery else {}),
                            **(
                                {"qualification_charger_outage": charge_outage}
                                if charge_outage
                                else {}
                            ),
                            **(
                                {"route_context_version": "source-route-context/2.0.0"}
                                if self.on_route_context is not None
                                else {}
                            ),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                "source_epoch_start_sim_s": 0,
                "initial_state_sim_s": mission["step_s"],
                "battery_power_source": (
                    "Net device energyConsumed minus observed station energyCharged per step"
                    if moving_charge
                    else "device.battery.energyConsumed (Wh per completed step) * 3600 / step_s; emission rate query is diagnostic only"
                ),
                "insertion_policy": "First SUMO insertion initializes energy; check device_step=0 and initial charge, then reconcile each completed physical interval",
                "schema_version": format_spec["schema_version"],
                "catalog_version": self.catalog_version,
                "input_sha256": inputs(self.scenario, self.osm, self.catalog_version),
            }
            manifest_bytes = (
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            self.manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            with (self.work / "run-manifest.json").open("xb") as artifact:
                artifact.write(manifest_bytes)
            if self.on_manifest:
                self.on_manifest(manifest_bytes)
            route_capture = None
            if self.on_route_context is not None:
                from evdt.monitoring.route_capture import RouteCapture

                route_capture = RouteCapture(
                    self.run_identity,
                    journey.spec["forecast_network_sha256"]
                    if journey
                    else hashlib.sha256((self.work / "network.net.xml").read_bytes()).hexdigest(),
                    self.manifest_sha256,
                    self.on_route_context,
                    indexed_journey=journey,
                )
            started = self.start_clock() if self.start_clock else time.monotonic()
            clock = WorkingClock(self.time_scale, started)
            pacing_lag_max_s = 0.0
            steps = 0
            sent = 0
            insertion = None
            maximum_emission_difference_w = 0.0
            pause_done = False
            while sent < self.maximum_samples and connection.simulation.getMinExpectedNumber() > 0:
                steps += 1
                if self.paced:
                    pacing_lag_max_s = max(
                        pacing_lag_max_s, clock.wait_until(steps * mission["step_s"])
                    )
                if live_command is not None and steps - 1 == command_step:
                    live_command.step(float(connection.simulation.getTime()), connection.vehicle)
                if (
                    charge_outage
                    and moving_charge.active is not None
                    and moving_charge.active[0].bay_id == charge_outage["bay_id"]
                    and float(connection.simulation.getTime()) >= charge_outage["working_s"]
                    and moving_charge.charged_wh > 0
                ):
                    # A qualification-only change to the simulated charger contract.
                    # The advisor sees it only through the verified source readback.
                    connection.chargingstation.setChargingPower(charge_outage["bay_id"], 0)
                    charge_outage = None
                if moving_charge is not None and not moving_charge.before_step(
                    connection, float(connection.simulation.getTime()), steps * mission["step_s"]
                ):
                    recovered = False
                    moving_charge.capture_interruption(connection, journey, capacity_j)
                    if self.on_held_charge and route_capture and self.on_clock:
                        from evdt.energy.route_aware.wire import canonical_bytes, fingerprint

                        held = moving_charge.held_observation(
                            route_capture.scope,
                            self.manifest_sha256,
                            auxiliary_power_w=plant.config.auxiliary_power_w,
                            battery_temperature_k=plant.pack_k,
                        )
                        if held is not None:
                            raw = canonical_bytes(held)
                            if len(raw) <= 8192:
                                self.on_clock("paused", held.working_s)
                                receipt = self.on_held_charge(raw)
                                if receipt["observation_sha256"] != fingerprint(held):
                                    raise RuntimeError(
                                        "Held charge observation acknowledgment mismatch"
                                    )
                                if charge_recovery:
                                    hold_started = time.monotonic()
                                    recovered = moving_charge.recover(
                                        connection, journey, held, self.on_clock
                                    )
                                    if recovered:
                                        self.on_clock("running", held.working_s)
                                        elapsed = time.monotonic() - hold_started
                                        started += elapsed
                                        clock.delay(elapsed)
                    if not recovered or not moving_charge.before_step(
                        connection,
                        float(connection.simulation.getTime()),
                        steps * mission["step_s"],
                    ):
                        break
                connection.simulationStep()
                if (
                    connection.simulation.getCollidingVehiclesIDList()
                    or connection.simulation.getStartingTeleportIDList()
                ):
                    raise RuntimeError("Unexpected collision or relocation")
                if self.asset not in connection.vehicle.getIDList():
                    continue
                if journey is not None:
                    journey.observe(connection, self.asset)
                x, y = connection.vehicle.getPosition(self.asset)
                lon, lat = connection.simulation.convertGeo(x, y)
                speed = connection.vehicle.getSpeed(self.asset)
                acceleration = connection.vehicle.getAcceleration(self.asset)
                energy_j = (
                    float(connection.vehicle.getParameter(self.asset, "device.battery.chargeLevel"))
                    * 3600
                )
                actual_capacity = (
                    float(connection.vehicle.getParameter(self.asset, "device.battery.capacity"))
                    * 3600
                )
                if abs(actual_capacity - capacity_j) > 0.01:
                    raise RuntimeError("SUMO capacity differs from the configured usable energy")
                emission_power_w = connection.vehicle.getElectricityConsumption(self.asset) * 3600
                device_step_wh = float(
                    connection.vehicle.getParameter(self.asset, "device.battery.energyConsumed")
                )
                if moving_charge and moving_charge.queue_opens_working_s is not None:
                    energy_j, device_step_wh = moving_charge.account_queued_step(
                        connection,
                        float(connection.simulation.getTime()),
                        energy_j,
                        device_step_wh,
                        mission["step_s"],
                    )
                device_step_j = device_step_wh * 3600
                power_w = battery_step_power(device_step_wh, mission["step_s"])
                if insertion is None:
                    if steps != 1 or abs(energy_j - initial_j) > 0.01 or abs(device_step_j) > 0.01:
                        raise RuntimeError(
                            "SUMO insertion semantics differ from the qualified initial state"
                        )
                    insertion = {
                        "sim_time_s": float(connection.simulation.getTime()),
                        "energy_j": energy_j,
                        "device_step_j": device_step_j,
                        "emission_rate_query_w": emission_power_w,
                    }
                    continue
                maximum_emission_difference_w = max(
                    maximum_emission_difference_w, abs(emission_power_w - power_w)
                )
                charge_wh = (
                    moving_charge.observe(
                        connection, steps * mission["step_s"], energy_j / capacity_j
                    )
                    if moving_charge
                    else 0.0
                )
                net_power_w = (
                    battery_step_power(device_step_wh - charge_wh, mission["step_s"])
                    if charge_wh
                    else power_w
                )
                charging_power_w = battery_step_power(charge_wh, mission["step_s"])
                ledger.observe(energy_j, net_power_w, mission["step_s"])
                if energy_j <= 0:
                    raise RuntimeError(
                        "Battery depleted; initial plant must stop instead of driving empty"
                    )
                readings = {
                    "location.latitude": lat,
                    "location.longitude": lon,
                    "motion.speed": speed,
                    "motion.distance": connection.vehicle.getDistance(self.asset),
                    "motion.heading": math.radians(connection.vehicle.getAngle(self.asset)),
                    "motion.acceleration": acceleration,
                    "location.horizontal_accuracy": 0.0,
                    **plant.step(
                        pack_power_w=net_power_w,
                        energy_j=energy_j,
                        capacity_j=capacity_j,
                        speed_m_s=speed,
                        acceleration_m_s2=acceleration,
                        ambient_k=p["ambient_temperature"],
                        dt_s=mission["step_s"],
                        charging_power_w=charging_power_w,
                    ),
                }
                if boundary is not None:
                    readings.update(boundary.step(readings, mission["step_s"]))
                sim_time = float(connection.simulation.getTime())
                self.step_trace.append(
                    {
                        "sim_time_s": sim_time,
                        "values": readings,
                        "sumo_heading_deg": connection.vehicle.getAngle(self.asset),
                    }
                )
                samples = sensors.sample(steps, readings)
                if self.on_clock and (samples or steps % max(1, round(1 / mission["step_s"])) == 0):
                    self.on_clock("running", sim_time)
                if samples:
                    annotations = {}
                    if route_capture is not None:
                        try:
                            annotations = route_capture.sample(
                                connection,
                                sim_time,
                                charging_state=(
                                    (
                                        "charging"
                                        if charge_wh
                                        else "unknown"
                                        if moving_charge.active
                                        else "not_charging"
                                    )
                                    if moving_charge
                                    else None
                                ),
                            )
                        except ValueError:
                            # Unsupported context cannot silently reuse the preceding plan.
                            annotations = {"energy_context_state": "unavailable"}
                    if journey is not None:
                        try:
                            progress = json.dumps(
                                journey.trip_progress(connection, self.asset),
                                separators=(",", ":"),
                                allow_nan=False,
                            )
                            if len(progress) <= 256:
                                annotations["trip_progress"] = progress
                        except (ValueError, traci.exceptions.TraCIException):
                            # An unsupported route observation leaves only trip progress unavailable.
                            pass
                    if moving_charge:
                        annotations.update(moving_charge.observation())
                    yield Frame(sim_time, samples, annotations)
                    sent += 1
                    if journey is not None and journey.arrived:
                        break
                    elapsed = 0.0
                    if self.pause_control:
                        elapsed = self.pause_control.between_observations(sim_time)
                        started += elapsed
                        clock.delay(elapsed)
                    if (
                        moving_charge
                        and route_capture
                        and not elapsed
                        and "energy_context" in annotations
                    ):
                        moving_charge.try_apply(
                            connection,
                            journey,
                            route_capture.current_context,
                            energy_j / capacity_j,
                            sim_time,
                        )
                    if (
                        not pause_done
                        and self.pause_at_s is not None
                        and sim_time >= self.pause_at_s
                        and self.pause_seconds > 0
                    ):
                        pause_started = time.monotonic()
                        if self.on_clock:
                            self.on_clock("paused", sim_time)
                        while time.monotonic() - pause_started < self.pause_seconds:
                            time.sleep(
                                min(
                                    1,
                                    max(0, self.pause_seconds - (time.monotonic() - pause_started)),
                                )
                            )
                            if self.on_clock:
                                self.on_clock("paused", sim_time)
                        if self.on_clock:
                            self.on_clock("running", sim_time)
                        pause_elapsed = time.monotonic() - pause_started
                        started += pause_elapsed
                        clock.delay(pause_elapsed)
                        pause_done = True
            if self.on_clock:
                self.on_clock("finished", float(connection.simulation.getTime()))
            self.summary = {
                "energy_ledger": ledger.__dict__,
                "sample_events": sent,
                "mission_finished": journey.arrived
                if journey
                else connection.simulation.getMinExpectedNumber() == 0,
                "completed_sim_time_s": float(connection.simulation.getTime()),
                "sensor_period_ticks": sensors.period_ticks,
                "insertion": insertion,
                "maximum_emission_rate_difference_w": maximum_emission_difference_w,
                **({"moving_charge": moving_charge.report()} if moving_charge else {}),
                "pacing": {
                    "time_scale": self.time_scale if self.paced else None,
                    "start_monotonic_s": started,
                    "completion_monotonic_s": time.monotonic(),
                    "elapsed_s": time.monotonic() - started,
                    "step_lag_max_s": pacing_lag_max_s,
                    "synchronized_start": self.start_clock is not None,
                },
            }
        finally:
            if moving_charge is not None and "moving_charge" not in self.summary:
                self.summary["moving_charge"] = moving_charge.report()
            try:
                (self.work / "plant-steps.json").write_text(
                    json.dumps(self.step_trace, separators=(",", ":")) + "\n"
                )
            finally:
                try:
                    if connection is not None:
                        connection.close(wait=False)
                finally:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                    log.close()
