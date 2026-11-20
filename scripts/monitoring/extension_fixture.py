"""Run the bounded live-source/durable-uplink checkpoint with owned local services."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--recovery", action="store_true", help="Run P1-D3 recovery instead")
    parser.add_argument("--outage", action="store_true", help="Run P1-E1 outage/storage checks")
    parser.add_argument(
        "--bounded-storage-control",
        type=Path,
        help="Explicit private Windows-controller bridge for bounded outage storage",
    )
    parser.add_argument(
        "--workload-sources", action="store_true", help="Run E2-A source preparation"
    )
    parser.add_argument("--workload-measurement", action="store_true", help="Run E2-B collectors")
    parser.add_argument(
        "--history-volume", action="store_true", help="Source-backed historical database fixture"
    )
    parser.add_argument("--history-samples", type=int, choices=[60, 600, 7200], default=7200)
    parser.add_argument("--history-assets", type=int, choices=range(1, 11), default=10)
    parser.add_argument(
        "--history-backend", action="store_true", help="Run bounded E3-A history semantics"
    )
    parser.add_argument("--sustained-workload", choices=("rehearsal", "W0", "W1"))
    phase6_mode = parser.add_mutually_exclusive_group()
    phase6_mode.add_argument("--phase6-smoke", action="store_true")
    phase6_mode.add_argument("--phase6-capacity", choices=("W0-30", "W1-30"))
    parser.add_argument(
        "--measurement-browser", choices=("windows-edge-visible", "chromium-automated")
    )
    parser.add_argument(
        "--dashboard-control", type=Path, help="Owned ignored browser coordination directory"
    )
    args = parser.parse_args()
    phase6 = args.phase6_smoke or args.phase6_capacity is not None
    if phase6 and (
        not args.workload_measurement
        or args.sustained_workload
        or args.measurement_browser
        or args.bounded_storage_control
        or args.port != 18765
    ):
        parser.error("Use the exact Phase 6 monitoring configuration")
    bounded_control = (
        args.bounded_storage_control.resolve() if args.bounded_storage_control else None
    )
    if bounded_control and (
        not args.outage
        or not bounded_control.is_relative_to(ROOT / ".runtime/bounded-storage")
        or not bounded_control.is_dir()
    ):
        parser.error(
            "Bounded storage currently requires outage mode and an existing canonical private control directory"
        )
    if args.sustained_workload and not (args.workload_measurement and args.measurement_browser):
        parser.error("A wall-time acceptance workload needs the measurement mode and browser")
    profile = None
    if phase6:
        sys.path.insert(0, str(ROOT / "scripts/release_checks"))
        from smoke_policy import SMOKE, consume_receipt, monitoring_profile, verify_sources

        workload = args.phase6_capacity or SMOKE
        profile = monitoring_profile(workload)
        verify_shared = partial(verify_sources, workload=workload)
    else:
        from current_scope import verify_shared
    if args.sustained_workload:
        from workload_profile import select

        profile = select(args.sustained_workload, args.measurement_browser)
    if (
        sum(
            (
                args.recovery,
                args.outage,
                args.workload_sources,
                args.workload_measurement,
                args.history_backend,
                args.history_volume,
            )
        )
        > 1
    ):
        parser.error("Choose one checkpoint")
    if os.name == "nt":
        parser.error("Run through scripts/linux.py")
    output = args.evidence_dir.resolve()
    evidence_root = ROOT / ("evidence/p6" if phase6 else "evidence/p1")
    if not output.is_relative_to(evidence_root) or output.exists():
        parser.error(f"Use a new evidence directory under {evidence_root.relative_to(ROOT)}")
    control = args.dashboard_control.resolve() if args.dashboard_control else None
    if not control and not (
        args.recovery
        or args.outage
        or args.workload_sources
        or args.history_backend
        or args.history_volume
    ):
        parser.error("Use the Windows extension browser controller")
    if control and (
        not control.is_relative_to(ROOT / ".runtime/dashboard") or not control.is_dir()
    ):
        parser.error(
            "Dashboard control must be an existing owned directory under .runtime/dashboard"
        )
    if phase6:
        shared = verify_shared()
        consume_receipt(control, output, workload)
    output.mkdir(parents=True)
    token = uuid.uuid4().hex
    run_id = str(uuid.UUID(token))
    project = (
        "evdt-e3c-"
        if args.history_volume
        else "evdt-e3a-"
        if args.history_backend
        else "evdt-e2c-"
        if profile
        else "evdt-e2b-"
        if args.workload_measurement
        else "evdt-e2a-"
        if args.workload_sources
        else "evdt-e1-"
        if args.outage
        else "evdt-d3-"
        if args.recovery
        else "evdt-d2-"
    ) + token[:12]
    if control:
        (control / "owner.json").write_text(json.dumps({"run_id": run_id, "project": project}))
    runtime = ROOT / ".runtime/p1-uplink" / token
    secret_dir = runtime / "secrets"
    secret_dir.mkdir(parents=True, mode=0o700)
    record = {
        "passed": False,
        "checkpoint": "P1-D2 independent asset and held-back sensor",
        "run_id": run_id,
        "project": project,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "commands": [],
        "phase_1_complete": False,
    }
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("contracts/**/*.json"),
        *ROOT.glob("catalog/*.json"),
        *ROOT.glob("migrations/monitoring/*.sql"),
        *ROOT.glob("simulation/montreal/*"),
        ROOT / "data/public/montreal-core-v1/roads.osm.xml",
        ROOT / "data/public/montreal-core-v1/source.json",
        ROOT / "ops/compose/compose.monitoring.yaml",
        ROOT / "ops/compose/compose.extension.yaml",
        ROOT / "ops/compose/compose.recovery.yaml",
        ROOT / "scripts/monitoring/recovery_fixture.py",
        ROOT / "scripts/monitoring/qualify_recovery.py",
        ROOT / "scripts/monitoring/qualify_outage.py",
        ROOT / "scripts/monitoring/outage_fixture.py",
        ROOT / "scripts/monitoring/bounded_fixture.py",
        ROOT / "scripts/monitoring/storage_lease.py",
        ROOT / "scripts/monitoring/qualify_storage_outage.py",
        ROOT / "scripts/monitoring/workload_source_fixture.py",
        *ROOT.glob("scripts/monitoring/*measurement*.py"),
        ROOT / "scripts/monitoring/workload_profile.py",
        ROOT / "scripts/monitoring/linux_resources.py",
        ROOT / "scripts/monitoring/docker_resources.py",
        ROOT / "scripts/monitoring/windows_resources.py",
        ROOT / "scripts/monitoring/host_memory_policy.py",
        ROOT / "benchmarks/monitoring-host.json",
        ROOT / "scripts/monitoring/workload_scope.py",
        ROOT / "scripts/monitoring/current_scope.py",
        ROOT / "scripts/monitoring/capacity_scope.py",
        ROOT / "scripts/monitoring/runtime_scope.py",
        ROOT / "scripts/monitoring/profile_catalog.py",
        ROOT / "scripts/monitoring/history_fixture.py",
        ROOT / "scripts/monitoring/history_volume_fixture.py",
        ROOT / "scripts/monitoring/history_checkpoint.py",
        ROOT / "scripts/monitoring/history_source_audit.py",
        ROOT / "benchmarks/history.json",
        *ROOT.glob("simulation/montreal-history/*"),
        ROOT / "scripts/monitoring/qualify_history_backend.py",
        *ROOT.glob("qualification/workload/*.json"),
        ROOT / "qualification/workload/core-changes.json",
        ROOT / "qualification/workload/measurement-core.json",
        ROOT / "scripts/monitoring/qualify_workload_sources.py",
        *ROOT.glob("simulation/montreal-sustained/*"),
        ROOT / "benchmarks/workloads.json",
        ROOT / "qualification/extension/shared-core.json",
        *ROOT.glob("simulation/montreal-bus-002/*"),
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "containers/sumo-debian.sources",
        ROOT / "scripts/monitoring/verify_plant.py",
        ROOT / "scripts/monitoring/extension_scope.py",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "environments/dataflow-images.json",
        Path(__file__).resolve(),
        ROOT / ".dockerignore",
        *ROOT.glob("apps/dashboard/src/**/*"),
        *ROOT.glob("apps/dashboard/*.json"),
        ROOT / "apps/dashboard/index.html",
        ROOT / "apps/dashboard/replay.html",
        ROOT / "apps/dashboard/vite.config.mjs",
    ]

    def fingerprints():
        return {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
            if p.is_file()
        }

    record["shared_core"] = shared if phase6 else verify_shared()
    before = fingerprints()
    log = (output / "commands.log").open("w")

    def execute(argv, *, timeout=180, check=True):
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        record["commands"].append({"argv": argv, "exit_code": result.returncode})
        log.write(json.dumps(argv) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        if check and result.returncode:
            raise RuntimeError("Command failed; inspect private commands.log")
        return result.stdout.strip()

    compose_prefix = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "--env-file",
        str(runtime / "compose.env"),
        "-f",
        str(ROOT / "ops/compose/compose.monitoring.yaml"),
        "-f",
        str(ROOT / "ops/compose/compose.extension.yaml"),
        "-p",
        project,
        "--profile",
        "*",
    ]

    def compose(*argv, **kwargs):
        return execute([*compose_prefix, *argv], **kwargs)

    started = False
    try:
        for key in [
            "admin_db",
            "ingest_db",
            "read_db",
            "replay_db",
            "replay_mqtt",
            "ingest_mqtt",
            "adapter_mqtt",
            "adapter2_mqtt",
            "observer_mqtt",
            "registry_db",
            "producer1",
            "producer2",
            "metrics",
        ]:
            (secret_dir / key).write_text(secrets.token_urlsafe(32) + "\n")
        readers = [
            {"label": "all", "token": secrets.token_urlsafe(32), "assets": ["bus-001", "bus-002"]},
            {"label": "bus-001", "token": secrets.token_urlsafe(32), "assets": ["bus-001"]},
            {"label": "bus-002", "token": secrets.token_urlsafe(32), "assets": ["bus-002"]},
        ]
        (secret_dir / "readers.json").write_text(json.dumps({"readers": readers}))
        replay_readers = [{**entry, "token": secrets.token_urlsafe(32)} for entry in readers]
        (secret_dir / "replay_readers.json").write_text(json.dumps({"readers": replay_readers}))
        (secret_dir / "writers.json").write_text(
            json.dumps(
                {
                    "writers": [
                        {
                            "token": (secret_dir / key).read_text().strip(),
                            "run_id": run_id,
                            "asset_id": asset,
                            "source_id": "sumo-adapter",
                        }
                        for key, asset in [("producer1", "bus-001"), ("producer2", "bus-002")]
                    ]
                }
            )
        )
        users = {
            "ingest": "ingest_mqtt",
            "adapter-bus-001": "adapter_mqtt",
            "adapter-bus-002": "adapter2_mqtt",
            "observer": "observer_mqtt",
            "replay-observer": "replay_mqtt",
        }
        password_file = secret_dir / "passwords"
        password_file.write_text(
            "".join(
                name + ":" + (secret_dir / key).read_text().strip() + "\n"
                for name, key in users.items()
            )
        )
        pins = json.loads((ROOT / "environments/dataflow-images.json").read_text())
        node_pin = json.loads((ROOT / "environments/engineering-images.json").read_text())["node"][
            "digest"
        ]
        # Upgrade an ignored plaintext fixture file in place; no password goes in argv/logs.
        execute(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "mosquitto_passwd",
                "-v",
                str(secret_dir) + ":/config",
                pins["mosquitto"]["digest"],
                "-U",
                "/config/passwords",
            ]
        )
        (secret_dir / "mosquitto.conf").write_text(
            "listener 1883\nallow_anonymous false\npassword_file /mosquitto/config/passwords\nacl_file /mosquitto/config/acl\npersistence true\npersistence_location /mosquitto/data/\nautosave_interval 1\nmessage_size_limit 65536\nmax_queued_messages 10000\nlog_type error\nlog_type warning\n"
        )
        acl = f"user ingest\ntopic read evdt/simulation/{run_id}/+/+/observations\ntopic write evdt/simulation/{run_id}/+/+/receipts\n"
        for asset in ["bus-001", "bus-002"]:
            acl += f"user adapter-{asset}\ntopic write evdt/simulation/{run_id}/{asset}/sumo-adapter/observations\ntopic read evdt/simulation/{run_id}/{asset}/sumo-adapter/receipts\n"
        acl += f"user observer\ntopic read evdt/simulation/{run_id}/bus-001/sumo-adapter/observations\n"
        acl += f"user replay-observer\ntopic read evdt/simulation/{run_id}/bus-001/sumo-adapter/observations\n"
        (secret_dir / "acl").write_text(acl)
        app_image = "evdt-p1-runtime:" + token
        sim_image = "evdt-p1-simulator:" + token
        env = {
            "EVDT_RUN_ID": run_id,
            "MONITORING_IMAGE": app_image,
            "SIMULATOR_IMAGE": sim_image,
            "POSTGRES_IMAGE": pins["postgres"]["digest"],
            "MOSQUITTO_IMAGE": pins["mosquitto"]["digest"],
            "API_PORT": str(args.port),
            "REPLAY_PORT": str(args.port + 1),
            "SECRET_DIRECTORY": str(secret_dir),
            "BROKER_CONFIG": str(secret_dir / "mosquitto.conf"),
            "BROKER_PASSWORDS": str(password_file),
            "BROKER_ACL": str(secret_dir / "acl"),
        }
        (runtime / "compose.env").write_text("".join(k + "=" + v + "\n" for k, v in env.items()))
        require_empty = execute(
            ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]
        )
        if require_empty:
            raise RuntimeError("Unique project label unexpectedly already exists")
        print("Building locked backend and optional simulator images", flush=True)
        for target, tag in [("runtime", app_image), ("simulator", sim_image)]:
            execute(
                [
                    "docker",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--target",
                    target,
                    "--build-arg",
                    "PYTHON_IMAGE=" + pins["python"]["digest"],
                    "--build-arg",
                    "UV_IMAGE=" + pins["uv"]["digest"],
                    "--build-arg",
                    "NODE_IMAGE=" + node_pin,
                    "-f",
                    "containers/monitoring.Dockerfile",
                    "-t",
                    tag,
                    ".",
                ],
                timeout=900,
            )
        if phase6:
            record["image_ids"] = {
                tag: execute(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])
                for tag in (app_image, sim_image, env["POSTGRES_IMAGE"], env["MOSQUITTO_IMAGE"])
            }
        if args.history_volume:
            from history_volume_fixture import qualify

            started = True
            record["checkpoint"] = "P1-E3-C2 source-backed history database"
            record["history_volume"] = qualify(
                compose,
                execute,
                compose_prefix,
                runtime,
                output,
                env,
                record,
                samples=args.history_samples,
                asset_count=args.history_assets,
            )
            record["passed"] = record["history_volume"]["passed"]
            return 0 if record["passed"] else 1
        execute(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                str(secret_dir) + ":/policy",
                "-v",
                str(ROOT / "simulation/montreal") + ":/scenario:ro",
                "-v",
                str(ROOT / "simulation/montreal-bus-002") + ":/scenario2:ro",
                "-v",
                str(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
                + ":/input/roads.osm.xml:ro",
                "--entrypoint",
                "python",
                app_image,
                "-m",
                "evdt.monitoring.extension_policy",
                "--run-id",
                run_id,
                "--output",
                "/policy/run_policy.json",
            ]
        )
        execute(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--memory",
                "512m",
                "-v",
                str(secret_dir) + ":/policy",
                "-v",
                str(ROOT / "simulation/montreal") + ":/scenario:ro",
                "-v",
                str(ROOT / "simulation/montreal-bus-002") + ":/scenario2:ro",
                "-v",
                str(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
                + ":/input/roads.osm.xml:ro",
                "--entrypoint",
                "python",
                sim_image,
                "-m",
                "evdt.monitoring.map_context",
                "--run-id",
                run_id,
                "--output",
                "/policy/map_context.json",
            ],
            timeout=120,
        )
        execute(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--memory",
                "512m",
                "-v",
                str(secret_dir) + ":/policy",
                "-v",
                str(ROOT / "simulation/montreal") + ":/scenario:ro",
                "-v",
                str(ROOT / "simulation/montreal-bus-002") + ":/scenario2:ro",
                "-v",
                str(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
                + ":/input/roads.osm.xml:ro",
                "--entrypoint",
                "python",
                sim_image,
                "-m",
                "evdt.monitoring.map_context",
                "--asset-id",
                "bus-002",
                "--scenario",
                "/scenario2",
                "--run-id",
                run_id,
                "--output",
                "/policy/map_context2.json",
            ],
            timeout=120,
        )
        compose("config", "--quiet")
        started = True
        if bounded_control:
            from bounded_fixture import attach

            attach(
                compose,
                execute,
                compose_prefix,
                runtime,
                env,
                record,
                bounded_control,
                [
                    {
                        "asset_id": "bus-001",
                        "source_id": "sumo-adapter",
                        "catalog_version": "1.2.0",
                        "encoded_limit_bytes": 32 * 1024**2,
                        "services": ["source", "sender", "probe"],
                    }
                ],
            )
        if args.history_backend:
            from history_fixture import qualify

            record["checkpoint"] = "P1-E3-A bounded history SQL and export pagination"
            record["history_backend"] = qualify(
                compose, execute, compose_prefix, runtime, output, env, record
            )
            record["passed"] = record["history_backend"]["passed"]
            return 0 if record["passed"] else 1
        if args.workload_sources or args.workload_measurement:
            from workload_source_fixture import qualify

            record["checkpoint"] = (
                "P6-B1 " + args.phase6_capacity
                if args.phase6_capacity
                else "P6-A2 monitoring smoke"
                if args.phase6_smoke
                else "P1-E2-C full wall-time workload"
                if profile
                else "P1-E2-B stage/clock/browser/resource collectors"
                if args.workload_measurement
                else "P1-E2-A sustained source and asset preparation"
            )
            record["sources"] = qualify(
                compose,
                execute,
                compose_prefix,
                runtime,
                output,
                env,
                record,
                control,
                measurement=args.workload_measurement,
                profile=profile,
            )
            if args.workload_measurement and not phase6:
                record["checkpoint"] = (
                    "P1-E2-C full wall-time workload"
                    if profile
                    else "P1-E2-B stage/clock/browser/resource collectors"
                )
            record["passed"] = record["sources"]["passed"]
            return 0 if record["passed"] else 1
        if args.outage:
            from outage_fixture import qualify

            record["checkpoint"] = "P1-E1 sustained outage and queue capacity"
            record["outage"] = qualify(
                compose,
                execute,
                compose_prefix,
                runtime,
                output,
                env,
                record,
                bounded=bounded_control is not None,
            )
            record["passed"] = record["outage"]["passed"]
            return 0 if record["passed"] else 1
        if args.recovery:
            from recovery_fixture import qualify

            record["checkpoint"] = "P1-D3 coordinated application recovery"
            record["recovery"] = qualify(
                compose, execute, compose_prefix, runtime, output, env, readers, record
            )
            record["passed"] = record["recovery"]["passed"]
            return 0 if record["passed"] else 1
        startup_start = time.monotonic()
        print(
            "Starting isolated database, authenticated broker, receiver, API and sender", flush=True
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
            "replay",
            "sender",
            "sender2",
            timeout=120,
        )

        def wait_for(predicate, seconds=180):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    if predicate():
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(0.25)
            raise RuntimeError("Timed out waiting for the extension condition")

        def health():
            with urlopen(f"http://127.0.0.1:{args.port}/health/ready", timeout=3) as response:
                return json.load(response)["ready"]

        wait_for(health)
        elapsed = time.monotonic() - startup_start
        record["startup"] = {"elapsed_s": elapsed, "limit_s": 120, "passed": elapsed <= 120}
        if elapsed > 120:
            raise RuntimeError("Application startup exceeded 120 seconds")
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "MAP_CONTEXT_FILE=/run/secrets/map_context2",
            "probe",
            "python",
            "-c",
            "import os;from pathlib import Path;from evdt.monitoring.repository import connection;from evdt.monitoring.map_context import provision;conn=connection();provision(conn,Path(os.environ['MAP_CONTEXT_FILE']));conn.commit();conn.close()",
        )
        (control / "ready.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "base_url": f"http://127.0.0.1:{args.port}",
                    "replay_base_url": f"http://127.0.0.1:{args.port + 1}",
                    "reader_token": readers[0]["token"],
                    "scoped_token": readers[1]["token"],
                    "second_token": readers[2]["token"],
                    "replay_token": replay_readers[0]["token"],
                }
            )
        )
        wait_for(lambda: (control / "start-source").exists())
        print("Stepping two independent paced vehicle plants", flush=True)
        compose("up", "-d", "source", "source2")
        for service in ("source", "source2"):
            identifier = compose("ps", "-aq", service)
            require_code = execute(["docker", "wait", identifier], timeout=150)
            if require_code != "0":
                raise RuntimeError("An actual vehicle source failed")
        wait_for(
            lambda: (
                json.loads(
                    compose(
                        "exec",
                        "-T",
                        "sender",
                        "python",
                        "-c",
                        "import json;from evdt.monitoring.transport import open_spool;print(json.dumps(open_spool().counts()))",
                    )
                )["pending"]
                == 0
            )
        )
        wait_for(
            lambda: (
                json.loads(
                    compose(
                        "exec",
                        "-T",
                        "sender2",
                        "python",
                        "-c",
                        "import json;from evdt.monitoring.transport import open_spool;print(json.dumps(open_spool().counts()))",
                    )
                )["pending"]
                == 0
            )
        )
        (control / "source-finished").touch()
        wait_for(lambda: (control / "browser-done").exists(), seconds=300)
        record["browser"] = json.loads((control / "browser-result.json").read_text())
        if not record["browser"].get("passed"):
            raise RuntimeError("Extension browser checks failed")
        record["extension"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.monitoring.extension_probe",
            )
        )
        record["plants"] = {}
        for asset, version, scenario in [
            ("bus-001", "1.2.0", "/scenario"),
            ("bus-002", "1.3.0", "/scenario2"),
        ]:
            plant_output = compose(
                "run",
                "--rm",
                "--no-deps",
                "-v",
                str(ROOT / "simulation/montreal-bus-002") + ":/scenario2:ro",
                "source",
                "python",
                "scripts/monitoring/verify_plant.py",
                "--asset",
                asset,
                "--catalog-version",
                version,
                "--scenario",
                scenario,
                "--output",
                "/source-output/full-" + asset,
                timeout=240,
            )
            lines = [line for line in plant_output.splitlines() if line.startswith("PLANT_RESULT=")]
            if len(lines) != 1:
                raise RuntimeError("Missing full-plant verification")
            record["plants"][asset] = json.loads(lines[0].split("=", 1)[1])
        record["passed"] = (
            all(p["passed"] for p in record["plants"].values()) and record["extension"]["passed"]
        )
    except Exception as exc:
        record["error"] = str(exc)
    finally:
        if started:
            try:
                execute(
                    [*compose_prefix, "cp", "source:/source-output/.", str(output / "source")],
                    check=False,
                )
                execute(
                    [*compose_prefix, "cp", "source2:/source-output/.", str(output / "source2")],
                    check=False,
                )
                compose("logs", "--no-color", check=False)
                compose("down", "--volumes", "--remove-orphans", timeout=90)
                record["cleanup"] = {
                    "containers": execute(
                        [
                            "docker",
                            "ps",
                            "-aq",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ]
                    ),
                    "volumes": execute(
                        [
                            "docker",
                            "volume",
                            "ls",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ]
                    ),
                }
                if phase6:
                    record["cleanup"]["networks"] = execute(
                        [
                            "docker",
                            "network",
                            "ls",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ]
                    )
                if any(record["cleanup"].values()):
                    record["passed"] = False
            except Exception as exc:
                record["passed"] = False
                record["cleanup_error"] = str(exc)
        record["shared_core_after"] = verify_shared()
        record["source_sha256"] = before
        if fingerprints() != before:
            record["passed"] = False
            record["error"] = "Source changed during qualification"
        log.close()
        (output / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"Evidence: {output.relative_to(ROOT)} passed: {record['passed']}", flush=True)
        if (
            args.recovery
            or args.outage
            or args.workload_sources
            or args.workload_measurement
            or args.history_backend
            or args.history_volume
        ) and not record["passed"]:
            return 1
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
