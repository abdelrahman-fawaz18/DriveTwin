"""Full five-minute single-bus outage and production queue capacity qualification."""

import json
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait(predicate, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if predicate():
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Outage fixture condition exceeded its deadline")


def read_state(path, *, attempts=50, delay_s=0.02):
    """Bound transient DrvFS rename/reader races; persistent faults remain failures."""
    for attempt in range(attempts):
        try:
            return json.loads(path.read_bytes())
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)
    raise ValueError("A positive read-attempt budget is required")


def qualify(compose, execute, prefix, runtime, output, env, record, *, bounded=False):
    result = {
        "passed": False,
        "scope": "P1-E1 single-asset five-minute outage and bounded storage faults",
        "W0_passed": False,
        "W1_passed": False,
        "Q07_fleet_passed": False,
        "Q12_host_resources_passed": False,
    }
    record["outage"] = result
    result["bounded_vehicle_storage"] = bounded

    def bounded_health(stage, connected):
        if bounded:
            result.setdefault("bounded_operational_health", {})[stage] = json.loads(
                compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "probe",
                    "python",
                    "-m",
                    "evdt.workload.bound_storage_health",
                    "connected" if connected else "unavailable",
                    timeout=30,
                )
            )

    project = prefix[prefix.index("-p") + 1]
    control = runtime / "outage-control"
    control.mkdir()
    capacity_volume = project + "-capacity"
    capacity_report = runtime / "capacity-report"
    capacity_report.mkdir()
    owned_capacity = False
    helpers = []
    try:
        # A named Linux-local volume survives the first capacity container's exit.
        require(
            not execute(
                ["docker", "volume", "ls", "-q", "--filter", "name=^" + capacity_volume + "$"]
            ),
            "Capacity volume already exists",
        )
        execute(
            ["docker", "volume", "create", "--label", "com.evdt.owner=" + project, capacity_volume]
        )
        owned_capacity = True
        base = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--memory",
            "384m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:size=16m,mode=1777",
            "-v",
            str(ROOT / "benchmarks") + ":/app/benchmarks:ro",
            "-v",
            str(capacity_report) + ":/report",
            "--entrypoint",
            "python",
        ]
        print(
            "Checking 1,800 worst-size queued packets, full encoded capacity, and container reopen",
            flush=True,
        )
        result["capacity_fill"] = json.loads(
            execute(
                [
                    *base,
                    "-v",
                    capacity_volume + ":/spool",
                    env["MONITORING_IMAGE"],
                    "-m",
                    "evdt.workload.capacity",
                    "fill",
                ],
                timeout=240,
            )
        )
        result["capacity_reopen"] = json.loads(
            execute(
                [
                    *base,
                    "-v",
                    capacity_volume + ":/spool",
                    env["MONITORING_IMAGE"],
                    "-m",
                    "evdt.workload.capacity",
                    "reopen",
                ],
                timeout=120,
            )
        )
        print("Filling a private 4 MiB test filesystem to check rollback and recovery", flush=True)
        result["filesystem_full"] = json.loads(
            execute(
                [
                    *base,
                    "--tmpfs",
                    "/spool:size=4m,uid=10001,gid=10001,mode=0700",
                    env["MONITORING_IMAGE"],
                    "-m",
                    "evdt.workload.capacity",
                    "disk-full",
                ],
                timeout=120,
            )
        )
        compose(
            "up",
            "-d",
            "db",
            "broker",
            "migrate",
            "registry",
            "ingest",
            "api",
            "sender",
            timeout=120,
        )

        def ready():
            with urlopen(f"http://127.0.0.1:{env['API_PORT']}/health/ready", timeout=3) as response:
                return json.load(response)["ready"]

        wait(ready)
        compose(
            "run",
            "--no-deps",
            "-d",
            "--name",
            project + "-monitor",
            "-v",
            str(control) + ":/control",
            "probe",
            "python",
            "-m",
            "evdt.workload.outage_probe",
            "monitor",
        )
        helpers.append(project + "-monitor")
        compose(
            "run",
            "--no-deps",
            "-d",
            "--name",
            project + "-plant",
            "source",
            "python",
            "-m",
            "evdt.monitoring.adapter",
            "--maximum-samples",
            "480",
        )
        helpers.append(project + "-plant")

        def latest():
            return read_state(control / "latest.json")

        print("Running the actual paced source through 60 initial observations", flush=True)
        wait(lambda: latest()["accepted"] >= 60 and latest()["pending"] == 0, seconds=100)
        result["warmup"] = latest()
        bounded_health("warmup", True)
        broker_id = compose("ps", "-aq", "broker")
        receiver_id = compose("ps", "-aq", "ingest")
        compose("stop", "broker", "ingest")
        require(
            execute(["docker", "inspect", broker_id, "--format", "{{.State.Running}} "]) == "false",
            "Broker did not stop",
        )
        require(
            execute(["docker", "inspect", receiver_id, "--format", "{{.State.Running}} "])
            == "false",
            "Receiver did not stop",
        )
        # Stop time starts after both services are confirmed down; conservative full outage.
        outage_start = time.monotonic()
        wait(lambda: latest()["pending"] >= 2, seconds=10)
        baseline = latest()
        outage_database_count = baseline["database"]["events"]
        result["outage_start"] = baseline
        compose("stop", "sender")
        bounded_health("sender_stopped_during_outage", False)
        print("Broker and receiver confirmed stopped: beginning 300 real wall seconds", flush=True)
        next_notice = 30
        previous_marker = baseline["monotonic_s"]
        last_progress = time.monotonic()
        while time.monotonic() - outage_start < 300:
            elapsed = time.monotonic() - outage_start
            current = latest()
            require(
                current["database"]["events"] == outage_database_count,
                "Observation ingress continued during broker/receiver outage",
            )
            require(current["rejected"] == 0, "Source events rejected during outage")
            if current["monotonic_s"] != previous_marker:
                previous_marker = current["monotonic_s"]
                last_progress = time.monotonic()
            require(time.monotonic() - last_progress < 5, "Monitor stopped sampling")
            if elapsed >= next_notice:
                print(
                    f"Verified outage {int(elapsed)}/300 seconds; pending={current['pending']}",
                    flush=True,
                )
                require(
                    execute(["docker", "inspect", broker_id, "--format", "{{.State.Running}} "])
                    == "false",
                    "Broker restarted during measured outage",
                )
                next_notice += 30
            time.sleep(0.5)
        result["outage_seconds"] = round(time.monotonic() - outage_start, 3)
        result["outage_end"] = latest()
        require(result["outage_end"]["pending"] >= 300, "Nominal five-minute backlog missing")
        recovery_start = time.monotonic()
        previous_ready = compose("logs", "--no-color", "ingest").count("monitoring_ingest_ready")
        compose("start", "broker")
        compose("restart", "ingest")
        wait(
            lambda: (
                compose("logs", "--no-color", "ingest").count("monitoring_ingest_ready")
                > previous_ready
            ),
            seconds=30,
        )
        result["receiver_ready_confirmed"] = True
        result["resume_state"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.workload.outage_probe",
                "state",
            )
        )
        # Deliberately deliver latest actual queued bytes before an older original, then
        # let the unchanged production sender drain the rest while generation continues.
        result["out_of_order"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.workload.outage_probe",
                "reorder",
            )
        )
        compose("start", "sender")
        wait(
            lambda: (
                latest()["pending"] == 0
                and latest()["database"]["events"] >= result["outage_end"]["generated"]
            ),
            seconds=120,
        )
        result["recovery_seconds"] = round(time.monotonic() - recovery_start, 3)
        require(
            result["recovery_seconds"] <= 120, "Single-asset backlog missed its recovery deadline"
        )
        result["drained_while_source_running"] = latest()["generated"] < 480
        bounded_health("reconnected_and_drained", True)
        require(
            result["drained_while_source_running"], "Backlog drained only after generation ended"
        )
        print(
            "Backlog drained while the source is still generating; checking post-recovery readings",
            flush=True,
        )
        wait(
            lambda: (
                latest()["generated"] == 480
                and latest()["accepted"] == 480
                and latest()["pending"] == 0
            ),
            seconds=180,
        )
        require(
            execute(["docker", "wait", project + "-plant"], timeout=20) == "0",
            "Actual source failed",
        )
        result["final"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.workload.outage_probe",
                "verify",
            )
        )
        bounded_health("final", True)
        (control / "finish").touch()
        wait(lambda: (control / "monitor-result.json").exists(), seconds=10)
        result["monitor"] = json.loads((control / "monitor-result.json").read_bytes())
        result["post_recovery_observations"] = 480 - result["resume_state"]["generated"]
        require(
            result["post_recovery_observations"] >= 30, "Post-recovery live period was too short"
        )
        for path in control.iterdir():
            if path.is_file():
                (output / path.name).write_bytes(path.read_bytes())
        execute(["docker", "cp", project + "-plant:/source-output/.", str(output / "source")])
        result["passed"] = True
        return result
    except BaseException as exc:
        result["primary_error"] = str(exc) or type(exc).__name__
        raise
    finally:
        (control / "finish").touch()
        if not result.get("passed"):
            # Save available original state before the helper/Compose cleanup.
            # It remains incomplete evidence, never a coordinated restore bundle.
            try:
                partial = output / "partial"
                partial.mkdir(exist_ok=True)
                for path in control.iterdir():
                    if path.is_file() and not path.is_symlink():
                        (partial / path.name).write_bytes(path.read_bytes())
                name = project + "-plant"
                inspected = execute(["docker", "inspect", name], check=False)
                containers = json.loads(inspected or "[]")
                if containers:
                    require(len(containers) == 1, "Ambiguous partial-capture source")
                    container = containers[0]
                    require(
                        container.get("Config", {})
                        .get("Labels", {})
                        .get("com.docker.compose.project")
                        == project,
                        "Refusing partial capture from a differently owned source",
                    )
                    execute(["docker", "stop", "--time", "5", name])
                    roles = {"source": "/source-output", "telemetry": "/spool"}
                    if bounded:
                        roles["critical"] = "/critical"
                    result["partial_capture_exit_scope"] = list(roles)
                    for role, location in roles.items():
                        execute(
                            ["docker", "cp", name + ":" + location + "/.", str(partial / role)],
                            check=False,
                        )
            except Exception as exc:
                result["partial_capture_error"] = str(exc)
        try:
            for name in helpers:
                owner = execute(
                    [
                        "docker",
                        "inspect",
                        name,
                        "--format",
                        '{{index .Config.Labels "com.docker.compose.project"}}',
                    ],
                    check=False,
                )
                if owner:
                    require(owner == project, "Refusing removal of differently owned helper")
                    execute(["docker", "logs", name], check=False)
                    execute(["docker", "rm", "--force", name], check=False)
                    wait(
                        lambda: (
                            not execute(["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"])
                        ),
                        seconds=10,
                    )
        finally:
            if owned_capacity:
                owner = execute(
                    [
                        "docker",
                        "volume",
                        "inspect",
                        capacity_volume,
                        "--format",
                        '{{index .Labels "com.evdt.owner"}}',
                    ]
                )
                require(owner == project, "Refusing removal of differently owned capacity volume")
                execute(["docker", "volume", "rm", capacity_volume])
                result["capacity_volume_removed"] = True
