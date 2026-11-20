"""Use the scoped simulator credential to fetch and report one reserved command."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

from command_wire import ACTUATOR_FAULT_RESULTS, FAULTS, TIMING_CASES, TIMING_TTL_S, post
from run_speed_limit import (
    OSM,
    SCENARIO,
    drive,
    observe_effect,
    read_speed,
    write_json,
    write_route,
)

from evdt.command_authority import FENCE_PROTOCOL
from evdt.command_journal import scope
from evdt.command_timing import ClockLease, host_clock
from evdt.contract_profile import Rejected
from evdt.monitoring.network import build_network


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    case = parser.add_mutually_exclusive_group()
    case.add_argument("--fault", choices=FAULTS)
    case.add_argument("--timing", choices=TIMING_CASES)
    case.add_argument("--authority-test", choices=("stale_fence", "local_protection"))
    case.add_argument("--actuator-fault", choices=ACTUATOR_FAULT_RESULTS)
    args = parser.parse_args()
    output = Path("/evidence/drive")
    output.mkdir(exist_ok=False)
    report = {"passed": False}
    target = scope()
    interruption = []
    refusals = []
    actuator_calls = []
    lease = None
    fence = None
    fenced = os.environ.get("COMMAND_ARBITRATION") == "1"
    if args.authority_test and not fenced:
        parser.error("Authority probes require the opt-in arbitration profile")

    def transmit(endpoint, value):
        data = json.dumps(value, allow_nan=False).encode()
        if args.fault is None:
            return post(endpoint, "executor", data)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("command_wire.py")),
                endpoint,
                "--fault",
                args.fault,
            ],
            input=data,
            capture_output=True,
            timeout=7,
        )
        should_interrupt = endpoint == ("claim" if args.fault == "before_actuation" else "result")
        if should_interrupt:
            if result.returncode != -signal.SIGKILL or result.stdout:
                raise RuntimeError("Expected transport process interruption before output")
            interruption.append({"endpoint": endpoint, "signal": "SIGKILL"})
            return None
        result.check_returncode()
        return result.stdout

    def receive(sim_s):
        nonlocal lease, fence
        claim = {"sim_time_s": sim_s, "state": "running"}
        if fenced:
            claim["fence_protocol"] = FENCE_PROTOCOL
        if args.timing == "pause_expiry":
            report["paused_at_utc"] = datetime.now(timezone.utc).isoformat()
            start = time.monotonic()
            if transmit("claim", {"sim_time_s": sim_s, "state": "paused"}) != b"":
                raise RuntimeError("Paused simulator received a command")
            # The driver advances the simulator only after this synchronous callback returns.
            time.sleep(TIMING_TTL_S + 0.1)
            report["paused_seconds"] = time.monotonic() - start
            report["paused_sim_time_s"] = sim_s
        report["running_claim_utc"] = datetime.now(timezone.utc).isoformat()
        packet = transmit("claim", claim)
        if not packet:
            return (
                None
                if packet is None or args.timing in ("pause_expiry", "accelerated_expiry")
                else b""
            )
        wire, clock, generation = json.loads(packet)
        lease = ClockLease.decode(clock)
        if fenced:
            if (
                not isinstance(generation, str)
                or not generation.isascii()
                or not generation.isdecimal()
                or int(generation) <= 0
            ):
                raise RuntimeError("Fenced command requires a positive authority generation")
            command = json.loads(wire)
            fence = {k: command[k] for k in ("command_id", "run_id", "asset_id")}
            fence["generation"] = int(generation)
            report["received_generation"] = fence["generation"]
        elif generation is not None:
            raise RuntimeError("Executor is not configured for a fenced command")
        return wire.encode()

    def verify_handoff():
        if lease is None:
            raise RuntimeError("Missing clock lease at actuation")
        if args.timing == "delivery_expiry":
            remaining_s = (lease.deadline_ns - host_clock().elapsed_ns) / 1_000_000_000
            time.sleep(max(0, remaining_s) + 0.1)
        if fenced:
            if fence is None:
                raise Rejected("authority_fence_refused")
            proof = dict(fence)
            if args.authority_test == "stale_fence":
                proof["generation"] -= 1
                report["stale_generation_tested"] = proof["generation"]
            try:
                if transmit("verify-fence", proof) != b"":
                    raise RuntimeError("Unexpected fence check response")
            except HTTPError as error:
                with error:
                    if error.code == 409:
                        raise Rejected("authority_fence_refused") from error
                    raise
        lease.verify(host_clock())

    def report_refusal(raw, reason):
        transmit("refusal", {"command_id": json.loads(raw)["command_id"], "reason": reason})
        refusals.append(reason)

    def simulate_actuator_fault(asset_id, limit):
        # The disposable adapter receives the call but leaves SUMO's speed ceiling alone.
        actuator_calls.append({"asset_id": asset_id, "limit_m_s": limit})
        if args.actuator_fault == "failed":
            raise TimeoutError("Injected speed-limit adapter failure")

    try:
        mission = json.loads((SCENARIO / "mission.json").read_bytes())
        network = build_network(SCENARIO, OSM, output)
        route = output / "route.rou.xml"
        write_route(route, mission, target.asset_id)
        if args.authority_test == "local_protection":
            xml = ET.parse(route)
            xml.getroot().find("vType").set("maxSpeed", "3")
            xml.write(route, encoding="utf-8", xml_declaration=True)
        drive(network, route, mission, target, output / "baseline", None, "")
        report.update(
            drive(
                network,
                route,
                mission,
                target,
                output / "commanded",
                None,
                "",
                receive_command=receive,
                publish_result=lambda result: transmit("result", result),
                verify_handoff=verify_handoff,
                report_refusal=report_refusal,
                actuator_override=simulate_actuator_fault if args.actuator_fault else None,
            )
        )
        if report["execution"] is not None:
            command = json.loads((output / "commanded/command.json").read_bytes())
            report["effect"] = observe_effect(
                output / "baseline/observations.json",
                output / "commanded/observations.json",
                command["parameters"]["value"],
            )
        if args.authority_test is not None:
            report["actuation_refusals"] = refusals
            baseline = read_speed(output / "baseline/observations.json")
            commanded = read_speed(output / "commanded/observations.json")
            report["identical_speed_traces"] = baseline == commanded
            report["maximum_commanded_m_s"] = max(commanded)
            expected = (
                "authority_fence_refused"
                if args.authority_test == "stale_fence"
                else "not_a_speed_reduction"
            )
            report["passed"] = (
                report["execution"] is None and baseline == commanded and refusals == [expected]
            )
        elif args.timing is not None:
            report["timing"] = args.timing
            report["actuation_refusals"] = refusals
            report["identical_speed_traces"] = read_speed(
                output / "baseline/observations.json"
            ) == read_speed(output / "commanded/observations.json")
            expected = ["elapsed_deadline"] if args.timing == "delivery_expiry" else []
            report["passed"] = (
                report["execution"] is None
                and report["identical_speed_traces"]
                and refusals == expected
            )
        elif args.actuator_fault is not None:
            report["actuator_fault"] = args.actuator_fault
            report["actuator_calls"] = actuator_calls
            report["passed"] = (
                report["execution"] is not None
                and report["execution"]["status"] == ACTUATOR_FAULT_RESULTS[args.actuator_fault]
                and report["effect"]["status"] == "not_observed"
                and not refusals
                and actuator_calls
                == [{"asset_id": target.asset_id, "limit_m_s": command["parameters"]["value"]}]
            )
        elif args.fault is None:
            report["passed"] = (
                report["execution"]["status"] == "acknowledged" and report["effect"]["confirmed"]
            )
        else:
            report["fault"] = args.fault
            report["transport_interruptions"] = interruption
            expected_execution = args.fault != "before_actuation"
            report["passed"] = (
                len(interruption) == 1 and (report["execution"] is not None) == expected_execution
            )
    finally:
        write_json(output / "qualification.json", report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
