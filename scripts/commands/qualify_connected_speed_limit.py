"""Exercise the authenticated command path in a disposable Compose project."""

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from command_wire import ACTUATOR_FAULT_RESULTS, FAULTS, TIMING_CASES, TIMING_TTL_S

ROOT = Path(__file__).resolve().parents[2]
ASSET = "bus-001"
ISSUER = "local-operator"


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def timestamp(value):
    return value.isoformat().replace("+00:00", "Z")


def request(base, path, token=None, data=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(base + path, data=data, headers=headers), timeout=5) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except HTTPError as error:
        with error:
            return error.code, None


def wait_ready(base):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            if request(base, "/health/ready")[0] == 200:
                return
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Service did not become ready")


def prepare(runtime, output, run):
    secret_dir = runtime / "secrets"
    secret_dir.mkdir()
    tokens = {
        name: secrets.token_urlsafe(36)
        for name in ("operator", "executor", "observer", "reader", "replay")
    }
    for key in (
        "admin_db",
        "ingest_db",
        "read_db",
        "registry_db",
        "replay_db",
        "command_db",
        "metrics",
    ):
        (secret_dir / key).write_text(secrets.token_urlsafe(36) + "\n")
    for key in ("operator", "executor", "observer"):
        (secret_dir / ("command_" + key)).write_text(tokens[key] + "\n")
    write(
        secret_dir / "readers.json", {"readers": [{"token": tokens["reader"], "assets": [ASSET]}]}
    )
    write(
        secret_dir / "replay_readers.json",
        {"readers": [{"token": tokens["replay"], "assets": [ASSET]}]},
    )
    scenario = ROOT / "simulation/montreal"
    inputs = {
        name: digest(scenario / name)
        for name in ("mission.json", "stations.json", "import-review.json")
    }
    inputs["roads.osm.xml"] = digest(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
    parameters = read(scenario / "bus-parameters.json")["parameters"]
    capacity = parameters["usable_battery_energy"]["value"] * 3_600_000
    write(
        secret_dir / "run_policy.json",
        {
            "policy_version": "run-policy/1.0.0",
            "mode": "simulation",
            "run_id": run,
            "sources": [
                {
                    "asset_id": ASSET,
                    "source_id": "sumo-adapter",
                    "expected": {
                        "schema_version": "3.0.0",
                        "catalog_version": "1.2.0",
                        "model_version": "synthetic-bus-plant/1.0.0",
                        "input_sha256": {
                            **inputs,
                            "catalog": digest(ROOT / "catalog/monitoring-signals.json"),
                        },
                        "step_s": read(scenario / "mission.json")["step_s"],
                        "paced": False,
                        "signals": {"motion.speed": {"period_s": 1, "freshness_s": 5}},
                        "heartbeat_deadline_s": 5,
                        "capacity_j": capacity,
                        "initial_energy_j": capacity * parameters["initial_soc"]["value"],
                        "packages": {
                            name: "1.28.0"
                            for name in ("eclipse-sumo", "traci", "sumolib", "sumo-data")
                        },
                        "sumo_version": "SUMO 1.28.0",
                        "traci_protocol": 22,
                    },
                }
            ],
        },
    )
    write(
        secret_dir / "map_context.json",
        {
            "type": "FeatureCollection",
            "features": [],
            "run_id": run,
            "asset_id": ASSET,
            "input_sha256": inputs,
        },
    )
    for name in ("broker.conf", "broker.passwords", "broker.acl"):
        (secret_dir / name).touch()
    pins = read(ROOT / "environments/dataflow-images.json")
    image = "evdt-energy-collector:p2"
    env = {
        **os.environ,
        "EVDT_RUN_ID": run,
        "COMMAND_ASSET_ID": ASSET,
        "COMMAND_ISSUER": ISSUER,
        "MONITORING_IMAGE": image,
        "SIMULATOR_IMAGE": image,
        "COMMAND_IMAGE": image,
        "POSTGRES_IMAGE": pins["postgres"]["digest"],
        "MOSQUITTO_IMAGE": pins["mosquitto"]["digest"],
        "SECRET_DIRECTORY": str(secret_dir),
        "COMMAND_EVIDENCE_DIR": str(output),
        "BROKER_CONFIG": str(secret_dir / "broker.conf"),
        "BROKER_PASSWORDS": str(secret_dir / "broker.passwords"),
        "BROKER_ACL": str(secret_dir / "broker.acl"),
        "COMMAND_PORT": "0",
        "API_PORT": "0",
    }
    override = runtime / "monitoring-mounts.json"
    write(
        override,
        {
            "services": {
                service: {
                    "environment": {"PYTHONPATH": "/workspace/src"},
                    "pull_policy": "never",
                    "volumes": [
                        f"./{folder}:/workspace/{folder}:ro"
                        for folder in ("src", "contracts", "catalog", "migrations")
                    ],
                }
                for service in ("migrate", "api")
            }
        },
    )
    return tokens, env, override


def running(sim_time_s):
    return {"sim_time_s": sim_time_s, "state": "running"}


def audit_summary(compose):
    probe = """
import json
from evdt.command_journal import connection
from psycopg.errors import InsufficientPrivilege
with connection() as conn:
    rows = conn.execute('''SELECT operation,reason,command_id IS NOT NULL AS linked,count(*) AS count
        FROM commanding.rejections GROUP BY 1,2,3 ORDER BY 1,2,3''').fetchall()
    columns = {row['column_name'] for row in conn.execute('''SELECT column_name FROM
        information_schema.columns WHERE table_schema='commanding' AND table_name='rejections' ''')}
    assert columns == {'event_id','run_id','asset_id','command_id','operation','reason','recorded_at'}
    for statement in ('UPDATE commanding.rejections SET reason=reason', 'DELETE FROM commanding.rejections'):
        try:
            with conn.transaction():
                conn.execute(statement)
        except InsufficientPrivilege:
            continue
        raise RuntimeError('Audit role can rewrite rejection history')
    counts = {table: conn.execute(f'SELECT count(*) AS n FROM commanding.{table}').fetchone()['n']
              for table in ('requests','deadlines','attempts','results','effects')}
print(json.dumps({'reasons':rows,'journal_counts':counts,'metadata_only':True,'immutable':True}))
"""
    result = json.loads(
        compose("run", "--rm", "--no-deps", "-T", "command-api", "python", "-c", probe)
    )
    present = {(row["operation"], row["reason"], row["linked"]) for row in result["reasons"]}
    expected = {
        ("submit", "credential_required", False),
        ("read", "credential_required", False),
        ("submit", "wrong_run", False),
        ("submit", "wrong_asset", False),
        ("submit", "unauthorized_issuer", False),
        ("submit", "replay_mode", False),
        ("submit", "unsupported_capability", False),
        ("submit", "wall_validity", False),
        ("submit", "payload_too_large", False),
        ("claim", "simulation_validity", True),
    }
    require(expected <= present, "Required rejection audit reasons or identity handling missing")
    return result


def restart_commands(compose, address):
    print("Replacing the command service and restarting PostgreSQL", flush=True)
    before_api = compose("ps", "-q", "command-api")
    compose("stop", "command-api")
    compose("restart", "db")
    compose("up", "-d", "--force-recreate", "--no-deps", "--pull", "never", "command-api")
    require(compose("ps", "-q", "command-api") != before_api, "Command service was not replaced")
    commands = address("command-api")
    wait_ready(commands)
    return commands


def verify_timing(compose, address, expect, commands, detail, operator, executor, raw, drive):
    timing = drive["timing"]
    handoff_expired = timing == "delivery_expiry"
    before = expect(commands, detail, 200, operator)
    require(
        before["state"] == ("rejected_before_actuation" if handoff_expired else "expired")
        and before["attempt_sim_s"] == (4 if handoff_expired else None)
        and before["refusal_reason"] == ("elapsed_deadline" if handoff_expired else None)
        and before["result"] is None
        and before["effect"] is None,
        "Expired command has misleading execution state",
    )
    expires = datetime.fromisoformat(json.loads(raw)["expires_at"])
    if timing == "pause_expiry":
        require(
            datetime.fromisoformat(drive["paused_at_utc"]) < expires
            and datetime.fromisoformat(drive["running_claim_utc"]) >= expires
            and drive["paused_sim_time_s"] == 4
            and drive["paused_seconds"] >= TIMING_TTL_S,
            "Pause did not straddle the wall deadline at the same simulation step",
        )
    else:
        require(
            datetime.fromisoformat(drive["running_claim_utc"]) < expires,
            "Wall expiry masked the selected timing condition",
        )
    replay_time = 2 if timing == "accelerated_expiry" else 4
    if timing == "accelerated_expiry":
        require(
            datetime.now(timezone.utc) < expires,
            "Wall deadline masked the stale-clock replay check",
        )
    expect(commands, "/internal/v1/claim", 204, executor, running(replay_time))
    commands = restart_commands(compose, address)
    require(
        expect(commands, detail, 200, operator) == before, "Restart changed expiry or reservation"
    )
    require(
        expect(commands, "/v1/commands", 200, operator, raw=raw) == before,
        "Exact retry changed the saved command",
    )
    expect(commands, "/internal/v1/claim", 204, executor, running(replay_time))
    if handoff_expired:
        command = json.loads(raw)
        expect(
            commands,
            "/internal/v1/result",
            409,
            executor,
            {
                "schema_version": "evdt-command-execution/1.0.0",
                "status": "acknowledged",
                "attempt_sim_s": 4,
                **{k: command[k] for k in ("command_id", "run_id", "asset_id")},
            },
        )
    audit = audit_summary(compose)
    reasons = {(row["operation"], row["reason"], row["linked"]) for row in audit["reasons"]}
    expected = {
        "pause_expiry": {("claim", "simulation_paused", True), ("claim", "elapsed_deadline", True)},
        "accelerated_expiry": {
            ("claim", "simulation_deadline", True),
            ("claim", "already_closed", True),
        },
        "delivery_expiry": {
            ("execute", "elapsed_deadline", True),
            ("claim", "already_reserved", True),
        },
    }[timing]
    require(expected <= reasons, "Timing refusal was not recorded")
    require(
        audit["journal_counts"]
        == {
            "requests": 1,
            "deadlines": 1,
            "attempts": int(handoff_expired),
            "results": 0,
            "effects": 0,
        },
        "Expired command acquired an unexpected handoff or execution result",
    )
    return {
        "timing": timing,
        "final_execution_state": before["state"],
        "matching_speed_traces": drive["identical_speed_traces"],
        "no_actuation": True,
        "restart_and_retry_refused_handoff": True,
        "audit": audit,
        "paused_seconds": drive.get("paused_seconds"),
        "actuation_refusals": drive["actuation_refusals"],
    }


def verify(compose, tokens, run, output, fault, timing, actuator_fault):
    compose("up", "-d", "--pull", "never", "api", "command-api")

    def address(service):
        return "http://" + compose("port", service, "8000").decode().strip()

    monitoring, commands = address("api"), address("command-api")
    wait_ready(monitoring)
    wait_ready(commands)
    catalog = "/v1/catalog?asset_id=" + ASSET
    operator, executor = tokens["operator"], tokens["executor"]
    observer = tokens["observer"]
    command_id = str(uuid.uuid4())
    route = "/v1/commands"
    detail = route + "/" + command_id
    claim, result, effect = "/internal/v1/claim", "/internal/v1/result", "/internal/v1/effect"

    def expect(base, path, status, token=None, value=None, raw=None):
        body = json.dumps(value).encode() if value is not None else raw
        actual, response = request(base, path, token, body)
        require(actual == status, f"{path} expected {status}, received {actual}")
        return response

    print("Checking command credentials and request admission", flush=True)
    expect(monitoring, catalog, 200, tokens["reader"])
    expect(monitoring, catalog, 401, operator)
    for rejected in (None, tokens["reader"], tokens["replay"], executor, observer):
        expect(commands, route, 401, rejected, {})
        expect(commands, detail, 401, rejected)
    expect(commands, claim, 401, operator, running(4))
    expect(commands, result, 401, operator, {})
    expect(commands, claim, 401, observer, running(4))
    expect(commands, result, 401, observer, {})
    for rejected in (operator, executor, tokens["reader"], tokens["replay"]):
        expect(commands, effect, 401, rejected, {})
    expect(commands, effect, 422, observer, {"confirmed": True})

    def observations(baseline, commanded):
        return {
            "schema_version": "evdt-speed-evidence/1.0.0",
            "command_id": command_id,
            "run_id": run,
            "asset_id": ASSET,
            "signal_id": "motion.speed",
            "unit": "m/s",
            "baseline": baseline,
            "commanded": commanded,
        }

    def command(now, **changes):
        return {
            "schema_version": "evdt-command/1.0.0",
            "command_id": command_id,
            "run_id": run,
            "asset_id": ASSET,
            "issuer": ISSUER,
            "mode": "simulation",
            "capability": "set_speed_limit",
            "parameters": {"value": 4, "unit": "m/s"},
            "reason": "Local connected speed-cap check",
            "issued_at": timestamp(now),
            "expires_at": timestamp(
                now
                + timedelta(
                    seconds=TIMING_TTL_S if timing in ("pause_expiry", "delivery_expiry") else 30
                )
            ),
            "not_before_sim_s": 1 if timing == "accelerated_expiry" else 4,
            "expires_sim_s": 4 if timing == "accelerated_expiry" else 6,
            **changes,
        }

    now = datetime.now(timezone.utc)
    for edits in (
        {"asset_id": "bus-002"},
        {"run_id": str(uuid.uuid4())},
        {"issuer": "other-operator"},
    ):
        expect(commands, route, 403, operator, command(now, **edits))
    expect(commands, route, 422, operator, command(now, mode="replay"))
    expect(commands, route, 422, operator, command(now, capability="charge"))
    expect(commands, route, 422, operator, command(now - timedelta(seconds=31)))
    expect(commands, route, 413, operator, raw=b" " * 2049)

    raw = json.dumps(command(datetime.now(timezone.utc))).encode()
    queued = expect(commands, route, 201, operator, raw=raw)
    require(queued["state"] == "queued" and queued["result"] is None, "Queue result is misleading")
    require(
        expect(commands, route, 200, operator, raw=raw) == queued, "Retry altered queued command"
    )
    expect(commands, route, 409, operator, {**json.loads(raw), "reason": "Different intent"})
    expect(commands, claim, 204, executor, running(0))
    early_result = {
        "schema_version": "evdt-command-execution/1.0.0",
        "command_id": command_id,
        "run_id": run,
        "asset_id": ASSET,
        "attempt_sim_s": 4,
        "status": "acknowledged",
    }
    expect(commands, result, 409, executor, early_result)
    expect(commands, effect, 409, observer, observations([0] * 16, [0] * 16))
    if fault is None and timing is None:
        skew_probe = """
from dataclasses import replace
from datetime import timedelta
from evdt import command_journal as journal
real_clock = journal.host_clock
for offset in (-2, 2):
    current = real_clock()
    journal.host_clock = lambda: replace(current, utc=current.utc+timedelta(seconds=offset))
    assert journal.claim(4, 'running') is None
"""
        compose("run", "--rm", "--no-deps", "-T", "command-api", "python", "-c", skew_probe)
    print("Delivering one command to the simulator and measuring speed", flush=True)
    drive_args = []
    for option, scenario in (
        ("--fault", fault),
        ("--timing", timing),
        ("--actuator-fault", actuator_fault),
    ):
        if scenario is not None:
            drive_args = [
                "python",
                "scripts/commands/run_connected_speed_limit.py",
                option,
                scenario,
            ]
            break
    compose("run", "--rm", "--no-deps", "-T", "command-simulator", *drive_args, timeout=75)
    drive = read(output / "drive/qualification.json")
    require(drive["passed"], "Simulator run or selected interruption failed")
    if timing:
        return verify_timing(
            compose, address, expect, commands, detail, operator, executor, raw, drive
        )
    delivered = output / "drive/commanded/command.json"
    if fault == "before_actuation":
        require(
            not delivered.exists() and drive["execution"] is None, "Command reached the actuator"
        )
    else:
        require(delivered.read_bytes() == raw, "Delivery changed command bytes")
    has_result = fault not in ("before_actuation", "after_actuation")
    expected_state = ACTUATOR_FAULT_RESULTS.get(actuator_fault, "acknowledged")
    saved = expect(commands, detail, 200, operator)
    require(
        saved["state"] == (expected_state if has_result else "unknown_outcome")
        and saved["result"] == (drive["execution"] if has_result else None)
        and saved["attempt_sim_s"] == 4
        and saved["effect"] is None,
        "Execution state misrepresented the interrupted handoff",
    )
    expect(commands, claim, 204, executor, running(4))

    commands = restart_commands(compose, address)
    require(expect(commands, detail, 200, operator) == saved, "Saved result changed on restart")
    require(
        expect(commands, route, 200, operator, raw=raw) == saved,
        "Retry changed the completed command",
    )
    if has_result:
        require(
            expect(commands, result, 200, executor, saved["result"]) == saved,
            "Result retry changed history",
        )
        conflict = "unknown_outcome" if expected_state == "acknowledged" else "acknowledged"
        expect(commands, result, 409, executor, {**saved["result"], "status": conflict})
    expect(commands, claim, 204, executor, running(4))
    expect(monitoring, catalog, 200, tokens["reader"])

    traces = [
        [sample["value"] for sample in read(output / f"drive/{name}/observations.json")]
        for name in ("baseline", "commanded")
    ]
    expect(commands, effect, 422, observer, observations(traces[0], traces[1][:-1]))
    require(expect(commands, detail, 200, operator) == saved, "Incomplete evidence changed state")
    if actuator_fault:
        require(
            drive["actuator_fault"] == actuator_fault
            and drive["actuator_calls"] == [{"asset_id": ASSET, "limit_m_s": 4}]
            and traces[0] == traces[1],
            "The selected actuator fault did not leave the speed trace unchanged",
        )
    print("Reconcile the reserved command from an independent speed observer", flush=True)
    compose("run", "--rm", "--no-deps", "-T", "command-observer", command_id)
    reconciled = expect(commands, detail, 200, operator)
    expected_effect = (
        "not_observed" if fault == "before_actuation" or actuator_fault else "confirmed"
    )
    require(
        reconciled["effect"]["status"] == expected_effect
        and reconciled["effect"]["confirmed"] is (expected_effect == "confirmed"),
        "Unexpected measured effect",
    )
    require({**reconciled, "effect": None} == saved, "Effect evidence changed execution history")
    if fault == "before_actuation":
        require(
            reconciled["effect"]["maximum_baseline_m_s"]
            == reconciled["effect"]["maximum_commanded_m_s"],
            "Before-actuation trace diverged",
        )
    else:
        require(
            reconciled["effect"] == drive["effect"],
            "Observer disagreed with the raw trace comparison",
        )
    compose("run", "--rm", "--no-deps", "-T", "command-observer", command_id)
    require(expect(commands, detail, 200, operator) == reconciled, "Evidence retry changed history")
    expect(commands, effect, 403, observer, {**observations(*traces), "run_id": str(uuid.uuid4())})
    changed = traces[1].copy()
    changed[-1] += 1
    expect(commands, effect, 409, observer, observations(traces[0], changed))
    verify_effect_durability = fault == "after_actuation" or actuator_fault is not None
    if verify_effect_durability:
        commands = restart_commands(compose, address)
        require(
            expect(commands, detail, 200, operator) == reconciled,
            "Observation did not survive restart",
        )
        expect(commands, claim, 204, executor, running(4))

    db_probe = f"""
from evdt.command_journal import connection
from psycopg.errors import InsufficientPrivilege
with connection() as conn:
    assert [conn.execute(f'SELECT count(*) AS n FROM commanding.{{table}}').fetchone()['n']
            for table in ('requests', 'attempts', 'results', 'effects')] == [1, 1, {int(has_result)}, 1]
    for statement in ('SELECT * FROM public.m_observations', 'DELETE FROM commanding.requests',
                      'UPDATE commanding.results SET payload=payload',
                      'UPDATE commanding.effects SET assessment=assessment'):
        try:
            with conn.transaction():
                conn.execute(statement)
        except InsufficientPrivilege:
            continue
        raise RuntimeError('Command role has excess database privileges')
"""
    compose("run", "--rm", "--no-deps", "-T", "command-api", "python", "-c", db_probe)
    audit = audit_summary(compose)
    skew_count = sum(
        row["count"]
        for row in audit["reasons"]
        if (row["operation"], row["reason"], row["linked"]) == ("claim", "clock_skew", True)
    )
    require(
        skew_count == (2 if fault is None else 0), "Clock skew refusals were not durably audited"
    )
    return {
        "http_scope_checks_passed": True,
        "payload_and_expiry_checks_passed": True,
        "forged_result_before_reservation_refused": True,
        "database_role_denial_checks": 4,
        "command_requests": 1,
        "actuation_reservations": 1,
        "execution_results": int(has_result),
        "execution_state_exact_after_api_replacement_and_database_restart": True,
        "command_retry_and_second_handoff_checked": True,
        "result_retry_checked": has_result,
        "independent_speed_effect": reconciled["effect"],
        "effect_durable_across_second_restart_checked": verify_effect_durability,
        "fault": fault,
        "transport_interruptions": drive.get("transport_interruptions", []),
        "final_execution_state": reconciled["state"],
        "audit": audit,
        "injected_coordinator_skew_refusals": skew_count,
        "sumo_version": drive["sumo_version"],
        **(
            {
                "actuator_fault": actuator_fault,
                "actuator_calls": len(drive["actuator_calls"]),
                "identical_speed_traces": True,
                "incomplete_evidence_refused_before_assessment": True,
            }
            if actuator_fault
            else {}
        ),
    }


def main():
    from qualify_command_authority import CASES, verify_authority
    from qualify_command_backup import verify_backup
    from qualify_live_restart import CASES as RESTART_CASES
    from qualify_live_restart import verify_live_restart
    from qualify_live_speed_command import load_reference, prepare_live, verify_live
    from qualify_operator_browser import CASES as OPERATOR_CASES
    from qualify_operator_browser import service_case
    from qualify_pending_commands import CASES as PENDING_CASES
    from qualify_pending_commands import verify_pending

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    case = parser.add_mutually_exclusive_group()
    case.add_argument("--fault", choices=FAULTS)
    case.add_argument("--timing", choices=TIMING_CASES)
    case.add_argument("--authority", choices=CASES)
    case.add_argument("--actuator-fault", choices=ACTUATOR_FAULT_RESULTS)
    case.add_argument("--live", choices=("baseline", "commanded"))
    case.add_argument("--live-restart", choices=RESTART_CASES)
    case.add_argument("--pending", choices=PENDING_CASES)
    case.add_argument("--command-backup", choices=("direct", "arbitration"))
    case.add_argument("--operator-browser", choices=OPERATOR_CASES)
    parser.add_argument("--operator-control", type=Path)
    case.add_argument(
        "--live-authority", choices=("operator_wins", "thermal_fallback", "stale_fence")
    )
    parser.add_argument("--reference-evidence", type=Path)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if os.name == "nt" or not output.is_relative_to(ROOT / "evidence/p3") or output.exists():
        parser.error("Run in Ubuntu using a new evidence/p3 directory")
    reference = args.reference_evidence.resolve() if args.reference_evidence else None
    control = args.operator_control.resolve() if args.operator_control else None
    if bool(args.operator_browser) != bool(control) or (
        control is not None
        and (not control.is_relative_to(ROOT / ".runtime/p3-operator") or not control.is_dir())
    ):
        parser.error("Operator browser checks require a private controller directory")
    needs_reference = args.live == "commanded" or args.live_authority is not None
    if needs_reference != (reference is not None) or (
        reference is not None
        and (not reference.is_relative_to(ROOT / "evidence/p3") or not reference.is_dir())
    ):
        parser.error("The commanded live run needs a previous baseline folder under evidence/p3")
    output.mkdir(parents=True)
    run = str(uuid.uuid4())
    project = (
        "evdt-e2c-" + run.replace("-", "")[:12]
        if args.operator_browser
        else "evdt-p3-command-" + run.split("-")[0]
    )
    runtime = ROOT / ".runtime/p3-commands" / project
    runtime.mkdir(parents=True)
    tokens, env, override = prepare(runtime, output, run)
    if args.operator_browser:
        env["REPLAY_PORT"] = "0"
        config = read(override)
        api = config["services"]["api"]
        api["volumes"].append("./apps/dashboard/dist:/app/dashboard:ro")
        config["services"]["replay"] = api
        write(override, config)
    arbitrated = (
        args.authority
        or args.live_authority
        or args.pending == "sealed-cancel"
        or args.command_backup == "arbitration"
        or args.operator_browser == "sealed"
    )
    if arbitrated:
        env["COMMAND_ISSUER"] = "dispatch-coordinator"
        for role in ("thermal", "energy"):
            tokens[role] = secrets.token_urlsafe(36)
            (runtime / "secrets" / ("command_" + role)).write_text(tokens[role] + "\n")
    report = {"passed": False}
    if args.operator_browser:

        def interrupted(_signal, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
    files = [
        "ops/compose/compose.monitoring.yaml",
        "ops/compose/compose.commands.yaml",
        "ops/compose/compose.arbitration.yaml",
        *[
            p.relative_to(ROOT).as_posix()
            for p in sorted((ROOT / "migrations/commands").glob("*.sql"))
        ],
        *[
            f"src/evdt/{name}.py"
            for name in (
                "commands",
                "command_api",
                "command_journal",
                "command_effect",
                "command_timing",
                "command_authority",
                "mission_intent",
            )
        ],
        *[
            p.relative_to(ROOT).as_posix()
            for p in sorted((ROOT / "contracts/commands").glob("*.json"))
        ],
        *[p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "scripts/commands").glob("*.py"))],
        "catalog/monitoring-signals.json",
        "data/public/montreal-core-v1/roads.osm.xml",
        "simulation/montreal/bus-parameters.json",
        "simulation/montreal/import-review.json",
        "simulation/montreal/mission.json",
        "simulation/montreal/stations.json",
        "src/evdt/contract_profile.py",
        "src/evdt/monitoring/network.py",
        "src/evdt/monitoring/sensors.py",
    ]
    if args.command_backup:
        files.extend(["src/evdt/recovery/database.py", "environments/dataflow-images.json"])
    if args.operator_browser:
        files.extend(
            [
                "ops/compose/compose.operator.yaml",
                "src/evdt/monitoring/api.py",
                "src/evdt/monitoring/operator_routes.py",
                "src/evdt/monitoring/replay_api.py",
            ]
        )
    live_source = args.live or args.live_restart or args.live_authority
    if live_source:
        files = sorted(
            set(files)
            | {
                p.relative_to(ROOT).as_posix()
                for pattern in (
                    "src/**/*.py",
                    "contracts/**/*.json",
                    "catalog/*.json",
                    "migrations/monitoring/*.sql",
                    "simulation/montreal/*.json",
                )
                for p in ROOT.glob(pattern)
            }
            | {"ops/compose/compose.live-speed.yaml", "environments/dataflow-images.json"}
        )
    sources = {path: digest(ROOT / path) for path in files}
    label = f"label=com.docker.compose.project={project}"
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
        str(override),
    ]
    if arbitrated:
        prefix.extend(["-f", "ops/compose/compose.arbitration.yaml"])
    if args.operator_browser:
        prefix.extend(["-f", "ops/compose/compose.operator.yaml"])
    if live_source:
        prefix.extend(["--profile", "source"])
    if needs_reference or args.live_restart:
        prefix.extend(["-f", "ops/compose/compose.live-speed.yaml"])
    with (output / "commands.log").open("wb") as log:

        def shell(
            *argv,
            timeout=65,
            include_stderr=False,
            environment=None,
            input_data=None,
            binary_output=False,
        ):
            result = subprocess.run(
                argv,
                cwd=ROOT,
                env=env if environment is None else environment,
                capture_output=True,
                timeout=timeout,
                input=input_data,
            )
            log.write((b"" if binary_output else result.stdout) + result.stderr + b"\n")
            log.flush()
            if result.returncode:
                raise RuntimeError(f"{argv[0]} command failed with exit {result.returncode}")
            return result.stdout + result.stderr if include_stderr else result.stdout

        def compose(*argv, **kwargs):
            return shell(*prefix, *argv, **kwargs)

        try:
            image_id = (
                shell("docker", "image", "inspect", env["COMMAND_IMAGE"], "--format", "{{.Id}}")
                .decode()
                .strip()
            )
            if args.authority:
                report.update(verify_authority(compose, tokens, run, output, args.authority))
            elif args.operator_browser:
                sys.path.insert(0, str(ROOT / "scripts/release"))
                from qualify_restarts import monitor

                with monitor(project, output, allow_replacement=True):
                    report.update(
                        service_case(compose, env, tokens, run, control, args.operator_browser)
                    )
            elif args.pending:
                checks, _ = verify_pending(compose, tokens, run, args.pending)
                report.update(checks)
            elif args.command_backup:
                report.update(
                    verify_backup(
                        compose, shell, runtime, output, run, tokens, project, args.command_backup
                    )
                )
            elif args.live_restart:
                report.update(
                    verify_live_restart(
                        compose,
                        shell,
                        runtime,
                        override,
                        env,
                        tokens,
                        run,
                        output,
                        args.live_restart,
                    )
                )
                report["image_id"] = image_id
            elif args.live or args.live_authority:
                approved = (
                    None
                    if reference is None
                    else load_reference(
                        reference, [{"path": path, "sha256": sha} for path, sha in sources.items()]
                    )
                )
                if approved is not None:
                    require(
                        approved[1]["image_id"] == image_id, "Live baseline used a different image"
                    )
                prepare_live(
                    compose,
                    shell,
                    runtime,
                    override,
                    env,
                    tokens,
                    run,
                    approved is not None,
                    stale_fence=args.live_authority == "stale_fence",
                )
                report.update(
                    verify_live(compose, shell, tokens, run, output, approved, args.live_authority)
                )
                report["image_id"] = image_id
            else:
                report.update(
                    verify(
                        compose, tokens, run, output, args.fault, args.timing, args.actuator_fault
                    )
                )
            report["passed"] = True
        except (
            OSError,
            ValueError,
            KeyError,
            RuntimeError,
            subprocess.TimeoutExpired,
            KeyboardInterrupt,
        ) as error:
            report["error"] = str(error)
            try:
                compose(
                    "logs",
                    "--no-color",
                    "--tail",
                    "45",
                    "command-migrate",
                    "command-api",
                    "api",
                    *(["source", "ingest", "registry", "sender", "broker"] if live_source else []),
                )
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass  # The original failure stays authoritative if log retrieval also fails.
        finally:
            try:
                compose("down", "--volumes", "--remove-orphans")
                if args.live_restart:
                    # The fresh run replaces these mounts in the Compose model.
                    retired = set(
                        shell("docker", "volume", "ls", "-q", "--filter", label).decode().split()
                    )
                    expected = {
                        f"{project}_{name}" for name in ("broker", "source-output", "spool")
                    }
                    if retired and retired <= expected:
                        shell("docker", "volume", "rm", *sorted(retired))
                report["cleanup_passed"] = not any(
                    (
                        shell("docker", "ps", "-aq", "--filter", label).strip(),
                        shell("docker", "volume", "ls", "-q", "--filter", label).strip(),
                        shell("docker", "network", "ls", "-q", "--filter", label).strip(),
                    )
                )
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                report.update(cleanup_passed=False, cleanup_error=str(error))
            report["source_files"] = [
                {"path": path, "sha256": sha} for path, sha in sources.items()
            ]
            report["source_files_match"] = all(
                digest(ROOT / path) == sha for path, sha in sources.items()
            )
            report["passed"] &= report["cleanup_passed"] and report["source_files_match"]
            write(output / "qualification.json", report)
    print(f"Connected speed limit check passed: {report['passed']}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
