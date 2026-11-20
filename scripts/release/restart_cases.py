"""Exercise each restart through the current command and telemetry services."""

import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from demo_session import DEMO_CASES, run_demo
from extension_session import EXPERIMENT_CASE, EXTENSION_CASE, recording_path, run_extension
from history_session import run_history
from live_speed_evidence import HISTORY, SAMPLES, speeds
from outage_session import DELIVERY_SERVICES, OUTAGE_CASE, run_outage
from prepare_simulation import SOURCE_LABEL, image_tag
from qualify_connected_speed_limit import (
    ASSET,
    ISSUER,
    ROOT,
    prepare,
    read,
    request,
    require,
    running,
    timestamp,
    wait_ready,
    write,
)
from qualify_live_restart import verify_live_restart, wait_for
from qualify_live_speed_command import prepare_live

TARGETS = ("broker", "ingest", "command-api", "db")
SCHEMA_CASE = "schema-upgrade"
HISTORY_CASE = "history-reader"
RETENTION_CASE = "retention-preview"
CASES = (
    *TARGETS,
    "executor-before",
    "executor-after",
    SCHEMA_CASE,
    HISTORY_CASE,
    RETENTION_CASE,
    OUTAGE_CASE,
    EXTENSION_CASE,
    EXPERIMENT_CASE,
    *DEMO_CASES,
)
SERVICES = ("registry", "api", "ingest", "sender", "command-api")

QUEUE_PROBE = """
import json, sqlite3
with sqlite3.connect('file:/spool/queue.db?mode=ro', uri=True) as db:
    print(json.dumps(db.execute(
        'SELECT accepted_count, rejected_count, (SELECT count(*) FROM pending) FROM metadata'
    ).fetchone()))
"""


def single_restart(target, compose, shell, runtime, override, env, tokens, run, output):
    prepare_live(compose, shell, runtime, override, env, tokens, run, False)
    # The service interruption uses wall time. The deterministic source
    # frames can run without pacing because this case measures no latency.
    policy_path = runtime / "secrets/run_policy.json"
    policy = read(policy_path)
    for entry in policy["sources"]:
        entry["expected"]["paced"] = False
    write(policy_path, policy)
    config = read(override)
    source = config["services"]["source"]
    source["command"] = [
        "python",
        "/restart_source.py",
        "--accelerated",
        "--maximum-samples",
        str(SAMPLES),
    ]
    source["volumes"].extend(
        [
            "./scripts/release/gate_restart_source.py:/restart_source.py:ro",
            f"{output}:/restart-control",
        ]
    )
    write(override, config)
    started = time.monotonic()
    compose("up", "-d", "--wait", "--wait-timeout", "45", "--pull", "never", *SERVICES)

    def ready_address(service):
        base = "http://" + compose("port", service, "8000").decode().strip()
        try:
            wait_ready(base)
        except RuntimeError as exc:
            raise RuntimeError(f"{service} readiness at {base}: {exc}") from exc
        return base

    api, command = ready_address("api"), ready_address("command-api")
    startup_s = time.monotonic() - started
    require(startup_s <= 120, "Local startup exceeded 120 seconds")
    sender_id = compose("ps", "-q", "sender").decode().strip()
    require(bool(sender_id) and "\n" not in sender_id, "Expected one owned sender")

    def call(base, path, role, expected=200, body=None):
        status, result = request(
            base, path, tokens[role], json.dumps(body).encode() if body is not None else None
        )
        require(status == expected, f"{path} returned {status}, expected {expected}")
        return result

    def history():
        return call(api, HISTORY.format(asset=ASSET), "reader")

    def queue():
        return json.loads(shell("docker", "exec", sender_id, "python", "-c", QUEUE_PROBE))

    def inspect(ids, services):
        records = json.loads(shell("docker", "inspect", *ids))
        result = {row["Config"]["Labels"]["com.docker.compose.service"]: row for row in records}
        require(
            len(records) == len(services) and set(result) == set(services),
            "Missing or ambiguous service container",
        )
        return result

    command_id = str(uuid4())
    detail = f"/v1/commands/{command_id}"
    now = datetime.now(timezone.utc)
    intent = {
        "schema_version": "evdt-command/1.0.0",
        "command_id": command_id,
        "run_id": run,
        "asset_id": ASSET,
        "issuer": ISSUER,
        "mode": "simulation",
        "capability": "set_speed_limit",
        "parameters": {"value": 4, "unit": "m/s"},
        "reason": "Verify journal continuity across one service restart",
        "issued_at": timestamp(now),
        "expires_at": timestamp(now + timedelta(seconds=30)),
        "not_before_sim_s": 4,
        "expires_sim_s": 6,
    }
    call(command, "/v1/commands", "replay", 401, intent)
    call(command, "/v1/commands", "operator", 201, intent)
    call(command, "/internal/v1/claim", "executor", 200, running(4))
    reserved = call(command, detail, "operator")
    require(
        reserved["state"] == "unknown_outcome"
        and reserved["result"] is None
        and reserved["effect"] is None,
        "Expected an unresolved one-time handoff",
    )
    compose("up", "-d", "--no-deps", "--pull", "never", "source")
    wait_for(lambda: (output / "sample-2-queued").is_file(), "Source did not queue two samples")
    wait_for(lambda: queue() == [2, 0, 0], "Two initial events were not acknowledged")
    initial = history()
    require(len(initial["items"]) == 2, "Initial history length differs")

    one_shot = ("migrate", "command-migrate")
    graph = (*SERVICES, "db", "broker", "source", *one_shot)
    ids = compose("ps", "-aq", *graph).decode().splitlines()
    require(len(ids) == len(graph) and len(set(ids)) == len(ids), "Unexpected owned service graph")
    prior = inspect(ids, graph)
    target_id = prior[target]["Id"]
    shell("docker", "stop", "--time", "1", target_id)
    down = inspect(ids, graph)
    require(not down[target]["State"]["Running"], "Target did not stop")
    pending_during_stop = None
    if target != "db":
        (output / "advance-2").touch()
        wait_for(
            lambda: (output / "sample-3-queued").is_file(), "Source did not queue third sample"
        )
        if target in ("broker", "ingest"):
            require(queue() == [2, 0, 1], "Event did not stay in the queue while delivery was down")
            require(history() == initial, "History changed while delivery was down")
            pending_during_stop = 1
        else:
            wait_for(lambda: queue() == [3, 0, 0], "Coordinator stop interrupted monitoring")
            pending_during_stop = 0

    recovery_start = time.monotonic()
    shell("docker", "start", target_id)
    # Docker can assign a different host port when a stopped container starts.
    api, command = ready_address("api"), ready_address("command-api")
    recovered = inspect(ids, graph)
    require(
        all(
            prior[name]["Id"] == recovered[name]["Id"]
            and recovered[name]["State"]["Running"] == (name not in one_shot)
            and not recovered[name]["State"]["OOMKilled"]
            and (prior[name]["State"]["StartedAt"] != recovered[name]["State"]["StartedAt"])
            == (name == target)
            for name in graph
        ),
        "More than the selected service restarted, or a required service failed",
    )
    if target == "db":
        require(history() == initial, "Database restart lost or changed committed history")
        (output / "advance-2").touch()
        wait_for(
            lambda: (output / "sample-3-queued").is_file(), "Source did not resume after database"
        )
    wait_for(lambda: queue() == [3, 0, 0], "Third original did not drain")
    drain_s = time.monotonic() - recovery_start
    require(drain_s <= 120, "Recovery exceeded 120 seconds")
    require(call(command, detail, "operator") == reserved, "Restart changed the reserved command")
    call(command, "/internal/v1/claim", "executor", 204, running(4))
    require(call(command, detail, "operator") == reserved, "Repeated claim changed durable command")
    (output / "advance-3").touch()
    require(
        shell("docker", "wait", prior["source"]["Id"], timeout=45).strip() == b"0",
        "Source did not finish normally",
    )
    wait_for(lambda: queue() == [SAMPLES, 0, 0], "Final source queue did not drain")
    final = history()
    originals_path = output / "source-report.json"
    compose("cp", "source:/source-output/source-report.json", str(originals_path))
    originals = read(originals_path)
    require(
        final["run_id"] == run
        and final["items"][:2] == initial["items"]
        and [row["payload"] for row in final["items"]]
        == [row["body"] for row in originals["events"]]
        and len({row["event_id"] for row in final["items"]}) == SAMPLES
        and originals["passed"],
        "Persisted history differs from the exact generated originals",
    )
    measured = speeds(final, ASSET)
    require(max(measured) > 4.25, "Unactuated motion unexpectedly remained speed limited")
    write(output / "accepted-observations.json", final)
    return {
        "service": target,
        "only_target_restarted": True,
        "startup_s": startup_s,
        "third_event_recovery_s": drain_s,
        "pending_during_stop": pending_during_stop,
        "accepted_originals": SAMPLES,
        "pending_at_end": 0,
        "reservation_unchanged": True,
        "repeat_claim_refused": True,
        "replay_command_denied": True,
        "peak_observed_speed_m_s": max(measured),
    }


def release_image(inspect, inventory_hash):
    image = json.loads(inspect("docker", "image", "inspect", image_tag(inventory_hash)))
    require(
        len(image) == 1 and image[0]["Config"]["Labels"].get(SOURCE_LABEL) == inventory_hash,
        "Build the dashboard image for this exact source",
    )
    return image[0]["Id"]


def execute(case, output, project, monitor, inventory_hash):
    if case == HISTORY_CASE:
        image = release_image(
            lambda *args: subprocess.check_output(args, cwd=ROOT, timeout=15), inventory_hash
        )
        with monitor(project, output, history=True, allow_replacement=True):
            return run_history(output, image)
    run = str(uuid4())
    runtime = ROOT / ".runtime/release-restarts" / project
    runtime.mkdir(parents=True)
    tokens, env, override = prepare(runtime, output, run)
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
        "ops/compose/compose.extension.yaml"
        if case in (EXTENSION_CASE, EXPERIMENT_CASE)
        else "ops/compose/compose.commands.yaml",
        "-f",
        str(override),
        "--profile",
        "source",
    ]
    if case.startswith("executor-"):
        prefix += ["-f", "ops/compose/compose.live-speed.yaml"]
    result = {"passed": False}
    with (output / "commands.log").open("wb") as log:

        def shell(*argv, timeout=60, include_stderr=False):
            command = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
            log.write(command.stderr)
            log.flush()
            require(command.returncode == 0, f"{argv[:2]} failed, see commands.log")
            return command.stdout + command.stderr if include_stderr else command.stdout

        def compose(*argv, **kwargs):
            return shell(*prefix, *argv, **kwargs)

        try:
            with monitor(
                project,
                output,
                allow_replacement=case.startswith("executor-"),
                restartable_services=DELIVERY_SERVICES if case == OUTAGE_CASE else (),
            ):
                if case == OUTAGE_CASE:
                    image_id = release_image(shell, inventory_hash)
                    result["outage"] = run_outage(
                        compose, shell, runtime, override, env, tokens, run, output, image_id
                    )
                    result["image_id"] = image_id
                elif case in (EXTENSION_CASE, EXPERIMENT_CASE):
                    image_id = release_image(shell, inventory_hash)
                    recording = None
                    if case == EXPERIMENT_CASE:
                        recording = recording_path(ROOT / read(output / "experiment-input.json"))
                    result.update(
                        run_extension(
                            compose,
                            shell,
                            runtime,
                            override,
                            env,
                            tokens,
                            run,
                            output,
                            image_id,
                            recording=recording,
                        )
                    )
                    result["dashboard_image_id"] = image_id
                elif case in DEMO_CASES:
                    image_id = release_image(shell, inventory_hash)
                    result.update(
                        run_demo(
                            case,
                            compose,
                            shell,
                            runtime,
                            override,
                            env,
                            tokens,
                            run,
                            output,
                            image_id,
                        )
                    )
                    result["dashboard_image_id"] = image_id
                elif case in (SCHEMA_CASE, RETENTION_CASE):
                    compose("up", "-d", "--wait", "--wait-timeout", "45", "--pull", "never", "db")
                    if case == SCHEMA_CASE:
                        script = "schema_upgrade_probe.py"
                        dependencies = []
                    else:
                        script = "verify_retention_preview.py"
                        dependencies = [
                            "--volume",
                            "./scripts/release/retention_preview.py:/fixture/retention_preview.py:ro",
                            "--volume",
                            "./benchmarks/workloads.json:/fixture/policy.json:ro",
                        ]
                    probe = compose(
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--volume",
                        f"./scripts/release/{script}:/fixture/{script}:ro",
                        *dependencies,
                        "migrate",
                        "python",
                        f"/fixture/{script}",
                    )
                    checks = json.loads(probe)
                    require(checks["passed"], f"{case} probe did not pass")
                    result.update(checks)
                elif case.startswith("executor-"):
                    boundary = case.removeprefix("executor-") + "_actuation"
                    result.update(
                        verify_live_restart(
                            compose, shell, runtime, override, env, tokens, run, output, boundary
                        )
                    )
                else:
                    result.update(
                        single_restart(
                            case, compose, shell, runtime, override, env, tokens, run, output
                        )
                    )
            result["passed"] = True
        finally:
            compose("down", "--timeout", "1", "--volumes", "--remove-orphans", timeout=45)
            write(output / "case.json", result)
    return result
