"""Exercise the one-use charging grant and refusal cases at a parked SUMO bus."""

import time
import uuid

from evdt.command_timing import ClockLease, host_clock
from scripts.charging.collect_station_arrivals import VEHICLE
from scripts.charging.qualify_station_arrival_forecast import write

AUTO_CASES = {
    "mission": None,
    "interrupted": None,
    "lost-grant": None,
    "override": "operator_override",
    "thermal": "local_protection",
    "local": "local_protection",
    "stale": "charging_observation_stale_or_future",
    "wall-expiry": "elapsed_deadline",
    "simulation-expiry": "charging_expired_or_early",
    "changed-station": "charging_lineage_mismatch",
}


def exercise(
    case,
    connection,
    output,
    call,
    scope,
    command_id,
    departure,
    stations,
    recommendation,
    timestamp,
    events,
):
    vehicle = connection.vehicle
    request = {**scope, "command_id": command_id}
    claim = {**request, "sim_s": departure["sim_s"]}
    submit = "/automatic/diversions"
    grant = "/automatic/actuation"
    expected = AUTO_CASES[case]
    route = tuple(vehicle.getRoute(VEHICLE))
    original_charge_wh = float(vehicle.getParameter(VEHICLE, "device.battery.chargeLevel"))
    denied = None
    reservation = issued = False

    def refuse(path, role, value, reason, status=409):
        reply = call(path, role, value, status)
        if reply["detail"] != reason:
            raise ValueError(f"{path} rejected for {reply['detail']}, expected {reason}")
        return reason

    def parked_observation(**flags):
        vehicle.setSpeed(VEHICLE, 0)
        connection.simulationStep()
        if (
            vehicle.getSpeed(VEHICLE) != 0
            or vehicle.getLanePosition(VEHICLE) != departure["departure_m"]
        ):
            raise ValueError("Guard test moved the stationary departure")
        now = connection.simulation.getTime()
        updated = {
            **departure,
            **flags,
            "sim_s": now,
            "observed_at": timestamp(),
            "observation_id": str(uuid.uuid4()),
            "initial_energy_kwh": float(vehicle.getParameter(VEHICLE, "device.battery.chargeLevel"))
            / 1000,
        }
        call("/departure", "observer", updated)
        return now

    if case == "stale":
        # The actual stored input ages while the simulator stays at its original time.
        time.sleep(5.1)
        denied = refuse(submit, "policy", request, expected)
    else:
        if case == "mission":
            call(submit, "operator", request, 401)
            call(submit, "executor", request, 401)
            call(submit, "observer", request, 401)
            call(submit, "station", request, 401)
            call(submit, "policy", {**request, "run_id": str(uuid.uuid4())}, 403)
            call(submit, "policy", {**request, "station_id": "depot-a"}, 422)
            refuse(
                "/diversions",
                "operator",
                {**request, "decision_sha256": recommendation["decision_sha256"]},
                "charging_automatic_profile_exclusive",
            )
        queued = call(submit, "policy", request)
        if queued["proposal"]["decision_sha256"] != recommendation["decision_sha256"]:
            raise ValueError("Policy decision differs from authenticated readings")
        if case == "mission":
            if call(submit, "policy", request) != queued:
                raise ValueError("Identical policy request changed the proposal")
            refuse(
                submit,
                "policy",
                {**request, "command_id": str(uuid.uuid4())},
                "charging_run_already_has_diversion",
            )
            refuse(grant, "executor", claim, "charging_automatic_reservation_required")
        reserved = call("/claims", "executor", claim)
        if reserved != {"command_id": command_id, "state": "reserved_unknown"}:
            raise ValueError("Reservation released actuation data before the final check")
        reservation = True
        write(output / "claim.json", claim)
        if case == "override":
            call("/automatic/override", "executor", scope, 401)
            if call("/automatic/override", "operator", scope) != {"state": "override_latched"}:
                raise ValueError("Operator override did not latch")
        elif case in ("thermal", "local"):
            flag = "thermal_block" if case == "thermal" else "local_protection_block"
            claim = {**claim, "sim_s": parked_observation(**{flag: True})}
        elif case == "wall-expiry":
            time.sleep(3.05)
        elif case == "simulation-expiry":
            claim = {**claim, "sim_s": queued["proposal"]["expires_sim_s"]}
        elif case == "changed-station":
            now = parked_observation()
            station = queued["candidate"]["station_id"]
            connection.chargingstation.setChargingPower(station, 0)
            if connection.chargingstation.getChargingPower(station) != 0:
                raise ValueError("The selected charger did not lose power")
            call(
                "/stations",
                "station",
                {
                    **stations,
                    "observation_id": str(uuid.uuid4()),
                    "sim_s": now,
                    "observed_at": timestamp(),
                    "stations": [
                        {
                            **row,
                            "observed_sim_s": now,
                            "state": "outage" if row["station_id"] == station else row["state"],
                        }
                        for row in stations["stations"]
                    ],
                },
            )
            claim = {**claim, "sim_s": now}
        if expected:
            denied = refuse(grant, "executor", claim, expected)
        elif case != "interrupted":
            if case == "mission":
                call(grant, "operator", claim, 401)
                call(grant, "policy", claim, 401)
            delivered = call(grant, "executor", claim)
            issued = True
            if delivered != queued:
                raise ValueError("Grant changed the recorded policy decision")
            refuse(grant, "executor", claim, "charging_actuation_already_granted")
            refuse("/claims", "executor", claim, "charging_already_reserved")

    # This measures the no-effect outcome at the simulator, independent of the HTTP response.
    unchanged = (
        route == tuple(vehicle.getRoute(VEHICLE))
        and vehicle.getSpeed(VEHICLE) == 0
        and float(vehicle.getParameter(VEHICLE, "device.battery.energyCharged")) == 0
        and float(vehicle.getParameter(VEHICLE, "device.battery.chargeLevel")) <= original_charge_wh
    )
    if not unchanged:
        raise ValueError("The bus moved or charged before a verified actuator handoff")
    write(
        output / "automatic-probe.json",
        {
            "case": case,
            "scope": scope,
            "command_id": command_id,
            "denial": denied,
            "reservation_recorded": reservation,
            "actuation_granted": issued,
            "route_unchanged_and_no_charge": unchanged,
        },
    )
    if case != "mission":
        return None, command_id
    proposal = delivered["proposal"]
    candidate = delivered["candidate"]
    ClockLease.decode(proposal["wall_lease"]).verify(host_clock())
    if (
        not proposal["valid_from_sim_s"]
        <= connection.simulation.getTime()
        < proposal["expires_sim_s"]
        or vehicle.getSpeed(VEHICLE) != 0
        or connection.chargingstation.getChargingPower(candidate["station_id"]) <= 0
    ):
        raise ValueError("Local actuator refused an expired, moving, or unavailable diversion")
    events.append({"event": "automatic_actuation_grant", "correlation_id": command_id})
    return candidate, command_id
