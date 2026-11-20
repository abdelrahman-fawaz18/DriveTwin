"""Measure a reviewed SUMO charging stop without enabling operator control."""

import json
import math
import secrets
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

from evdt.monitoring.charging_queue import apply_waiting_equipment, waiting_at_bay
from evdt.monitoring.charging_stop import (
    MOVING_CHARGE_STOP_LIMIT_S,
    insert_on_route_charge,
    write_stations,
)
from evdt.monitoring.plant import EnergyLedger, battery_step_power
from evdt.monitoring.prepared_journey import PreparedJourney

ASSET = "bus-001"
STEP_S = 0.2


def read(path):
    return json.loads(path.read_bytes())


def run(output, profile, *, outage=False, recovery=False):
    from uuid import uuid4

    from evaluate import IMAGE, ROOT, read_inspect
    from live_inputs import prepare
    from qualify_restarts import monitor

    from evdt.energy.route_aware.wire import canonical_bytes

    project = "evdt-e2c-" + uuid4().hex[:12]
    (output / "secrets").mkdir()
    run_id = str(uuid4())
    _, _, data, assignment = prepare(profile, output, run_id)
    if assignment is None:
        raise ValueError("Charging plant probe requires a reviewed station assignment")
    same_route_bays = sorted(assignment.bays, key=lambda row: row.external_index)
    recovery_plan = profile["held_recovery_probe"] if recovery else None
    queue = profile.get("queued_station_probe")
    detour = profile.get("detour_station_probe")
    if detour is not None and (
        outage or recovery or queue is not None or set(detour) != {"bay_id", "target_soc"}
    ):
        raise ValueError("Detour probe requires one fixed alternate bay and target")
    if queue is not None and (outage or recovery or set(queue) != {"bay_id", "target_soc"}):
        raise ValueError("Queued station probe requires one fixed stop and target")
    bay = (
        next(item.bay for item in same_route_bays if item.bay.bay_id == recovery_plan["failed_bay"])
        if recovery
        else next(item.bay for item in same_route_bays if item.bay.bay_id == queue["bay_id"])
        if queue is not None
        else next(
            item.bay
            for item in same_route_bays
            if item.detour is not None and item.bay.bay_id == detour["bay_id"]
        )
        if detour is not None
        else same_route_bays[-1].bay
    )
    recipe = data["recipe"]
    capacity = recipe["capacity_kwh"] * 3_600_000
    target = (queue or detour or {"target_soc": 0.12})["target_soc"]
    if bay.setup_working_s < 1 or not 0.1 <= recipe["initial_soc"] < target <= 1:
        raise ValueError("Use the reviewed low-charge plant case with a stationary setup delay")
    (output / "assignment.json").write_bytes(canonical_bytes(assignment))
    (output / "case.json").write_text(
        json.dumps(
            {
                "bay_id": bay.bay_id,
                "initial_j": capacity * recipe["initial_soc"],
                "capacity_j": capacity,
                "reserve_j": capacity * 0.1,
                "target_j": capacity * target,
                "outage": outage or recovery,
                **({"queue": True} if queue is not None else {}),
                **(
                    {
                        "detour": True,
                        "max_working_s": min(
                            14_400,
                            max(2000, math.ceil(profile["expected_working_minutes"] * 75 + 600)),
                        ),
                    }
                    if detour is not None
                    else {}
                ),
                **({"recovery": recovery_plan} if recovery else {}),
            }
        )
    )
    name = project + "-probe"
    command = [
        "docker",
        "run",
        "--pull=never",
        "--name",
        name,
        "--label",
        "com.docker.compose.project=" + project,
        "--label",
        "com.docker.compose.service=charging-plant",
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
        f"{output}:/evidence",
        "--volume",
        f"{ROOT / 'src'}:/project-src:ro",
        "--volume",
        f"{ROOT / 'contracts'}:/contracts:ro",
        "--volume",
        f"{Path(__file__).resolve()}:/probe.py:ro",
        "--env",
        "PYTHONPATH=/project-src",
        "--entrypoint",
        "python",
        IMAGE,
        "/probe.py",
        "/evidence",
    ]
    result = {"passed": False, "cleanup_verified": False}
    try:
        # A finite job may exit between Docker's inventory and the cgroup read.
        # The collector resamples that transition and still checks the final exit.
        with monitor(project, output, allow_replacement=True, finite_services=("charging-plant",)):
            with (output / "probe.log").open("xb") as log:
                status = subprocess.run(command, stdout=log, stderr=log, timeout=145)
            info = read_inspect(name)
            reading = read(output / "plant-result.json")
            result.update(
                measurement=reading,
                container_exit_code=info["State"]["ExitCode"],
                oom_killed=info["State"]["OOMKilled"],
            )
            result["passed"] = (
                status.returncode == 0 and reading["passed"] and not result["oom_killed"]
            )
    finally:
        query = ["docker", "ps", "-aq", "--filter", f"name=^/{name}$"]
        existing = subprocess.check_output(query, timeout=10).strip()
        if existing:
            info = read_inspect(name)
            if info["Config"]["Labels"].get("com.docker.compose.project") != project:
                raise RuntimeError("Charging probe cleanup refused for foreign container")
            if info["State"]["Running"]:
                subprocess.run(["docker", "stop", "--time", "3", name], check=True, timeout=15)
            subprocess.run(["docker", "rm", name], check=True, timeout=15)
        result["cleanup_verified"] = not subprocess.check_output(query, timeout=10).strip()
    result["passed"] = result["passed"] and result["cleanup_verified"]
    return result


def observe(folder, result):
    import sumo
    import traci

    from evdt.energy.moving_charge_assignment import StationAssignment
    from evdt.energy.route_aware.wire import decode_record

    assignment = decode_record(StationAssignment, read(folder / "assignment.json"))
    case = read(folder / "case.json")
    assigned = next(row for row in assignment.bays if row.bay.bay_id == case["bay_id"])

    def apply_detour(connection, journey, lane):
        from types import SimpleNamespace

        from evdt.energy.route_aware.wire import fingerprint
        from evdt.monitoring.moving_charge_executor import MovingChargeExecutor

        progress = SimpleNamespace(
            external_index=connection.vehicle.getRouteIndex(ASSET),
            lane_id=lane,
        )
        review = SimpleNamespace(detour_sha256=fingerprint(assigned.detour))
        return MovingChargeExecutor.apply_detour(
            SimpleNamespace(asset=ASSET),
            connection,
            journey,
            progress,
            assigned,
            review,
        )

    bay = assigned.bay
    recovery = case.get("recovery")
    queue = case.get("queue", False)
    opening = None
    power_release = None
    previous_queue_soc_j = None
    was_waiting = False
    supplemental_queue_j = 0.0
    if queue:
        schedule = next(row for row in assignment.scenario.schedules if row.bay_id == bay.bay_id)
        waiting, available = schedule.intervals
        if (
            waiting.from_working_s != 0
            or waiting.state != "occupied"
            or available.state != "available"
            or waiting.until_working_s != available.from_working_s
            or available.power_w != bay.power_w
        ):
            raise ValueError("Expected one scheduled opening at nominal power")
        opening = available.from_working_s
        result["scheduled_opening_working_s"] = opening
    if recovery:
        replacement = next(row for row in assignment.bays if row.bay.bay_id == recovery["bay_id"])
        if replacement.detour is not None or assigned.external_index >= replacement.external_index:
            raise ValueError("A recovery probe requires a later bay on the same route")
        bay_ids = {bay.bay_id, replacement.bay.bay_id}
        charge_by_bay = dict.fromkeys(bay_ids, 0.0)
        target_soc = recovery["target_soc"]
        if (
            not case["reserve_j"] / case["capacity_j"]
            <= recovery["minimum_handoff_soc"]
            < target_soc
            <= 1
        ):
            raise ValueError("Invalid sealed recovery charge thresholds")
    else:
        bay_ids = {bay.bay_id}
        target_soc = case["target_j"] / case["capacity_j"]
    executor = None
    if case.get("outage"):
        from evdt.monitoring.moving_charge_executor import MovingChargeExecutor

        # Use the production step guard with a fixed stop and no command service.
        with TemporaryDirectory() as work:
            unused_credential = Path(work) / "unused"
            unused_credential.write_text(secrets.token_urlsafe(32))
            executor = MovingChargeExecutor(
                assignment.run_id, ASSET, folder / "assignment.json", unused_credential
            )
    journey = PreparedJourney(folder / "scenario")
    journey.install(folder, ASSET)
    write_stations(
        folder / "charging.add.xml",
        (bay, replacement.bay) if recovery else (bay,),
    )
    with socket.socket() as socket_reservation:
        socket_reservation.bind(("127.0.0.1", 0))
        port = socket_reservation.getsockname()[1]
    ledger = EnergyLedger(case["initial_j"], case["capacity_j"])
    limits = read(folder / "scenario/mission.json")
    process, connection = None, None
    ended = False
    charged_j = 0.0
    minimum_j = case["initial_j"]
    started = time.monotonic()
    served = 0
    stable_stops = [*journey.spec["stops"]]
    try:
        with (folder / "sumo.log").open("xb") as log:
            process = subprocess.Popen(
                [
                    str(Path(sumo.SUMO_HOME) / "bin/sumo"),
                    "--net-file",
                    "network.net.xml",
                    "--route-files",
                    "routes.rou.xml",
                    "--additional-files",
                    "charging.add.xml",
                    "--step-length",
                    str(STEP_S),
                    "--remote-port",
                    str(port),
                    "--seed",
                    str(limits["seed"]),
                    "--no-step-log",
                    "true",
                    "--precision",
                    "12",
                    "--time-to-teleport",
                    "-1",
                    "--chargingstations-output",
                    "charging-stations.xml",
                ],
                cwd=folder,
                stdout=log,
                stderr=log,
            )
            connection = traci.connect(
                port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            version = connection.getVersion()
            if version[0] != 22 or "1.28.0" not in version[1]:
                raise RuntimeError("Unexpected SUMO version")
            result["sumo"] = version
            vehicle = connection.vehicle
            charger = connection.chargingstation
            if queue:
                # Keep the plant at zero until the bus has arrived and the reviewed setup ends.
                charger.setChargingPower(bay.bay_id, 0)
                charger.setChargeDelay(bay.bay_id, 0)
            for tick in range(1, math.ceil(case.get("max_working_s", 2000) / STEP_S) + 1):
                if time.monotonic() - started > 120:
                    raise TimeoutError("Charging probe exceeded its real-life limit")
                if (folder / "linux-guard.json").exists():
                    raise RuntimeError("Host resource guard interrupted the charging probe")
                if executor is not None and executor.active is not None:
                    current_time = connection.simulation.getTime()
                    if charged_j >= 360_000:
                        connection.chargingstation.setChargingPower(bay.bay_id, 0)
                        if connection.chargingstation.getChargingPower(bay.bay_id) != 0:
                            raise RuntimeError("SUMO outage injection did not turn off the bay")
                        result["injected_outage_working_s"] = current_time
                    if not executor.before_step(connection, current_time, tick * STEP_S):
                        executor.capture_interruption(connection, journey, case["capacity_j"])
                        result["interruption"] = executor.report()
                        result["last_plant_step_working_s"] = connection.simulation.getTime()
                        if not recovery:
                            break
                        snapshot = executor.interruption_snapshot
                        if (
                            snapshot is None
                            or snapshot.get("status") != "held_at_committed_bay"
                            or abs(snapshot["working_s"] - recovery["held_working_s"]) > 1e-6
                            or abs(snapshot["energy_j"] - recovery["held_energy_j"]) > 0.01
                            or snapshot["route_sha256"] != recovery["route_sha256"]
                            or snapshot["remaining_stops_sha256"]
                            != recovery["remaining_stops_sha256"]
                            or snapshot["power_w"] != 0
                            or snapshot["energy_j"] / snapshot["capacity_j"]
                            < recovery["minimum_handoff_soc"]
                        ):
                            raise RuntimeError(
                                "Actual held state does not match the reviewed recovery inputs"
                            )
                        replacement_index = insert_on_route_charge(
                            connection,
                            ASSET,
                            journey,
                            replacement,
                            stop_limit_s=MOVING_CHARGE_STOP_LIMIT_S,
                            minimum_lead_m=25,
                            braking_m_s2=1.5,
                            held_bay=bay,
                        )
                        journey.charge_stop = replacement
                        result["replacement_applied"] = {
                            "bay_id": replacement.bay.bay_id,
                            "working_s": current_time,
                            "stop_index": replacement_index,
                        }
                        bay = replacement.bay
                        executor = None
                if queue and "queue_arrival" in result and power_release is None:
                    due = max(opening, result["queue_arrival"]["working_s"]) + bay.setup_working_s
                    if tick * STEP_S >= due - 1e-9:
                        previous_queue_soc_j = ledger.previous_j
                        charger.setChargingPower(bay.bay_id, bay.power_w)
                        power_release = tick * STEP_S
                        result["released_power_working_s"] = power_release
                if queue and charger.getChargingPower(bay.bay_id) != (
                    0 if power_release is None else bay.power_w
                ):
                    raise RuntimeError("Observed charger power differs from the reviewed slot")
                connection.simulationStep()
                if (
                    connection.simulation.getStartingTeleportIDList()
                    or connection.simulation.getCollidingVehiclesIDList()
                    or ASSET not in vehicle.getIDList()
                ):
                    raise RuntimeError("Unexpected bus disappearance, relocation, or collision")
                working_s = connection.simulation.getTime()
                journey.observe(connection, ASSET)
                speed = vehicle.getSpeed(ASSET)
                lane = vehicle.getLaneID(ASSET)
                position = vehicle.getLanePosition(ASSET)
                energy_j = float(vehicle.getParameter(ASSET, "device.battery.chargeLevel")) * 3600
                draw_wh = float(vehicle.getParameter(ASSET, "device.battery.energyConsumed"))
                charge_wh = float(vehicle.getParameter(ASSET, "device.battery.energyCharged"))
                station = vehicle.getParameter(ASSET, "device.battery.chargingStationId")
                stops = vehicle.getStops(ASSET)
                active_station = (
                    bool(stops)
                    and stops[0].stoppingPlaceID == bay.bay_id
                    and vehicle.isStopped(ASSET)
                    and lane == bay.lane_id
                    and bay.start_offset_m <= position <= bay.end_offset_m + 0.001
                    and speed < 0.1
                    and station == bay.bay_id
                )
                if queue:
                    at_queued_stop = waiting_at_bay(vehicle, ASSET, bay)
                    if at_queued_stop and "queue_arrival" not in result:
                        result["queue_arrival"] = {"working_s": working_s, "energy_j": energy_j}
                    if at_queued_stop and power_release is None and charge_wh != 0:
                        raise RuntimeError(
                            "Measured energy transfer while the bay was held at zero"
                        )
                    if was_waiting and at_queued_stop and power_release is None:
                        native_energy_j = energy_j
                        energy_j, draw_wh = apply_waiting_equipment(
                            vehicle,
                            ASSET,
                            energy_j=energy_j,
                            native_draw_wh=draw_wh,
                            equipment_w=assignment.policy.equipment_w,
                            step_s=STEP_S,
                        )
                        supplemental_queue_j += native_energy_j - energy_j
                    was_waiting = at_queued_stop
                if not math.isfinite(charge_wh) or charge_wh < 0:
                    raise RuntimeError("Invalid SUMO charger reading")
                if tick == 1:
                    if abs(energy_j - ledger.previous_j) > 0.01 or draw_wh or charge_wh:
                        raise RuntimeError("Initial observation differs from the prepared bus")
                else:
                    admitted_wh = charge_wh if active_station else 0.0
                    ledger.observe(
                        energy_j, battery_step_power(draw_wh - admitted_wh, STEP_S), STEP_S
                    )
                    if admitted_wh:
                        charged_j += admitted_wh * 3600
                        if recovery:
                            charge_by_bay[bay.bay_id] += admitted_wh * 3600
                            if (
                                bay.bay_id == recovery["bay_id"]
                                and "replacement_charging" not in result
                            ):
                                result["replacement_charging"] = {
                                    "working_s": working_s,
                                    "energy_j": energy_j,
                                    "bay_id": station,
                                }
                        if "charging" not in result:
                            result["charging"] = {
                                "working_s": working_s,
                                "energy_j": energy_j,
                                "bay_id": station,
                            }
                        if executor is not None:
                            executor.charged_wh += admitted_wh
                        if "target" not in result and energy_j >= target_soc * case["capacity_j"]:
                            result["target"] = {"working_s": working_s, "energy_j": energy_j}
                            vehicle.resume(ASSET)
                minimum_j = min(minimum_j, energy_j)
                if energy_j < case["reserve_j"]:
                    raise RuntimeError("Independent observation crossed the reserve")
                passenger = [s for s in stops if s.stoppingPlaceID not in bay_ids]
                if len(passenger) > len(stable_stops):
                    raise RuntimeError("Passenger stop count increased")
                expected = stable_stops[len(stable_stops) - len(passenger) :]
                if any(
                    s.lane != p["lane_id"] or abs(s.endPos - p["offset_m"]) > 0.001
                    for s, p in zip(passenger, expected, strict=True)
                ):
                    raise RuntimeError("A passenger stop changed or disappeared out of order")
                served = len(stable_stops) - len(passenger)
                if "inserted" in result and tuple(vehicle.getRoute(ASSET)) != tuple(
                    journey.spec["route_edges"]
                ):
                    raise RuntimeError("The prepared road sequence changed after insertion")
                if (
                    "inserted" not in result
                    and working_s >= 2
                    and speed > 0
                    and not lane.startswith(":")
                ):
                    if case.get("detour"):
                        assigned, journey = apply_detour(connection, journey, lane)
                        result["detour_route_edges"] = list(journey.spec["route_edges"])
                    insert_on_route_charge(
                        connection,
                        ASSET,
                        journey,
                        assigned,
                        stop_limit_s=600,
                        minimum_lead_m=25,
                        braking_m_s2=1.5,
                    )
                    result["inserted"] = {"working_s": working_s, "speed_m_s": speed}
                    if executor is not None:
                        executor.active = (bay, 0.12)
                        executor.station_deadline_s = working_s + 600
                        journey.charge_stop = assigned
                if (
                    "target" in result
                    and "resumed" not in result
                    and speed > 0.1
                    and not active_station
                ):
                    result["resumed"] = {"working_s": working_s, "speed_m_s": speed}
                if journey.arrived:
                    ended = True
                    result["destination"] = {"working_s": working_s, "energy_j": energy_j}
                    break
    finally:
        if connection:
            connection.close()
        if process and process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
        result.update(
            ended=ended,
            delivered_j=charged_j,
            minimum_j=minimum_j,
            passenger_stops_served=served,
            steps=ledger.steps,
            maximum_residual_j=ledger.maximum_residual_j,
        )
    if executor is not None:
        outage_at = result.get("injected_outage_working_s")
        snapshot = executor.interruption_snapshot or {}
        return (
            outage_at is not None
            and result.get("last_plant_step_working_s") == outage_at
            and charged_j >= 360_000
            and abs(charged_j - executor.report()["delivered_j"]) <= 0.01
            and len(executor.events) == 1
            and executor.events[0]["reason"] == "plant_power_changed"
            and snapshot.get("status") == "held_at_committed_bay"
            and snapshot["working_s"] == outage_at
            and snapshot["power_w"] == 0
            and abs(snapshot["energy_j"] - ledger.previous_j) <= 0.01
            and minimum_j >= case["reserve_j"]
            and ledger.maximum_residual_j <= 0.01
            and all(name not in result for name in ("target", "resumed", "destination"))
        )
    if recovery:
        meter = {
            item.get("id"): math.fsum(
                float(sample.get("energyCharged")) * 3600 for sample in item.iter("step")
            )
            for item in ET.parse(folder / "charging-stations.xml")
            .getroot()
            .findall("chargingStation")
        }
        result["charging_j_by_bay"] = charge_by_bay
        result["station_meter_j"] = meter
        return (
            ended
            and served == len(stable_stops) - 1
            and result.get("replacement_charging", {}).get("bay_id") == recovery["bay_id"]
            and "resumed" in result
            and minimum_j / case["capacity_j"] >= recovery["planning_floor"]
            and ledger.maximum_residual_j <= 0.01
            and meter.keys() == charge_by_bay.keys()
            and all(
                amount > 0 and abs(meter[name] - amount) <= 0.01
                for name, amount in charge_by_bay.items()
            )
        )
    if queue:
        meter_j = math.fsum(
            float(sample.get("energyCharged")) * 3600
            for sample in ET.parse(folder / "charging-stations.xml").getroot().iter("step")
        )
        result["station_meter_j"] = meter_j
        result["energy_before_first_transfer_j"] = previous_queue_soc_j
        result["supplemental_queue_draw_j"] = supplemental_queue_j
        queue_arrival = result.get("queue_arrival", {})
        if previous_queue_soc_j is not None and queue_arrival and power_release is not None:
            expected_draw = (
                power_release - queue_arrival["working_s"] - STEP_S
            ) * assignment.policy.equipment_w
            measured_draw = queue_arrival["energy_j"] - previous_queue_soc_j
            result["expected_queue_equipment_j"] = expected_draw
            result["measured_queue_equipment_j"] = measured_draw
        return (
            ended
            and charged_j > 0
            and served == len(stable_stops) - 1
            and all(
                name in result
                for name in ("inserted", "queue_arrival", "charging", "target", "resumed")
            )
            and result["queue_arrival"]["working_s"] < opening
            and result["charging"]["working_s"] >= opening + bay.setup_working_s
            and previous_queue_soc_j is not None
            and previous_queue_soc_j < result["queue_arrival"]["energy_j"]
            and abs(measured_draw - expected_draw) <= 0.01
            and minimum_j >= case["capacity_j"] * (0.1 + assignment.policy.margin_soc)
            and abs(meter_j - charged_j) <= 0.01
            and ledger.maximum_residual_j <= 0.01
        )
    if case.get("detour"):
        meter = math.fsum(
            float(sample.get("energyCharged")) * 3600
            for sample in ET.parse(folder / "charging-stations.xml").getroot().iter("step")
        )
        result["station_meter_j"] = meter
        if (
            result.get("detour_route_edges") == list(assignment.baseline_edges)
            or abs(meter - charged_j) > 0.01
            or ledger.maximum_residual_j > 0.01
        ):
            return False
    return (
        ended
        and charged_j > 0
        and served == len(stable_stops) - 1
        and all(name in result for name in ("inserted", "charging", "target", "resumed"))
    )


def main():
    folder = Path(sys.argv[1])
    result = {"passed": False}
    try:
        result["passed"] = observe(folder, result)
    finally:
        (folder / "plant-result.json").write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
