"""Start owned Compose stacks and qualify registered charging commands."""

import argparse
import json
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/commands"))
from qualify_connected_speed_limit import ASSET, digest, prepare, read, wait_ready, write  # noqa: E402

NETWORK = ROOT / "evidence/p4/station-arrivals-02/network.net.xml"
RELEASE = ROOT / "qualification/charging/station-arrival-release-1.0.0.json"
CATALOG = ROOT / "simulation/montreal/charging-stations-1.0.0.json"
AUTO_PROFILE = ROOT / "qualification/charging/automatic-departure-1.0.0.json"
SOURCES = (
    "ops/compose/compose.monitoring.yaml",
    "ops/compose/compose.commands.yaml",
    "ops/compose/compose.charging.yaml",
    "ops/compose/compose.automatic-charging.yaml",
    "migrations/commands/007_charging_diversions.sql",
    "migrations/commands/008_charging_automatic.sql",
    "src/evdt/charging_api.py",
    "src/evdt/charging_wire.py",
    "src/evdt/command_api.py",
    "src/evdt/command_journal.py",
    "src/evdt/energy/charging_dispatch.py",
    "scripts/commands/qualify_connected_speed_limit.py",
    "scripts/charging/qualify_registered_charging.py",
    "scripts/charging/registered_charging_actor.py",
    "scripts/charging/automatic_charging_cases.py",
    "scripts/charging/run_charging_missions.py",
    "scripts/charging/run_charging_advisor.py",
    "qualification/charging/station-arrival-release-1.0.0.json",
    "qualification/charging/automatic-departure-1.0.0.json",
    "simulation/montreal/charging-stations-1.0.0.json",
)
ROLE_FILES = (
    "run_policy.json",
    "producer1",
    "command_operator",
    "command_executor",
    "command_observer",
    "command_station_writer",
    "charging_probe_reader",
)
AUTOMATIC_CASES = (
    "mission",
    "interrupted",
    "lost-grant",
    "override",
    "thermal",
    "local",
    "stale",
    "wall-expiry",
    "simulation-expiry",
    "changed-station",
)


def verify_refusal(case, mission, measured, call, base, restart):
    probe = read(mission / "automatic-probe.json")
    trace = read(mission / "measurements.jsonl")
    if (
        measured["terminal_reason"] != "handoff_interrupted"
        or measured["completed"]
        or measured["route_changed"]
        or trace["route_before"] != trace["route_after"]
        or not probe["route_unchanged_and_no_charge"]
        or trace["speed_m_s"] != 0
    ):
        raise ValueError("The refused or interrupted command affected the bus")
    command_id = probe["command_id"]
    route = "/diversions/" + command_id
    if case == "stale":
        if probe["reservation_recorded"] or probe["actuation_granted"]:
            raise ValueError("Stale inputs created a command")
        if (
            call(base, route, "operator", expected_status=404)["detail"]
            != "charging_command_not_found"
        ):
            raise ValueError("Stale inputs left a command in storage")
        return {"denial": probe["denial"], "actuation_granted": False, "no_measured_effect": True}
    claim = read(mission / "claim.json")
    current = call(base, route, "operator")
    if not (
        probe["reservation_recorded"]
        and current["state"] == "reserved_unknown"
        and current["actuation_granted"] == probe["actuation_granted"] == (case == "lost-grant")
        and current["effect"] is None
    ):
        raise ValueError("Unexpected durable reservation, actuation grant, or effect")
    recovery = None
    if case in ("interrupted", "lost-grant", "override"):
        base = restart()
        recovered = call(base, route, "operator")
        if recovered != current:
            raise ValueError("Restart lost the unknown state or grant record")
        if call(base, "/claims", "executor", claim, 409)["detail"] != "charging_already_reserved":
            raise ValueError("Restart incorrectly made the automatic diversion claimable")
        recovery = call(base, "/automatic/actuation", "executor", claim, 409)["detail"]
        if recovery != (
            "charging_actuation_already_granted"
            if case == "lost-grant"
            else "charging_reservation_epoch_changed"
        ):
            raise ValueError("Restart allowed or misclassified an uncertain handoff")
        if (
            case == "override"
            and call(
                base,
                "/automatic/diversions",
                "policy",
                {
                    **probe["scope"],
                    "command_id": command_id,
                },
                409,
            )["detail"]
            != "operator_override"
        ):
            raise ValueError("Restart lost the operator override")
        if case == "interrupted":
            fake = {
                **probe["scope"],
                "command_id": command_id,
                "manifest_sha256": read(mission / "registered-inputs.json")["departure"][
                    "manifest_sha256"
                ],
                "station_id": "depot-a",
                "arrived_sim_s": 10,
                "target_sim_s": 20,
                "resumed_sim_s": 30,
                "finished_sim_s": 40,
                "arrival_kwh": 52,
                "target_kwh": 88,
                "finish_kwh": 87,
                "minimum_kwh": 52,
                "delivered_kwh": 36,
                "trace_sha256": "0" * 64,
            }
            if (
                call(base, "/effects", "observer", fake, 409)["detail"]
                != "charging_effect_without_automatic_grant"
            ):
                raise ValueError("An effect could be recorded without a grant")
    return {
        "denial": probe["denial"],
        "actuation_granted": probe["actuation_granted"],
        "recovery_denial": recovery,
        "no_measured_effect": True,
    }


def qualify(output, interrupt, automatic_case=None):
    run_id = str(uuid.uuid4())
    project = "evdt-p4-charging-" + run_id.split("-")[0]
    runtime = ROOT / ".runtime/p4-charging" / project
    runtime.mkdir(parents=True)
    tokens, env, override = prepare(runtime, output, run_id)
    env["CHARGING_NETWORK_FILE"] = str(NETWORK)
    secret_dir = runtime / "secrets"
    for name in ("producer1", "command_station_writer", "charging_probe_reader"):
        (secret_dir / name).write_text(secrets.token_urlsafe(36) + "\n")
    policy_path = secret_dir / "run_policy.json"
    policy = read(policy_path)
    expected = policy["sources"][0]["expected"]
    expected["initial_energy_j"] = 54 * 3_600_000
    expected["input_sha256"].update(
        {
            "charging-release": digest(RELEASE),
            "charging-catalog": digest(CATALOG),
            "charging-network": digest(NETWORK),
        }
    )
    role_files = ROLE_FILES
    if automatic_case:
        env["COMMAND_ISSUER"] = "charging-qualification"
        expected["input_sha256"]["charging-automatic-profile"] = digest(AUTO_PROFILE)
        tokens["policy"] = secrets.token_urlsafe(36)
        (secret_dir / "command_charging_policy").write_text(tokens["policy"] + "\n")
        role_files = (*role_files, "command_charging_policy")
    write(policy_path, policy)
    write(
        secret_dir / "writers.json",
        {
            "writers": [
                {
                    "token": (secret_dir / "producer1").read_text().strip(),
                    "run_id": run_id,
                    "asset_id": ASSET,
                    "source_id": "sumo-adapter",
                }
            ]
        },
    )
    config = read(override)
    config["services"]["registry"] = config["services"]["api"]
    write(override, config)
    prefix = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "-p",
        project,
        "-f",
        "ops/compose/compose.monitoring.yaml",
        "-f",
        "ops/compose/compose.commands.yaml",
        "-f",
        "ops/compose/compose.charging.yaml",
        *(["-f", "ops/compose/compose.automatic-charging.yaml"] if automatic_case else []),
        "-f",
        str(override),
    ]
    result = {"passed": False, "interrupted_handoff": interrupt}
    if automatic_case:
        result["automatic_case"] = automatic_case
    with (output / "commands.log").open("wb") as log:

        def shell(*argv, timeout=65):
            process = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
            log.write(process.stdout + process.stderr + b"\n")
            log.flush()
            if process.returncode:
                raise RuntimeError(f"{argv[0]} exited {process.returncode}")
            return process.stdout

        def compose(*argv, **kwargs):
            return shell(*prefix, *argv, **kwargs)

        def service_base():
            base = "http://" + compose("port", "command-api", "8000").decode().strip()
            wait_ready(base)
            return base

        def restart():
            compose("restart", "command-api")
            return service_base()

        def call(base, route, role, body=None, expected_status=200):
            query = Request(
                base + "/v1/charging" + route,
                data=None if body is None else json.dumps(body, allow_nan=False).encode(),
                headers={
                    "Authorization": "Bearer " + tokens[role],
                    "Content-Type": "application/json",
                },
            )
            try:
                with urlopen(query, timeout=5) as response:
                    code, value = response.status, json.load(response)
            except HTTPError as response:
                with response:
                    code, value = response.code, json.load(response)
            if code != expected_status:
                raise ValueError(f"{route}: expected {expected_status}, got {code} {value}")
            return value

        try:
            compose("up", "-d", "--wait", "registry", "command-api", timeout=100)
            base = service_base()
            mounts = [
                item
                for name in role_files
                for item in ("--volume", f"{secret_dir / name}:/fixture-secrets/{name}:ro")
            ]
            compose(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                *mounts,
                "--volume",
                f"{output}:/qualification",
                "--entrypoint",
                "python",
                "charging-fixture",
                "-m",
                "scripts.charging.registered_charging_actor",
                "--output",
                "/qualification/mission",
                *(["--interrupt"] if interrupt else []),
                *(["--automatic-case", automatic_case] if automatic_case else []),
                timeout=120,
            )
            mission = output / "mission"
            observed = read(mission / "registered-inputs.json")
            measured = read(mission / "result.json")
            if automatic_case and automatic_case != "mission":
                result.update(
                    verify_refusal(automatic_case, mission, measured, call, base, restart)
                )
                result.update(
                    passed=True,
                    effect_observed=False,
                    registered_inputs_verified=observed["unregistered_and_role_probes_passed"],
                )
                # finally still verifies and records the destruction of this test stack.
                return result
            claim = read(mission / "claim.json")
            path = "/diversions/" + claim["command_id"]
            before_restart = call(base, path, "operator")
            if before_restart["state"] != "reserved_unknown":
                raise ValueError("A reserved command must stay unknown until independent evidence")
            base = restart()
            if (
                call(base, path, "operator") != before_restart
                or call(base, "/claims", "executor", claim, 409)["detail"]
                != "charging_already_reserved"
            ):
                raise ValueError("Restart lost the reservation or reissued the diversion")
            if automatic_case:
                authority = before_restart["payload"].get("authority")
                if (
                    authority
                    != {
                        "mode": "automatic",
                        "issuer": "charging-qualification",
                        "profile_sha256": digest(AUTO_PROFILE),
                    }
                    or not before_restart["actuation_granted"]
                    or call(base, "/automatic/actuation", "executor", claim, 409)["detail"]
                    != "charging_actuation_already_granted"
                ):
                    raise ValueError("Automatic authority or the one-use grant failed recovery")
                result["automatic_authority_verified"] = True
            if interrupt:
                if (
                    measured["route_changed"]
                    or measured["terminal_reason"] != "handoff_interrupted"
                ):
                    raise ValueError("Interrupted simulator was actuated")
            else:
                events = {event["event"]: event for event in measured["events"]}
                start, target, resumed = (
                    events[name]
                    for name in (
                        "charging_observed",
                        "target_observed",
                        "motion_resumed",
                    )
                )
                trace = mission / "measurements.jsonl"
                with trace.open(encoding="utf-8") as stream:
                    approach = []
                    for line in stream:
                        step = json.loads(line)
                        if step["sim_s"] >= start["sim_s"]:
                            break
                        approach.append(step["battery_wh"] / 1000)
                arrival, minimum = approach[-1], min(approach)
                forecast = next(
                    row["energy"]
                    for row in observed["recommendation"]["record"]["candidates"]
                    if row["station_id"] == start["station"]
                )
                for value, field in (
                    (arrival, "arrival_interval_kwh"),
                    (minimum, "minimum_interval_kwh"),
                ):
                    if not forecast[field][0] <= value <= forecast[field][1]:
                        raise ValueError(f"Registered approach falls outside its frozen {field}")
                result["approach_forecast"] = {
                    "measured_arrival_kwh": arrival,
                    "measured_minimum_kwh": minimum,
                    **forecast,
                }
                if (
                    not measured["completed"]
                    or not measured["route_changed"]
                    or measured["reserve_violation"]
                    or digest(trace) != measured["trace_sha256"]
                    or any(
                        event["correlation_id"] != claim["command_id"]
                        for event in (start, target, resumed)
                    )
                ):
                    raise ValueError("Measured diversion and its independent effects do not agree")
                if (
                    automatic_case
                    and events["automatic_actuation_grant"]["correlation_id"] != claim["command_id"]
                ):
                    raise ValueError(
                        "Measured automatic diversion does not match the durable grant"
                    )
                effect = {
                    "run_id": run_id,
                    "asset_id": ASSET,
                    "command_id": claim["command_id"],
                    "manifest_sha256": observed["departure"]["manifest_sha256"],
                    "station_id": start["station"],
                    "arrived_sim_s": start["sim_s"],
                    "target_sim_s": target["sim_s"],
                    "resumed_sim_s": resumed["sim_s"],
                    "finished_sim_s": measured["terminal_sim_s"],
                    "arrival_kwh": start["energy_kwh"],
                    "target_kwh": target["energy_kwh"],
                    "finish_kwh": measured["last_sample"]["battery_wh"] / 1000,
                    "minimum_kwh": measured["minimum_kwh"],
                    "delivered_kwh": measured["charge_delivered_kwh"],
                    "trace_sha256": digest(trace),
                }
                call(base, "/effects", "executor", effect, 401)
                call(base, "/effects", "observer", {**effect, "delivered_kwh": 0}, 409)
                call(base, "/effects", "observer", effect)
                if call(base, path, "operator")["effect"] != effect:
                    raise ValueError("Independent effect did not reconcile the reserved command")
                write(output / "observed-effect.json", effect)
            result.update(
                passed=True,
                registered_inputs_verified=observed["unregistered_and_role_probes_passed"],
                durable_claim_verified=True,
                effect_observed=not interrupt,
                terminal_reason=measured["terminal_reason"],
            )
        except (
            OSError,
            ValueError,
            KeyError,
            RuntimeError,
            URLError,
            subprocess.TimeoutExpired,
        ) as error:
            result["error"] = str(error)
            try:
                compose(
                    "logs",
                    "--no-color",
                    "--tail",
                    "35",
                    "charging-migrate",
                    "command-api",
                    "registry",
                )
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass
        finally:
            try:
                compose("down", "--volumes", "--remove-orphans")
                label = "label=com.docker.compose.project=" + project
                result["cleanup_passed"] = not any(
                    shell(
                        "docker",
                        group,
                        "ls",
                        *(["-a"] if group == "container" else []),
                        "-q",
                        "--filter",
                        label,
                    ).strip()
                    for group in ("container", "volume", "network")
                )
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                result["cleanup_passed"] = False
            result["passed"] &= result["cleanup_passed"]
            write(output / "qualification.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--automatic", action="store_true")
    parser.add_argument("--case", choices=AUTOMATIC_CASES, help="Run one automatic diagnostic case")
    args = parser.parse_args()
    if args.case and not args.automatic:
        parser.error("--case requires --automatic")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "evidence/p4") or output.exists():
        parser.error("Use a new evidence/p4 directory")
    output.mkdir()
    before = {name: digest(ROOT / name) for name in SOURCES}
    cases = (
        (args.case,)
        if args.case
        else (AUTOMATIC_CASES if args.automatic else ("mission", "interrupted"))
    )
    outcomes = {}
    for name in cases:
        folder = output / name
        folder.mkdir()
        outcomes[name] = qualify(
            folder, not args.automatic and name == "interrupted", name if args.automatic else None
        )["passed"]
        print(f"{name}: {outcomes[name]}", flush=True)
    sources_unchanged = before == {name: digest(ROOT / name) for name in SOURCES}
    write(
        output / "qualification.json",
        {
            "passed": all(outcomes.values()) and sources_unchanged,
            "complete_automatic_matrix": args.automatic and not args.case,
            "outcomes": outcomes,
            "mission_passed": outcomes.get("mission"),
            "interruption_passed": outcomes.get("interrupted"),
            "source_sha256": before,
            "sources_unchanged": sources_unchanged,
        },
    )
    return 0 if all(outcomes.values()) and sources_unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
