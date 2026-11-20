"""Run and review the bounded P6-C1 cases through the owned Ubuntu stack."""

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / name)
    for name in ("scripts/release_checks", "scripts/monitoring", "scripts/commands", "src")
]

from qualify_connected_speed_limit import ASSET, digest, prepare, read, require, wait_ready, write  # noqa: E402
from smoke_policy import (  # noqa: E402
    INPUT_PATHS,
    WindowsSmokeGuard,
    consume_receipt,
    inventory,
    launch_receipt,
    preflight,
)

RECIPE = "qualification/combined-faults/combined-faults.json"
RELEASE = "qualification/combined-faults/combined-faults-release.json"
NETWORK = ROOT / "evidence/p4/station-arrivals-02/network.net.xml"
STATION_RELEASE = "qualification/charging/station-arrival-release-1.0.0.json"
CATALOG = "simulation/montreal/charging-stations-1.0.0.json"
CASE_NAMES = ("charge", "cool", "protected", "stale-outage", "interrupted-outage")
COMPOSE = (
    "ops/compose/compose.monitoring.yaml",
    "ops/compose/compose.commands.yaml",
    "ops/compose/compose.charging.yaml",
)
INPUTS = (
    *INPUT_PATHS,
    *COMPOSE,
    "scripts/combined_faults",
    "scripts/commands",
    "scripts/charging",
    "scripts/thermal/thermal_advisory.py",
    "scripts/thermal/thermal_intervention.py",
    "qualification/charging",
    "qualification/thermal",
    RECIPE,
)


def source_binding():
    release = read(ROOT / RELEASE)
    actual = inventory(INPUTS)
    reference = read(ROOT / STATION_RELEASE)
    require(
        release["version"] == "phase6-combined-faults-release/1.0.0"
        and release["reviewed_source_inventory"] == actual
        and release["network_sha256"] == digest(NETWORK) == reference["network_sha256"],
        "The combined-fault release or original charging network differs",
    )
    require(tuple(read(ROOT / RECIPE)["cases"]) == CASE_NAMES, "Unexpected case roster")
    require(
        read(ROOT / RECIPE)["automatic_dispatch"] is False, "Automatic authority is out of scope"
    )
    return {"release_sha256": digest(ROOT / RELEASE), "inventory": actual}


def setup(runtime, output, run, shell):
    tokens, env, override = prepare(runtime, output, run)
    secret_dir = runtime / "secrets"
    for name in ("producer1", "command_station_writer", "ingest_mqtt", "adapter_mqtt"):
        (secret_dir / name).write_text(secrets.token_urlsafe(36) + "\n")
    (secret_dir / "reader").write_text(tokens["reader"] + "\n")
    write(
        secret_dir / "writers.json",
        {
            "writers": [
                {
                    "token": (secret_dir / "producer1").read_text().strip(),
                    "run_id": run,
                    "asset_id": ASSET,
                    "source_id": "sumo-adapter",
                }
            ]
        },
    )
    policy_path = secret_dir / "run_policy.json"
    policy = read(policy_path)
    expected = policy["sources"][0]["expected"]
    expected["initial_energy_j"] = 54 * 3_600_000
    expected["signals"]["traction.motor.temperature"] = {"period_s": 1, "freshness_s": 5}
    expected["input_sha256"].update(
        {
            "charging-release": digest(ROOT / STATION_RELEASE),
            "charging-catalog": digest(ROOT / CATALOG),
            "charging-network": digest(NETWORK),
            "p6-combined-recipe": digest(ROOT / RECIPE),
        }
    )
    write(policy_path, policy)
    env["CHARGING_NETWORK_FILE"] = str(NETWORK)
    config = read(override)
    for name in ("registry", "ingest", "sender"):
        config["services"][name] = dict(config["services"]["api"])
    config["services"]["sender"]["environment"] = {
        "PYTHONPATH": "/workspace/src",
        "CATALOG_VERSION": "1.2.0",
    }
    config["services"]["charging-fixture"] = {
        "environment": {
            "PYTHONPATH": "/workspace/src:/workspace:/workspace/scripts/combined_faults:/workspace/scripts/thermal",
            "EVDT_RUN_ID": run,
            "ASSET_ID": ASSET,
            "SOURCE_ID": "sumo-adapter",
            "SPOOL_FILE": "/spool/queue.db",
            "CATALOG_VERSION": "1.2.0",
        },
        "volumes": [
            "spool:/spool",
            "./catalog:/workspace/catalog:ro",
            "./scripts/combined_faults:/workspace/scripts/combined_faults:ro",
            "./scripts/thermal/thermal_advisory.py:/workspace/scripts/thermal/thermal_advisory.py:ro",
            "./scripts/thermal/thermal_intervention.py:/workspace/scripts/thermal/thermal_intervention.py:ro",
            "./qualification/thermal:/workspace/qualification/thermal:ro",
            "./qualification/combined-faults:/workspace/qualification/combined-faults:ro",
        ],
    }
    write(override, config)
    topic = f"evdt/simulation/{run}/{ASSET}/sumo-adapter"
    (secret_dir / "broker.conf").write_text(
        "listener 1883\nallow_anonymous false\npassword_file /mosquitto/config/passwords\n"
        "acl_file /mosquitto/config/acl\npersistence true\npersistence_location /mosquitto/data/\n"
    )
    (secret_dir / "broker.acl").write_text(
        f"user ingest\ntopic read {topic}/observations\ntopic write {topic}/receipts\n"
        f"user adapter-{ASSET}\ntopic write {topic}/observations\ntopic read {topic}/receipts\n"
    )
    (secret_dir / "broker.passwords").write_text(
        "".join(
            name + ":" + (secret_dir / key).read_text().strip() + "\n"
            for name, key in (("ingest", "ingest_mqtt"), (f"adapter-{ASSET}", "adapter_mqtt"))
        )
    )
    shell(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "mosquitto_passwd",
        "-v",
        str(secret_dir) + ":/config",
        env["MOSQUITTO_IMAGE"],
        "-U",
        "/config/broker.passwords",
        env=env,
    )
    return tokens, env, override


def await_marker(process, path, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and process.poll() is None:
        if path.is_file():
            return
        time.sleep(0.1)
    raise RuntimeError(f"Fixture did not reach {path.name}; see the private service log")


def owner(output):
    return "evdt-p6c-" + hashlib.sha256(output.name.encode()).hexdigest()[:16]


def fallback_cleanup(output):
    for case in CASE_NAMES:
        label = "label=com.docker.compose.project=" + owner(output) + "-" + case
        for kind in ("container", "volume", "network"):
            args = ["docker", kind, "ls", "-q", "--filter", label]
            if kind == "container":
                args.append("-a")
            ids = subprocess.check_output(args, cwd=ROOT, timeout=15, text=True).splitlines()
            if ids:
                subprocess.run(
                    ["docker", kind, "rm", *(["-f"] if kind == "container" else []), *ids],
                    cwd=ROOT,
                    timeout=25,
                    check=True,
                    capture_output=True,
                )
            require(
                not subprocess.check_output(args, cwd=ROOT, timeout=15).strip(),
                "Owned cleanup is incomplete",
            )


def review_case(case, output, tokens, base):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    def call(route, role, body=None, status=200):
        request = Request(
            base + "/v1/charging" + route,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + tokens[role], "Content-Type": "application/json"},
        )
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            require(response.code == status, f"Unexpected charging status for {route}")
            return json.load(response)

    record = read(output / "case.json")
    recipe = read(ROOT / RECIPE)
    measured = read(output / "mission/result.json")
    result = {"accepted_messages": record["accepted_messages"], "role_isolation": True}
    require(record["case"] == case and record["asset_id"] == ASSET, "Case or asset differs")
    require(
        record["accepted_messages"]
        == (3 if case.endswith("-outage") else 1 if case == "charge" else 2),
        "Case did not exercise the expected registered samples",
    )
    require(
        record["final_queue"] == {"pending": 0, "accepted": record["accepted_messages"]},
        "Queue not empty",
    )
    path = "/diversions/" + record["command_id"]
    call(path, "replay", status=401)
    if case == "charge":
        trace = output / "mission/measurements.jsonl"
        steps = [json.loads(line) for line in trace.read_text().splitlines()]
        require(digest(trace) == measured["trace_sha256"], "Charging trace fingerprint differs")
        charge = [step for step in steps if step["charge_wh"] > 0]
        events = {event["event"]: event for event in measured["events"]}
        start, target, resumed = (
            events[name] for name in ("charging_observed", "target_observed", "motion_resumed")
        )
        require(
            charge
            and all(
                events[name]["correlation_id"] == record["command_id"]
                for name in ("charging_observed", "target_observed", "motion_resumed")
            )
            and sum(step["charge_wh"] for step in charge) > 0
            and any(step["sim_s"] > target["sim_s"] and step["speed_m_s"] > 0 for step in steps),
            "Independent trace does not show charge and resumed motion",
        )
        approach = [step["battery_wh"] / 1000 for step in steps if step["sim_s"] < start["sim_s"]]
        effect = {
            **{key: record[key] for key in ("run_id", "asset_id", "command_id")},
            "manifest_sha256": call(path, "operator")["payload"]["departure_manifest_sha256"],
            "station_id": start["station"],
            "arrived_sim_s": start["sim_s"],
            "target_sim_s": target["sim_s"],
            "resumed_sim_s": resumed["sim_s"],
            "finished_sim_s": measured["terminal_sim_s"],
            "arrival_kwh": start["energy_kwh"],
            "target_kwh": target["energy_kwh"],
            "finish_kwh": steps[-1]["battery_wh"] / 1000,
            "minimum_kwh": measured["minimum_kwh"],
            "delivered_kwh": sum(step["charge_wh"] for step in charge) / 1000,
            "trace_sha256": digest(trace),
        }
        require(
            approach and measured["completed"] and not measured["reserve_violation"],
            "Charge mission reserve failed",
        )
        call("/effects", "executor", effect, 401)
        call("/effects", "observer", effect)
        require(call(path, "operator")["effect"] == effect, "Effect not reconciled")
        write(output / "independent-charge-effect.json", effect)
        result.update(
            charge_delivered_kwh=effect["delivered_kwh"],
            motion_resumed=True,
            effect_reconciled=True,
        )
    elif case == "cool":
        trace = output / "cooling-trace.jsonl"
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        window = recipe["thermal"]["effect_window_s"]
        require(
            [row["elapsed_s"] for row in rows] == list(range(1, window + 1))
            and all(
                row["pump_w"] == 1200 and row["speed_m_s"] == 0 and row["route"] == rows[0]["route"]
                for row in rows
            ),
            "Cooling trace is incomplete or the parked route changed",
        )
        final = rows[-1]
        effect = final["matched_motor_k"] - final["motor_k"]
        history = read(output / "accepted-observations.json")
        persisted = [
            sample["value"]
            for sample in history["items"][-1]["payload"]["samples"]
            if sample["signal_id"] == "traction.motor.temperature"
        ]
        require(
            effect >= recipe["thermal"]["minimum_motor_effect_k"]
            and record["two_probe_evidence"]
            and record["repeated_cooling_refused"]
            and persisted == [final["motor_k"]],
            "Measured cooling target failed",
        )
        result.update(motor_effect_k=effect, pump_power_w=1200, measured_window_s=window)
    else:
        require(
            record["no_effect"] and record["before"]["route"] == record["after"]["route"],
            "Rejected request changed route",
        )
        require(
            record["before_effect_refusal"] == recipe["cases"][case]["before_effect_refusal"],
            "Incorrect refusal",
        )
        require(call(path, "operator")["effect"] is None, "Rejected case recorded an effect")
        result.update(no_effect=True, refusal=record["before_effect_refusal"])
        if case.endswith("-outage"):
            timeline = record["timeline"]
            order = [
                "cooling_degradation_injected",
                "operator_request_queued",
                "broker_down",
                "stale_snapshot",
                "charger_power_removed",
                "broker_restored",
                "fresh_snapshot",
                "recovery_refused",
            ]
            require(
                record["stale_snapshot_verified"]
                and record["outage_pending_count"] == 1
                and record["recovery_refusal"] == recipe["cases"][case]["recovery_refusal"]
                and record["after"]["station_power_w"] == 0
                and [row["stage"] for row in timeline] == order
                and all(
                    left["elapsed_ns"] < right["elapsed_ns"]
                    for left, right in zip(timeline, timeline[1:])
                )
                and timeline[3]["elapsed_ns"] - timeline[2]["elapsed_ns"]
                >= recipe["outage_wait_s"] * 10**9,
                "Outage, stale state or recovery refusal evidence differs",
            )
            result.update(
                recovery_refusal=record["recovery_refusal"],
                stale_snapshot_verified=True,
                ordered_fault_timeline_verified=True,
            )
    return result


def execute_case(case, output, prefix_tag):
    output.mkdir()
    run = str(uuid4())
    project = prefix_tag + "-" + case
    runtime = ROOT / ".runtime/p6-combined" / project
    runtime.mkdir(parents=True)
    result = {"passed": False, "project": project}
    process = None
    compose = None
    with (output / "commands.log").open("wb") as log:

        def shell(*argv, env=None, timeout=55):
            completed = subprocess.run(
                argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=log, timeout=timeout
            )
            log.write(completed.stdout + b"\n")
            log.flush()
            require(
                completed.returncode == 0,
                f"{argv[:2]} exited {completed.returncode}; see commands.log",
            )
            return completed.stdout.decode().strip()

        try:
            tokens, env, override = setup(runtime, output, run, shell)
            prefix = ["docker", "compose", "--project-directory", ".", "-p", project]
            for file in (*COMPOSE, override):
                prefix += ["-f", str(file)]

            def compose(*argv, timeout=55):
                return shell(*prefix, *argv, env=env, timeout=timeout)

            result["image_ids"] = {
                name: shell("docker", "image", "inspect", env[name], "--format", "{{.Id}}")
                for name in ("COMMAND_IMAGE", "POSTGRES_IMAGE", "MOSQUITTO_IMAGE")
            }
            compose(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "50",
                "--pull",
                "never",
                "registry",
                "api",
                "ingest",
                "sender",
                "command-api",
                timeout=70,
            )
            base = "http://" + compose("port", "command-api", "8000")
            wait_ready(base)
            wait_ready("http://" + compose("port", "api", "8000"))
            secrets_dir = runtime / "secrets"
            mounts = [
                item
                for name in (
                    "run_policy.json",
                    "producer1",
                    "command_operator",
                    "command_executor",
                    "command_observer",
                    "command_station_writer",
                    "reader",
                )
                for item in ("--volume", f"{secrets_dir / name}:/fixture-secrets/{name}:ro")
            ]
            process = subprocess.Popen(
                [
                    *prefix,
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
                    "/workspace/scripts/combined_faults/combined_actor.py",
                    case,
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            if case.endswith("-outage"):
                await_marker(process, output / "stop-broker", 45)
                compose("stop", "--timeout", "1", "broker", "ingest")
                require(
                    not compose("ps", "--status", "running", "-q", "broker", "ingest"),
                    "Broker or ingestion remained running",
                )
                (output / "broker-down").touch()
                await_marker(process, output / "restore-broker", 30)
                compose("start", "broker")
                compose("start", "ingest")
                (output / "broker-restored").touch()
                result["broker_stopped_and_ingest_restarted"] = True
            require(process.wait(timeout=75) == 0, "Combined actor failed; see commands.log")
            result.update(review_case(case, output, tokens, base))
            result["passed"] = True
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            result["error"] = str(exc)
        finally:
            if compose is not None:
                try:
                    compose("down", "--timeout", "1", "--volumes", "--remove-orphans", timeout=45)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    result["cleanup_error"] = str(exc)
            result["cleanup_passed"] = not any(
                shell(
                    "docker",
                    kind,
                    "ls",
                    *(["-a"] if kind == "container" else []),
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=" + project,
                )
                for kind in ("container", "volume", "network")
            )
            result["passed"] &= result["cleanup_passed"]
            write(output / "qualification.json", result)
    return result


def worker(output, cases):
    source = source_binding()
    consume_receipt(output, output, "P6-C1")

    def interrupted(_number, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    prefix = owner(output)
    results = {}
    try:
        for case in cases:
            print("P6-C1 " + case, flush=True)
            results[case] = execute_case(case, output / case, prefix)
            if not results[case]["passed"]:
                break
    finally:
        after = source_binding()
        write(
            output / "worker-result.json",
            {
                "passed": len(results) == len(cases)
                and all(r["passed"] for r in results.values())
                and source == after,
                "source": source,
                "source_after": after,
                "cases": results,
            },
        )
    return 0 if read(output / "worker-result.json")["passed"] else 1


def windows(output, cases):
    from windows_resources import Sampler

    source = source_binding()
    output.mkdir()
    result = {"passed": False, "source": source}
    sampler = None
    try:
        result["preflight"] = preflight(output)
        require(
            result["preflight"]["passed"], "Phase 6 host precheck failed; no fixture was started"
        )
        guard = WindowsSmokeGuard(output / "linux-guard.json", live_clock=True)
        sampler = Sampler(output / "windows-resources.jsonl", on_sample=guard.observe)
        sampler.start()
        deadline = time.monotonic() + 5
        while not guard.first_sample_validated:
            require(
                not (sampler.error or guard.failure) and time.monotonic() < deadline,
                "Initial Windows counter failed",
            )
            time.sleep(0.1)
        launch_receipt(output, output, "P6-C1")
        with (output / "worker.log").open("wb") as log:
            relative = output.relative_to(ROOT).as_posix()
            launch_id = uuid4().hex
            child = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/linux.py",
                    "--run-id",
                    launch_id,
                    "--timeout",
                    "660",
                    "--",
                    "python3",
                    "scripts/combined_faults/qualify_combined_faults.py",
                    "--worker",
                    "--output",
                    relative,
                    "--cases",
                    *cases,
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
            )
            try:
                while child.poll() is None:
                    require(
                        not (sampler.error or guard.failure), str(sampler.error or guard.failure)
                    )
                    time.sleep(0.25)
                result["worker"] = read(output / "worker-result.json")
                result["passed"] = (
                    child.returncode == 0
                    and result["worker"]["passed"]
                    and result["worker"]["source"] == source_binding() == source
                    and not (sampler.error or guard.failure)
                )
            finally:
                if child.poll() is None:
                    subprocess.run(
                        [sys.executable, "scripts/linux.py", "--cancel", launch_id],
                        cwd=ROOT,
                        timeout=15,
                    )
                    child.wait(timeout=70)
                if not result["passed"]:
                    cleanup = subprocess.run(
                        [
                            sys.executable,
                            "scripts/linux.py",
                            "--timeout",
                            "90",
                            "--",
                            "python3",
                            "scripts/combined_faults/qualify_combined_faults.py",
                            "--cleanup",
                            "--output",
                            relative,
                        ],
                        cwd=ROOT,
                        timeout=100,
                        capture_output=True,
                    )
                    (output / "fallback-cleanup.log").write_bytes(cleanup.stdout + cleanup.stderr)
                    result["fallback_cleanup_passed"] = cleanup.returncode == 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result.update(passed=False, error=str(exc))
    finally:
        if sampler:
            sampler.close()
        write(output / "qualification.json", result)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", choices=CASE_NAMES, nargs="+", default=CASE_NAMES)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(source_binding(), indent=2))
        return 0
    output = args.output.resolve() if args.output else None
    if (
        not output
        or not output.is_relative_to(ROOT / "evidence/p6")
        or len(set(args.cases)) != len(args.cases)
    ):
        parser.error("Choose an owned Phase 6 evidence folder and distinct cases")
    if args.cleanup:
        if os.name == "nt" or not (output / "phase6-preflight.json").is_file():
            parser.error("Owned cleanup requires the original Linux fixture path")
        fallback_cleanup(output)
        return 0
    if args.worker and os.name != "nt":
        return worker(output, args.cases)
    if args.worker or os.name != "nt" or output.exists():
        parser.error("Start from Windows with a new evidence folder")
    import msvcrt

    path = ROOT / ".runtime/p6-monitoring.lock"
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise SystemExit("An owned Phase 6 controller already holds the run lock") from exc
        try:
            return windows(output, args.cases)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    raise SystemExit(main())
