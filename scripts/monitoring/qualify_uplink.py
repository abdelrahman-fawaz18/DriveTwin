"""Run the bounded live-source/durable-uplink checkpoint with owned local services."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument(
        "--dashboard-control", type=Path, help="Owned ignored browser coordination directory"
    )
    args = parser.parse_args()
    if os.name == "nt":
        parser.error("Run through scripts/linux.py")
    output = args.evidence_dir.resolve()
    if not output.is_relative_to(ROOT / "evidence/p1") or output.exists():
        parser.error("Use a new evidence directory under evidence/p1")
    output.mkdir(parents=True)
    control = args.dashboard_control.resolve() if args.dashboard_control else None
    if control and (
        not control.is_relative_to(ROOT / ".runtime/dashboard") or not control.is_dir()
    ):
        parser.error(
            "Dashboard control must be an existing owned directory under .runtime/dashboard"
        )
    token = uuid.uuid4().hex
    run_id = str(uuid.UUID(token))
    project = "evdt-p1-" + token[:12]
    runtime = ROOT / ".runtime/p1-uplink" / token
    secret_dir = runtime / "secrets"
    secret_dir.mkdir(parents=True, mode=0o700)
    record = {
        "passed": False,
        "checkpoint": "P1-D1 isolated recording replay",
        "run_id": run_id,
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
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "containers/sumo-debian.sources",
        ROOT / "scripts/monitoring/verify_plant.py",
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
                str(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
                + ":/input/roads.osm.xml:ro",
                "--entrypoint",
                "python",
                app_image,
                "-m",
                "evdt.monitoring.provision_policy",
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
        compose("config", "--quiet")
        started = True
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
            timeout=120,
        )

        def get_api(path, authenticate=True):
            headers = {"Authorization": "Bearer " + readers[0]["token"]} if authenticate else {}
            with urlopen(
                Request(f"http://127.0.0.1:{args.port}" + path, headers=headers), timeout=2
            ) as response:
                return json.load(response)

        def wait_for(predicate, seconds=60):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    if predicate():
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(0.25)
            raise RuntimeError("Timed out waiting for the tested condition")

        wait_for(lambda: get_api("/health/ready", False)["ready"])
        wait_for(lambda: "monitoring_ingest_ready" in compose("logs", "--no-color", "ingest"))
        record["initial"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.monitoring.probe",
                "initial",
            )
        )
        if control:
            (control / "ready.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "base_url": f"http://127.0.0.1:{args.port}",
                        "reader_token": readers[0]["token"],
                        "scoped_token": readers[1]["token"],
                        "replay_base_url": f"http://127.0.0.1:{args.port + 1}",
                        "replay_token": replay_readers[0]["token"],
                        "replay_scoped_token": replay_readers[1]["token"],
                    }
                )
            )
            wait_for(lambda: (control / "start-source").is_file(), seconds=180)
        print("Collecting live SUMO motion into the persistent local queue", flush=True)
        compose("up", "-d", "source")
        wait_for(
            lambda: all(
                r["freshness"] == "paused" for r in get_api("/v1/current?asset_id=bus-001")["items"]
            )
        )
        time.sleep(5.2)
        record["healthy_pause"] = all(
            r["freshness"] == "paused" and r["source_state"] == "paused"
            for r in get_api("/v1/current?asset_id=bus-001")["items"]
        )
        if not record["healthy_pause"]:
            raise RuntimeError("A healthy deliberate pause was mislabeled stale")
        wait_for(lambda: len(get_api("/v1/observations?asset_id=bus-001")["items"]) >= 6)
        print(
            "Testing broker outage and sender/receiver restart while the source continues",
            flush=True,
        )
        compose("stop", "broker", "ingest")
        time.sleep(3)
        compose("restart", "sender")
        time.sleep(2)
        pending = compose(
            "exec",
            "-T",
            "sender",
            "python",
            "-c",
            "import json;from evdt.monitoring.transport import open_spool;print(json.dumps(open_spool().counts()))",
        )
        record["outage_queue"] = json.loads(pending)
        if record["outage_queue"]["pending"] < 2:
            raise RuntimeError("Outage did not exercise durable backlog")
        compose("start", "broker", "ingest")
        compose("wait", "source", timeout=60)
        wait_for(lambda: len(get_api("/v1/observations?asset_id=bus-001")["items"]) == 18)
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
        record["operational_health_live"] = get_api("/v1/system-health?asset_id=bus-001")
        if not (
            record["operational_health_live"]["sender"]["available"]
            and record["operational_health_live"]["sender"]["queue"]["pending"] == 0
            and record["operational_health_live"]["receiver"]["available"]
        ):
            raise RuntimeError("Live authenticated operational metrics unavailable")
        compose("stop", "sender")
        compose("restart", "registry")
        if control:
            (control / "source-finished").touch()
            wait_for(lambda: (control / "browser-done").is_file(), seconds=240)
            browser = json.loads((control / "browser-result.json").read_text())
            if browser.get("passed") is not True:
                raise RuntimeError("Dashboard browser checks failed; see owned browser results")
            record["dashboard_browser"] = browser
        record["dashboard_read_models"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.monitoring.dashboard_probe",
                timeout=60,
            )
        )
        record["analysis_views"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.monitoring.analysis_probe",
                timeout=90,
            )
        )
        print(
            "Checking receipts, exact bytes, duplicates, stale state and access boundaries",
            flush=True,
        )
        record["probe"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "probe",
                "python",
                "-m",
                "evdt.monitoring.probe",
                "final",
                timeout=180,
            )
        )
        if control:
            scenario_name = project + "-scenarios"
            compose(
                "run",
                "-d",
                "--no-deps",
                "--name",
                scenario_name,
                "-v",
                str(control) + ":/dashboard-control",
                "probe",
                "python",
                "-m",
                "evdt.monitoring.dashboard_scenarios",
                "--control",
                "/dashboard-control",
            )
            wait_for(lambda: (control / "scenarios-done").is_file(), seconds=360)
            if execute(["docker", "wait", scenario_name], timeout=30) != "0":
                raise RuntimeError("Controlled browser producer failed")
            record["controlled_scenarios"] = json.loads(
                (control / "scenario-producer-result.json").read_text()
            )
            record["scenario_browsers"] = json.loads(
                (control / "scenario-browser-result.json").read_text()
            )
            if not record["scenario_browsers"].get("passed"):
                raise RuntimeError("Controlled quality/clock browser checks failed")
        print(
            "Checking isolated replay API, original bytes and real permission denials", flush=True
        )
        record["replay"] = json.loads(
            compose(
                "run", "--rm", "--no-deps", "probe", "python", "-m", "evdt.monitoring.replay_probe"
            )
        )
        broker_container = compose("ps", "-q", "broker")
        broker_networks = json.loads(
            execute(
                [
                    "docker",
                    "inspect",
                    broker_container,
                    "--format",
                    "{{json .NetworkSettings.Networks}} ",
                ]
            )
        )
        broker_ip = next(iter(broker_networks.values()))["IPAddress"]
        network_check = """import socket,json,os
blocked=[]
for host in ('broker',os.environ['BROKER_TEST_IP']):
 try:
  socket.create_connection((host,1883),timeout=2).close()
 except OSError:
  blocked.append(host)
assert len(blocked)==2,'Replay can reach broker'
print(json.dumps({'passed':True,'broker_dns_and_tcp_isolated':True}))
"""
        record["replay_network"] = json.loads(
            compose(
                "exec",
                "-T",
                "-e",
                "BROKER_TEST_IP=" + broker_ip,
                "replay",
                "python",
                "-c",
                network_check,
            )
        )
        print("Verifying two complete accelerated 20-signal plant missions", flush=True)
        plant_output = compose(
            "run",
            "--rm",
            "--no-deps",
            "source",
            "python",
            "scripts/monitoring/verify_plant.py",
            "--output",
            "/source-output/plant-verification",
            timeout=240,
        )
        plant_lines = [
            line for line in plant_output.splitlines() if line.startswith("PLANT_RESULT=")
        ]
        if len(plant_lines) != 1:
            raise RuntimeError("Missing or ambiguous complete plant result")
        record["plant"] = json.loads(plant_lines[0].split("=", 1)[1])
        record["passed"] = record["probe"]["passed"] is True and record["plant"]["passed"] is True
    except Exception as exc:
        record["error"] = str(exc)
    finally:
        if started:
            try:
                execute(
                    [*compose_prefix, "cp", "source:/source-output/.", str(output / "source")],
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
                if any(record["cleanup"].values()):
                    record["passed"] = False
            except Exception as exc:
                record["passed"] = False
                record["cleanup_error"] = str(exc)
        record["source_sha256"] = before
        if fingerprints() != before:
            record["passed"] = False
            record["error"] = "Source changed during qualification"
        log.close()
        (output / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"Evidence: {output.relative_to(ROOT)} passed: {record['passed']}", flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
