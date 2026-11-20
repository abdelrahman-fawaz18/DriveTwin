"""Qualify sustained source prefixes and ten actual independent asset instances."""

import json
import secrets
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
ASSETS = [f"bus-{i:03}" for i in range(1, 11)]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait(predicate, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Source-preparation fixture deadline exceeded")


def marked(raw):
    lines = [
        line
        for line in raw.splitlines()
        if line.startswith(("SOURCE_SETUP_RESULT=", "SUSTAINED_RESULT="))
    ]
    require(len(lines) == 1, "Missing or ambiguous structured source result")
    return json.loads(lines[0].split("=", 1)[1])


def qualify(
    compose,
    execute,
    prefix,
    runtime,
    output,
    env,
    record,
    control,
    *,
    measurement=False,
    profile=None,
):
    require(control is not None, "Use the Windows source/browser controller")
    result = {
        "passed": False,
        "full_E2_passed": False,
        "W0_passed": False,
        "W1_passed": False,
        "scope": "E2-A source readiness; accelerated long prefixes and ten assets paced in batches of two",
    }
    record["sources"] = result
    secret_dir = Path(env["SECRET_DIRECTORY"])
    for i in range(3, 11):
        for name in (f"adapter{i}_mqtt", f"producer{i}"):
            (secret_dir / name).write_text(secrets.token_urlsafe(32) + "\n")
    readers = json.loads((secret_dir / "readers.json").read_bytes())
    readers["readers"][0]["assets"] = ASSETS
    (secret_dir / "readers.json").write_text(json.dumps(readers))
    writers = {
        "writers": [
            {
                "token": (secret_dir / f"producer{i}").read_text().strip(),
                "run_id": env["EVDT_RUN_ID"],
                "asset_id": asset,
                "source_id": "sumo-adapter",
            }
            for i, asset in enumerate(ASSETS, 1)
        ]
    }
    (secret_dir / "writers.json").write_text(json.dumps(writers))
    users = {"ingest": "ingest_mqtt", "observer": "observer_mqtt", "replay-observer": "replay_mqtt"}
    users.update(
        {
            "adapter-" + asset: "adapter_mqtt" if i == 1 else f"adapter{i}_mqtt"
            for i, asset in enumerate(ASSETS, 1)
        }
    )
    (secret_dir / "passwords").write_text(
        "".join(
            user + ":" + (secret_dir / name).read_text().strip() + "\n"
            for user, name in users.items()
        )
    )
    execute(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "mosquitto_passwd",
            "-v",
            str(secret_dir) + ":/config",
            env["MOSQUITTO_IMAGE"],
            "-U",
            "/config/passwords",
        ]
    )
    run = env["EVDT_RUN_ID"]
    acl = f"user ingest\ntopic read evdt/simulation/{run}/+/+/observations\ntopic write evdt/simulation/{run}/+/+/receipts\n"
    for asset in ASSETS:
        acl += f"user adapter-{asset}\ntopic write evdt/simulation/{run}/{asset}/sumo-adapter/observations\ntopic read evdt/simulation/{run}/{asset}/sumo-adapter/receipts\n"
    acl += f"user observer\ntopic read evdt/simulation/{run}/bus-001/sumo-adapter/observations\n"
    (secret_dir / "acl").write_text(acl)
    print(
        "Preparing the five-lap route, trusted ten-asset policy and original per-asset context",
        flush=True,
    )
    result["preparation"] = marked(
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
                str(ROOT / "simulation/montreal-sustained") + ":/sustained:ro",
                "-v",
                str(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
                + ":/input/roads.osm.xml:ro",
                "--entrypoint",
                "python",
                env["SIMULATOR_IMAGE"],
                "-m",
                "evdt.workload.source_setup",
                "--run-id",
                run,
                "--output",
                "/policy",
            ],
            timeout=120,
        )
    )
    definitions = {"services": {}, "secrets": {}, "volumes": {}}
    source_names = []
    sender_names = []
    for i, asset in enumerate(ASSETS, 1):
        source = "source" if i == 1 else f"source{i}"
        sender = "sender" if i == 1 else f"sender{i}"
        spool = "spool" if i == 1 else f"spool{i}"
        out = "source-output" if i == 1 else f"source-output{i}"
        source_names.append(source)
        sender_names.append(sender)
        password = "adapter_mqtt" if i == 1 else f"adapter{i}_mqtt"
        for name, file in [
            (password, password),
            (f"producer{i}", f"producer{i}"),
            (f"map_context{i}", f"map_context{i}.json"),
        ]:
            if i > 2:
                definitions["secrets"][name] = {"file": str(secret_dir / file)}
        definitions["volumes"][spool] = {"labels": {"com.evdt.scope": "e2-source"}}
        definitions["volumes"][out] = {"labels": {"com.evdt.scope": "e2-source"}}
        definitions["services"][source] = {
            "extends": {
                "file": str(ROOT / "ops/compose/compose.monitoring.yaml"),
                "service": "source",
            },
            "environment": {
                "ASSET_ID": asset,
                "CATALOG_VERSION": "1.4.0",
                "PRODUCER_TOKEN_FILE": f"/run/secrets/producer{i}",
            },
            "secrets": [f"producer{i}"],
            "command": ["python", "-m", "evdt.monitoring.adapter", "--maximum-samples", "30"],
            "volumes": [
                f"{spool}:/spool",
                f"{out}:/source-output",
                str(ROOT / "simulation/montreal-sustained") + ":/scenario:ro",
            ],
        }
        definitions["services"][sender] = {
            "extends": {
                "file": str(ROOT / "ops/compose/compose.monitoring.yaml"),
                "service": "sender",
            },
            "environment": {
                "ASSET_ID": asset,
                "CATALOG_VERSION": "1.4.0",
                "MQTT_USER": "adapter-" + asset,
                "MQTT_PASSWORD_FILE": "/run/secrets/" + password,
            },
            "secrets": [password, "metrics"],
            "volumes": [f"{spool}:/spool"],
        }
        if i <= 2:
            definitions["services"][source].pop("extends")
            definitions["services"][sender].pop("extends")
        else:
            # Compose rebases inherited binds relative to the file that defines the service.
            definitions["services"][source]["volumes"].append(
                str(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
                + ":/input/roads.osm.xml:ro"
            )
    definitions["services"]["api"] = {
        "environment": {"SENDER_SERVICES": json.dumps(dict(zip(ASSETS, sender_names)))}
    }
    definitions["services"]["probe"] = {
        "secrets": [f"map_context{i}" for i in range(2, 11)] + ["adapter10_mqtt"],
        "volumes": [
            f"{'spool' if i == 1 else 'spool' + str(i)}:/fleet/spool/{asset}"
            for i, asset in enumerate(ASSETS, 1)
        ]
        + [
            f"{'source-output' if i == 1 else 'source-output' + str(i)}:/fleet/source/{asset}"
            for i, asset in enumerate(ASSETS, 1)
        ],
    }
    override = runtime / "sources.compose.json"
    override.write_text(json.dumps(definitions, indent=2) + "\n")
    prefix.extend(["-f", str(override)])
    compose("config", "--quiet")
    if measurement:
        from measurement_fixture import qualify as qualify_measurement

        return qualify_measurement(
            compose,
            execute,
            prefix,
            runtime,
            output,
            env,
            record,
            control,
            definitions,
            override,
            readers,
            profile=profile,
        )
    print(
        "Checking 1,860 and 3,660 actual simulated seconds, each twice; no wall-time claim",
        flush=True,
    )
    result["long_prefixes"] = marked(
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-v",
            str(output) + ":/verification",
            "source",
            "python",
            "-m",
            "evdt.workload.sustained",
            "--output",
            "/verification/long-prefixes",
            timeout=600,
        )
    )
    compose("up", "-d", "db", "broker", "migrate", "registry", "ingest", "api", timeout=120)

    def ready():
        with urlopen(f"http://127.0.0.1:{env['API_PORT']}/health/ready", timeout=3) as response:
            return json.load(response)["ready"]

    wait(ready)
    compose(
        "run", "--rm", "--no-deps", "probe", "python", "-m", "evdt.workload.fleet_probe", "contexts"
    )
    containers = []
    for start in range(0, 10, 2):
        batch = source_names[start : start + 2]
        senders = sender_names[start : start + 2]
        print(f"Stepping independent paced sources {start + 1}–{start + 2} of ten", flush=True)
        compose("up", "-d", *senders, *batch, timeout=120)
        for name in batch:
            identifier = compose("ps", "-aq", name)
            containers.append(identifier)
            require(
                execute(["docker", "wait", identifier], timeout=90) == "0",
                "Independent asset source failed",
            )
        expected_assets = ASSETS[: start + 2]
        wait(
            lambda: all(
                json.loads(
                    compose(
                        "run",
                        "--rm",
                        "--no-deps",
                        "probe",
                        "python",
                        "-m",
                        "evdt.workload.fleet_probe",
                        "status",
                    )
                )[asset]
                == 30
                for asset in expected_assets
            )
        )
        compose("stop", *senders)
    require(len(set(containers)) == 10, "Source process identities were reused")
    result["independent_containers"] = 10
    result["fleet"] = json.loads(
        compose(
            "run",
            "--rm",
            "--no-deps",
            "probe",
            "python",
            "-m",
            "evdt.workload.fleet_probe",
            "verify",
        )
    )
    for name, asset in zip(source_names, ASSETS):
        compose("cp", name + ":/source-output/.", str(output / asset))
    (control / "ready.tmp").write_text(
        json.dumps(
            {
                "base_url": f"http://127.0.0.1:{env['API_PORT']}",
                "reader_token": readers["readers"][0]["token"],
                "scoped_token": readers["readers"][1]["token"],
                "run_id": run,
            }
        )
    )
    (control / "ready.tmp").replace(control / "ready.json")
    wait(lambda: (control / "browser-done").exists(), seconds=300)
    result["browser"] = json.loads((control / "browser-result.json").read_bytes())
    require(result["browser"]["passed"], "Fleet browser verification failed")
    result["passed"] = True
    return result
