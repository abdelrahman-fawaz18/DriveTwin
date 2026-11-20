"""Exercise a killed monitoring source and recovery under the original command journal."""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from live_speed_evidence import HISTORY, SAMPLES, speeds
from qualify_connected_speed_limit import (
    ASSET,
    ISSUER,
    prepare,
    read,
    request,
    require,
    running,
    timestamp,
    wait_ready,
    write,
)
from qualify_live_speed_command import prepare_live

CASES = ("before_actuation", "after_actuation")
SERVICES = ("registry", "api", "ingest", "sender", "command-api")
HISTORY_PATH = HISTORY.format(asset=ASSET)
CLAIM = "/internal/v1/claim"


def wait_for(check, message, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := check():
            return value
        time.sleep(0.3)
    raise RuntimeError(message)


def verify_live_restart(compose, shell, runtime, override, env, tokens, run, output, case):
    prepare_live(compose, shell, runtime, override, env, tokens, run, True)
    configuration = read(override)
    source = configuration["services"]["source"]
    source["init"] = True
    source["command"] = [
        "python",
        "/fixture/interrupt_live_source.py",
        case,
        "--maximum-samples",
        str(SAMPLES),
    ]
    source["volumes"].append(
        "./scripts/commands/interrupt_live_source.py:/fixture/interrupt_live_source.py:ro"
    )
    write(override, configuration)
    print("Starting the live source and command journal for the interruption", flush=True)
    compose("up", "-d", "--wait", "--wait-timeout", "45", "--pull", "never", *SERVICES)
    database = compose("ps", "-q", "db").strip()

    def address(service):
        return "http://" + compose("port", service, "8000").decode().strip()

    def call(base, path, status, role, value=None):
        body = json.dumps(value).encode() if value is not None else None
        actual, result = request(base, path, tokens[role], body)
        require(actual == status, f"{path} expected {status}, received {actual}")
        return result

    def history(base):
        return call(base, HISTORY_PATH, 200, "reader")

    def spool_state():
        probe = """
import json
import sqlite3
with sqlite3.connect('file:/spool/queue.db?mode=ro', uri=True) as conn:
    conn.execute('BEGIN')
    print(json.dumps(dict(zip(('source_epoch','session_id','next_seq','accepted','rejected','pending'),
        conn.execute('SELECT source_epoch,session_id,next_seq,accepted_count,rejected_count, '
                     '(SELECT count(*) FROM pending) FROM metadata WHERE id=1').fetchone()))))
"""
        return json.loads(compose("exec", "-T", "sender", "python", "-c", probe))

    commands, api = address("command-api"), address("api")
    wait_ready(commands)
    wait_ready(api)
    identity = str(uuid.uuid4())
    detail = "/v1/commands/" + identity
    now = datetime.now(timezone.utc)
    command = {
        "schema_version": "evdt-command/1.0.0",
        "command_id": identity,
        "run_id": run,
        "asset_id": ASSET,
        "issuer": ISSUER,
        "mode": "simulation",
        "capability": "set_speed_limit",
        "parameters": {"value": 4, "unit": "m/s"},
        "reason": "Live source restart safety qualification",
        "issued_at": timestamp(now),
        "expires_at": timestamp(now + timedelta(seconds=30)),
        "not_before_sim_s": 4,
        "expires_sim_s": 6,
    }
    call(commands, "/v1/commands", 201, "operator", command)
    compose("up", "-d", "--no-deps", "--pull", "never", "source")
    old_source = compose("ps", "-aq", "source").decode().strip()
    require(bool(old_source) and "\n" not in old_source, "Expected one owned source container")
    wait_for(
        lambda: (
            b"EV-DT source parked for crash" in shell("docker", "logs", "--tail", "35", old_source)
        ),
        "Source never reached the selected crash boundary",
    )
    interrupted = call(commands, detail, 200, "operator")
    require(
        interrupted["state"] == "unknown_outcome"
        and interrupted["attempt_sim_s"] == 4
        and interrupted["result"] is None
        and interrupted["effect"] is None,
        "Interrupted command did not retain an uncertain reservation",
    )
    print("Killing only the parked source container and checking its durable state", flush=True)
    shell("docker", "kill", "--signal", "KILL", old_source)
    require(
        shell("docker", "wait", old_source, timeout=15).strip() == b"137",
        "Source did not exit from the injected kill",
    )
    count = 4 if case == "before_actuation" else 8

    def drained():
        state = spool_state()
        if state["accepted"] == count and state["pending"] == 0:
            return state
        return None

    saved_spool = wait_for(drained, "Interrupted source outbox did not drain")
    require(
        saved_spool["source_epoch"] == 1
        and saved_spool["next_seq"] == count
        and saved_spool["rejected"] == 0,
        "Interrupted source ownership was inconsistent",
    )
    old_history = history(api)
    old_speed = speeds(old_history, ASSET, count=count)
    require(old_history["run_id"] == run, "Wrong interrupted history scope")
    if case == "after_actuation":
        require(
            max(old_speed[5:]) <= 4.05 and 4.0 in old_speed[5:],
            "Accepted readings did not show the cap",
        )
        require(
            b"EV-DT discarded live execution result" in shell("docker", "logs", old_source),
            "The lost result was not injected",
        )
    write(output / "interrupted-observations.json", old_history)

    def disconnected():
        snapshot = call(api, "/v1/snapshot?asset_id=" + ASSET, 200, "reader")
        item = next(i for i in snapshot["items"] if i["signal_id"] == "motion.speed")
        return (item["source_state"], item["freshness"]) == ("disconnected", "stale")

    wait_for(disconnected, "Dashboard data did not mark the interrupted source stale")
    print("Trying to restart the same run and checking that its queue is unchanged", flush=True)
    shell("docker", "start", old_source)
    require(
        shell("docker", "wait", old_source, timeout=15).strip() == b"1"
        and b"Plant restart requires a new run"
        in shell("docker", "logs", old_source, include_stderr=True),
        "Same-run restart was not refused by the source guard",
    )
    require(
        spool_state() == saved_spool and history(api) == old_history,
        "Refused restart changed queue ownership or monitoring history",
    )
    call(commands, CLAIM, 204, "executor", running(4))
    require(
        call(commands, "/v1/commands", 200, "operator", command) == interrupted
        and call(commands, detail, 200, "operator") == interrupted,
        "Original command retry or second handoff changed the reservation",
    )

    print("Starting a fresh run with new credentials and the same durable database", flush=True)
    compose("stop", *SERVICES, "broker")
    fresh_run = str(uuid.uuid4())
    fresh_dir = runtime / "fresh"
    fresh_dir.mkdir()
    fresh_tokens, fresh_env, fresh_override = prepare(fresh_dir, output, fresh_run)
    (fresh_dir / "secrets/admin_db").write_bytes((runtime / "secrets/admin_db").read_bytes())
    old_operator = tokens["operator"]
    env.update(fresh_env)
    tokens.update(fresh_tokens)
    write(override, read(fresh_override))
    prepare_live(compose, shell, fresh_dir, override, env, tokens, fresh_run, True)
    configuration = read(override)
    new_volumes = {
        "broker": ("broker", "/mosquitto/data"),
        "sender": ("spool", "/spool"),
        "source": ("spool", "/spool"),
    }
    services = configuration["services"]
    for name, (volume, destination) in new_volumes.items():
        services.setdefault(name, {}).setdefault("volumes", []).append(
            f"fresh-{volume}:{destination}"
        )
    services["source"]["volumes"].append("fresh-source-output:/source-output")
    configuration["volumes"] = {
        f"fresh-{name}": {} for name in ("broker", "spool", "source-output")
    }
    write(override, configuration)
    for name in ("migrate", "command-migrate"):
        compose("run", "--rm", "--no-deps", "-T", name)
    compose(
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "45",
        "--pull",
        "never",
        "broker",
        *SERVICES,
    )
    require(
        bool(database) and compose("ps", "-q", "db").strip() == database,
        "Fresh run did not keep the original database",
    )
    commands, api = address("command-api"), address("api")
    wait_ready(commands)
    wait_ready(api)
    require(
        request(commands, detail, old_operator)[0] == 401,
        "Fresh scope accepted the retired operator credential",
    )
    call(commands, detail, 404, "operator")
    call(commands, "/v1/commands", 403, "operator", command)
    require(history(api)["items"] == [], "Fresh run inherited earlier observations")
    compose("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "source")
    fresh_source = compose("ps", "-aq", "source").decode().strip()
    require(
        bool(fresh_source) and "\n" not in fresh_source and fresh_source != old_source,
        "Expected a fresh owned source container",
    )
    require(
        shell("docker", "wait", fresh_source, timeout=45).strip() == b"0",
        "Fresh source did not finish the normal drive",
    )

    def fresh_observations():
        current = history(api)
        if len(current["items"]) >= SAMPLES:
            return current
        return None

    fresh_history = wait_for(fresh_observations, "Fresh run did not deliver sixteen events")
    fresh_speed = speeds(fresh_history, ASSET)
    require(
        fresh_history["run_id"] == fresh_run
        and fresh_speed[:4] == old_speed[:4]
        and sum(v > 4.25 for v in fresh_speed[5:]) >= 2,
        "Fresh source did not drive independently of the earlier command",
    )
    write(output / "fresh-observations.json", fresh_history)
    call(commands, CLAIM, 204, "executor", running(4))

    probe = """
import json, sys
from fastapi.encoders import jsonable_encoder
from evdt.command_journal import connection, fetch, scope, view
from evdt.commands import OperatorScope
with connection() as conn:
    target = scope()
    old = OperatorScope(sys.argv[1], target.asset_id, target.issuer)
    row = fetch(conn, old, sys.argv[2])
    attempts = conn.execute('SELECT count(*) AS n FROM commanding.attempts WHERE command_id=%s',
                            (sys.argv[2],)).fetchone()['n']
    print(json.dumps({'old_state':jsonable_encoder(view(row)), 'old_attempts':attempts,
                      'fresh_empty':fetch(conn, target) is None}))
"""
    ledger = json.loads(
        compose(
            "run", "--rm", "--no-deps", "-T", "command-api", "python", "-c", probe, run, identity
        )
    )
    require(
        ledger == {"old_state": interrupted, "old_attempts": 1, "fresh_empty": True},
        "Recovery changed the old command or created a fresh reservation",
    )
    return {
        "restart_case": case,
        "source_exit_code": 137,
        "same_run_restart_refused": True,
        "queue_state_preserved": True,
        "interrupted_source_marked_stale": True,
        "interrupted_originals": count,
        "old_execution_state": interrupted["state"],
        "old_attempts": ledger["old_attempts"],
        "old_result_present": False,
        "old_effect_present": False,
        "interrupted_speed_m_s": old_speed,
        "fresh_originals": len(fresh_speed),
        "fresh_maximum_speed_m_s": max(fresh_speed),
        "fresh_uses_existing_database": True,
        "old_command_refused_in_fresh_run": True,
        "old_command_preserved": True,
        "fresh_command_journal_empty": True,
    }
