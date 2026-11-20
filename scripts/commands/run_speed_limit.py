"""Run one local operator speed-limit command and compare observed SUMO speed."""

import argparse
import hashlib
import json
import math
import socket
import subprocess
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evdt.command_effect import APPLY_AT_S, STOP_AT_S, compare_speed
from evdt.commands import COMMAND, RESULT, OperatorScope, SpeedLimitExecutor
from evdt.config import ROOT
from evdt.contract_profile import Rejected
from evdt.monitoring.network import build_network
from evdt.monitoring.sensors import SensorBank

SCENARIO = ROOT / "simulation/montreal"
OSM = ROOT / "data/public/montreal-core-v1/roads.osm.xml"
CATALOG = ROOT / "catalog/monitoring-signals.json"


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def command_bytes(scope: OperatorScope, limit: float, reason: str, now: datetime) -> bytes:
    return json.dumps(
        {
            "schema_version": "evdt-command/1.0.0",
            "command_id": str(uuid.uuid4()),
            "run_id": scope.run_id,
            "asset_id": scope.asset_id,
            "mode": "simulation",
            "issuer": scope.issuer,
            "capability": "set_speed_limit",
            "parameters": {"value": limit, "unit": "m/s"},
            "reason": reason,
            "issued_at": utc_text(now),
            "expires_at": utc_text(now + timedelta(seconds=10)),
            "not_before_sim_s": APPLY_AT_S,
            "expires_sim_s": APPLY_AT_S + 2,
        },
        allow_nan=False,
    ).encode()


def write_route(path: Path, mission: dict, asset: str) -> None:
    config = json.loads((SCENARIO / "bus-parameters.json").read_bytes())["parameters"]
    attributes = {
        "length": "length",
        "width": "width",
        "maxSpeed": "maximum_speed",
        "accel": "acceleration",
        "decel": "comfortable_deceleration",
        "emergencyDecel": "emergency_deceleration",
        "tau": "driver_tau",
        "sigma": "driver_sigma",
    }
    routes = ET.Element("routes")
    ET.SubElement(
        routes,
        "vType",
        id="command-bus",
        vClass="bus",
        **{name: str(config[key]["value"]) for name, key in attributes.items()},
    )
    ET.SubElement(routes, "route", id="mission", edges=" ".join(mission["route_edges"]))
    ET.SubElement(routes, "vehicle", id=asset, type="command-bus", route="mission", depart="0")
    ET.ElementTree(routes).write(path, encoding="utf-8", xml_declaration=True)


def drive(
    network: Path,
    route: Path,
    mission: dict,
    scope: OperatorScope,
    output: Path,
    limit: float | None,
    reason: str,
    *,
    receive_command=None,
    publish_result=None,
    verify_handoff=None,
    report_refusal=None,
    actuator_override=None,
) -> dict:
    import sumo
    import traci

    spec = json.loads(CATALOG.read_bytes())["signals"]["motion.speed"]
    step_s = mission["step_s"]
    sensors = SensorBank(
        {"signals": {"motion.speed": spec}},
        step_s,
        {"default_period_s": spec["sample_period_s"], "period_overrides_s": {}},
    )
    executor = SpeedLimitExecutor(scope)
    execution = None
    readings = []
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    argv = [
        str(Path(sumo.SUMO_HOME) / "bin/sumo"),
        "--net-file",
        str(network),
        "--route-files",
        str(route),
        "--remote-port",
        str(port),
        "--step-length",
        str(step_s),
        "--seed",
        str(mission["seed"]),
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
    ]
    output.mkdir()
    with (
        (output / "sumo.log").open("wb") as log,
        subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT) as process,
    ):
        connection = None
        try:
            connection = traci.connect(
                port=port, host="127.0.0.1", numRetries=100, waitBetweenRetries=0.05, proc=process
            )
            _, version = connection.getVersion()
            if version.split()[-1] != "1.28.0" or not math.isclose(
                connection.simulation.getDeltaT(), step_s
            ):
                raise RuntimeError("Unexpected SUMO version or step size")
            apply_limit = (
                connection.vehicle.setMaxSpeed if actuator_override is None else actuator_override
            )
            for tick in range(1, round(STOP_AT_S / step_s) + 1):
                if (limit is not None or receive_command is not None) and tick == round(
                    APPLY_AT_S / step_s
                ) + 1:
                    sim_s = connection.simulation.getTime()
                    now = datetime.now(timezone.utc)
                    raw = (
                        receive_command(sim_s)
                        if receive_command is not None
                        else command_bytes(scope, limit, reason, now)
                    )
                    if raw == b"":
                        raise RuntimeError("No eligible command at the simulator handoff")
                    if raw is not None:
                        (output / "command.json").write_bytes(raw)
                        try:
                            execution = executor.execute(
                                raw,
                                now=datetime.now(timezone.utc),
                                sim_time_s=sim_s,
                                current_limit_m_s=connection.vehicle.getMaxSpeed(scope.asset_id),
                                apply_limit=apply_limit,
                                verify_handoff=verify_handoff,
                            )
                        except Rejected as error:
                            if report_refusal is None:
                                raise
                            report_refusal(raw, str(error))
                        else:
                            if not RESULT.is_valid(execution):
                                raise RuntimeError("Execution result violated its contract")
                            write_json(output / "execution.json", execution)
                            if publish_result is not None:
                                publish_result(execution)

                connection.simulationStep()
                if (
                    connection.simulation.getCollidingVehiclesIDList()
                    or connection.simulation.getStartingTeleportIDList()
                    or scope.asset_id not in connection.vehicle.getIDList()
                ):
                    raise RuntimeError("Unexpected simulator motion or lost vehicle")
                samples = sensors.sample(
                    tick, {"motion.speed": connection.vehicle.getSpeed(scope.asset_id)}
                )
                for sample in samples:
                    if sample["quality"] != "good":
                        raise RuntimeError("Speed measurement is unavailable")
                    readings.append({"sim_time_s": connection.simulation.getTime(), **sample})
        finally:
            try:
                if connection is not None:
                    connection.close(wait=False)
            finally:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
    write_json(output / "observations.json", readings)
    return {"execution": execution, "sumo_version": version}


def read_speed(path: Path) -> list:
    rows = json.loads(path.read_bytes())
    if [row["sim_time_s"] for row in rows] != list(range(1, STOP_AT_S + 1)) or any(
        (row["signal_id"], row["unit"], row["quality"], row["provenance"])
        != ("motion.speed", "m/s", "good", "simulated-observation")
        for row in rows
    ):
        raise RuntimeError("Incomplete or invalid simulated speed trace")
    return [row["value"] for row in rows]


def observe_effect(baseline: Path, commanded: Path, limit: float) -> dict:
    return compare_speed(read_speed(baseline), read_speed(commanded), limit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-m-s", type=float, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    scope = OperatorScope(str(uuid.uuid4()), "bus-001", "local-operator")
    if not math.isfinite(args.limit_m_s) or not COMMAND.is_valid(
        json.loads(command_bytes(scope, args.limit_m_s, args.reason, datetime.now(timezone.utc)))
    ):
        parser.error("Use a 2–5 m/s limit and a nonblank reason of at most 160 characters")
    output.mkdir(parents=True, exist_ok=False)
    paths = [
        Path(__file__).resolve(),
        SCENARIO / "mission.json",
        SCENARIO / "bus-parameters.json",
        SCENARIO / "import-review.json",
        OSM,
        CATALOG,
        ROOT / "src/evdt/commands.py",
        ROOT / "src/evdt/contract_profile.py",
        ROOT / "src/evdt/monitoring/network.py",
        ROOT / "src/evdt/monitoring/sensors.py",
        *sorted((ROOT / "contracts/commands").glob("*.json")),
    ]
    fingerprints = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report = {"passed": False, "limit_m_s": args.limit_m_s, "command_sim_time_s": APPLY_AT_S}
    try:
        mission = json.loads((SCENARIO / "mission.json").read_bytes())
        network = build_network(SCENARIO, OSM, output)
        route = output / "route.rou.xml"
        write_route(route, mission, scope.asset_id)
        baseline_scope = OperatorScope(str(uuid.uuid4()), scope.asset_id, scope.issuer)
        drive(network, route, mission, baseline_scope, output / "baseline", None, args.reason)
        report.update(
            drive(network, route, mission, scope, output / "commanded", args.limit_m_s, args.reason)
        )
        report["effect"] = observe_effect(
            output / "baseline/observations.json",
            output / "commanded/observations.json",
            args.limit_m_s,
        )
        report["sources_match"] = all(
            hashlib.sha256(path.read_bytes()).hexdigest() == digest
            for path, digest in fingerprints.items()
        )
        report["passed"] = (
            report["sources_match"]
            and report["execution"]["status"] == "acknowledged"
            and report["effect"]["confirmed"]
        )
    finally:
        report["source_files"] = [
            {"path": path.relative_to(ROOT).as_posix(), "sha256": digest}
            for path, digest in fingerprints.items()
        ]
        write_json(output / "qualification.json", report)
    print(f"Speed limit check passed: {report['passed']}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
