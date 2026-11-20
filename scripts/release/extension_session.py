"""Exercise two distinct source configurations through the shared scalar services."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode
from uuid import uuid4

from demo_session import dashboard_assets
from live_speed_evidence import HISTORY, SAMPLES
from qualify_connected_speed_limit import ROOT, digest, read, request, require, wait_ready, write
from qualify_live_restart import wait_for

EXTENSION_CASE = "sensor-extension"
EXPERIMENT_CASE = "recorded-experiment"
ASSETS = ("bus-001", "bus-002")
SOURCES = ("source", "source2")
SENDERS = ("sender", "sender2")
COOLANT = "coolant.inlet.temperature"


def prepare_extension(compose, shell, runtime, override, env, tokens, run, image):
    directory = runtime / "secrets"
    writers = []
    users = {"ingest": "ingest_mqtt"}
    root = f"evdt/simulation/{run}"
    acl = [f"user ingest\ntopic read {root}/+/+/observations\ntopic write {root}/+/+/receipts\n"]
    for index, asset in enumerate(ASSETS, 1):
        name = f"producer{index}"
        token = secrets.token_urlsafe(36)
        (directory / name).write_text(token + "\n")
        writers.append(
            {"token": token, "run_id": run, "asset_id": asset, "source_id": "sumo-adapter"}
        )
        user = f"adapter-{asset}"
        users[user] = "adapter_mqtt" if index == 1 else "adapter2_mqtt"
        path = f"{root}/{asset}/sumo-adapter"
        acl.append(f"user {user}\ntopic write {path}/observations\ntopic read {path}/receipts\n")
    write(directory / "writers.json", {"writers": writers})
    (directory / "broker.acl").write_text("".join(acl))
    passwords = []
    for user, credential in users.items():
        password = secrets.token_urlsafe(36)
        (directory / credential).write_text(password + "\n")
        passwords.append(f"{user}:{password}\n")
    (directory / "broker.passwords").write_text("".join(passwords))
    shell(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "mosquitto_passwd",
        "-v",
        f"{directory}:/config",
        env["MOSQUITTO_IMAGE"],
        "-U",
        "/config/broker.passwords",
    )
    (directory / "broker.conf").write_text(
        "listener 1883\nallow_anonymous false\npassword_file /mosquitto/config/passwords\n"
        "acl_file /mosquitto/config/acl\npersistence true\npersistence_location /mosquitto/data/\n"
        "autosave_interval 1\nmessage_size_limit 65536\nmax_queued_messages 10000\n"
    )

    for role, filename in (("reader", "readers.json"), ("replay", "replay_readers.json")):
        entries = [{"token": tokens[role], "assets": list(ASSETS)}]
        for asset in ASSETS:
            scoped = tokens[f"{role}-{asset}"] = secrets.token_urlsafe(36)
            entries.append({"token": scoped, "assets": [asset]})
        write(directory / filename, {"readers": entries})

    config = read(override)
    config["services"] = {
        name: {"pull_policy": "never"}
        for name in ("api", "migrate", "ingest", "registry", "replay", *SENDERS)
    }
    for index, version in enumerate(("1.2.0", "1.3.0")):
        config["services"][SOURCES[index]] = {
            "environment": {"CATALOG_VERSION": version, "PYTHONPATH": "/workspace/src"},
            "pull_policy": "never",
            "volumes": [
                f"./{folder}:/workspace/{folder}:ro" for folder in ("src", "contracts", "catalog")
            ],
            "command": [
                "python",
                "-m",
                "evdt.monitoring.adapter",
                "--accelerated",
                "--maximum-samples",
                str(SAMPLES),
            ],
        }
        config["services"][SENDERS[index]]["environment"] = {"CATALOG_VERSION": version}
    write(override, config)
    env.update(MONITORING_IMAGE=image, API_PORT="18765", REPLAY_PORT="18766")
    (directory / "run_policy.json").unlink()
    compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--volume",
        f"{directory}:/policy",
        "--volume",
        "./simulation/montreal-bus-002:/scenario2:ro",
        "--entrypoint",
        "python",
        "source",
        "-m",
        "evdt.monitoring.extension_policy",
        "--run-id",
        run,
        "--output",
        "/policy/run_policy.json",
    )
    policy = read(directory / "run_policy.json")
    for entry in policy["sources"]:
        entry["expected"]["paced"] = False
    write(directory / "run_policy.json", policy)


def response(base, path, credential, expected=200, body=None):
    status, result = request(base, path, credential, json.dumps(body).encode() if body else None)
    require(status == expected, f"Unexpected status for {path}: {status}, expected {expected}")
    return result


def recording_path(path):
    path = path.resolve()
    require(
        path.parent == ROOT / "evidence/p6" and path.name.startswith("extension-"),
        "Use a qualified extension recording in evidence/p6",
    )
    return path


def configure_experiment(compose, runtime, override, recording, image):
    prior = read(recording / "qualification.json")
    require(
        prior["passed"]
        and prior["case"] == EXTENSION_CASE
        and prior["worker"]["cleanup_passed"]
        and prior["source"] == prior["worker"]["source"] == prior["worker"]["source_after"]
        and {key: row["originals"] for key, row in prior["worker"]["checks"]["assets"].items()}
        == dict.fromkeys(ASSETS, SAMPLES),
        "The original two-asset recording lacks a passing service result",
    )
    directory = runtime / "secrets"
    adapter = "./scripts/release/recorded_adapter.py:/fixture/recorded_adapter.py:ro"
    compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--volume",
        f"{directory}:/policy",
        "--volume",
        f"{recording}:/reference-traces:ro",
        "--volume",
        "./simulation/montreal-bus-002:/scenario2:ro",
        "--volume",
        adapter,
        "--entrypoint",
        "python",
        "source",
        "/fixture/recorded_adapter.py",
        "--prepare",
    )
    config = read(override)
    for index, (asset, source) in enumerate(zip(ASSETS, SOURCES, strict=True), 1):
        service = config["services"][source]
        credential = "adapter_mqtt" if index == 1 else "adapter2_mqtt"
        service["image"] = image
        service["mem_limit"] = "192m"
        service["command"] = ["python", "/fixture/recorded_adapter.py"]
        service["secrets"] = [f"producer{index}", credential]
        service["environment"].update(
            MQTT_USER=f"adapter-{asset}", MQTT_PASSWORD_FILE=f"/run/secrets/{credential}"
        )
        service["volumes"].extend(
            [
                adapter,
                f"{recording}/{asset}-originals.json:/experiment/originals.json:ro",
                f"{directory}/{asset}-reference.json:/experiment/reference.json:ro",
                f"{directory}/{asset}-experiment.json:/experiment/recipe.json:ro",
            ]
        )
    write(override, config)


def compare_experiment(api, replay, tokens, run, asset, report, recording):
    previous_path = recording / f"{asset}-originals.json"
    old = read(previous_path)["events"]
    previous_run = old[0]["body"]["run_id"]
    new = report["events"]
    require(
        report["trace_sha256"] == digest(previous_path)
        and report["origin_run_id"] == previous_run != run
        and report["registry_denials"] == report["broker_denials"] == 2
        and {row["event_id"] for row in old}.isdisjoint(row["event_id"] for row in new)
        and old[0]["body"]["session_id"] != new[0]["body"]["session_id"],
        "Experiment did not preserve a distinct, permission-scoped identity",
    )
    for original, experiment in zip(old, new, strict=True):
        source, target = original["body"], experiment["body"]
        require(
            (source["sim_time_s"], source["samples"]) == (target["sim_time_s"], target["samples"])
            and target["annotations"]["original_event_id"] == original["event_id"]
            and target["annotations"]["original_wire_sha256"] == original["wire_sha256"],
            "Experiment changed the recorded values or lost their lineage",
        )
    playback = tokens[f"replay-{asset}"]
    response(
        replay,
        "/v1/replay/sessions",
        playback,
        403,
        {"asset_id": asset, "source_run_id": previous_run, "from_s": 0, "to_s": SAMPLES},
    )
    response(replay, "/v1/commands", playback, 404, {"asset_id": asset})
    response(api, "/v1/commands", tokens[f"reader-{asset}"], 404, {"asset_id": asset})
    prior_project = "evdt-e2c-" + hashlib.sha256(recording.name.encode()).hexdigest()[:12]
    prior_runtime = ROOT / ".runtime/release-restarts" / prior_project / "secrets"
    old_reader = next(
        row["token"]
        for row in read(prior_runtime / "readers.json")["readers"]
        if row["assets"] == [asset]
    )
    response(api, f"/v1/snapshot?asset_id={asset}", old_reader, 401)
    return {"registry_scope_denials": 2, "broker_scope_denials": 2, "lineage_matches": True}


def check_asset(api, replay, tokens, run, asset, report):
    reader = tokens[f"reader-{asset}"]
    playback = tokens[f"replay-{asset}"]
    originals = [row["body"] for row in report["events"]]
    require(
        report["passed"]
        and len(originals) == len({row["event_id"] for row in originals}) == SAMPLES
        and all((row["run_id"], row["asset_id"]) == (run, asset) for row in originals),
        "Source identity or original count differs",
    )
    base = f"asset_id={asset}"
    snapshot = response(api, f"/v1/snapshot?{base}", reader)
    catalog = response(api, f"/v1/catalog?{base}", reader)
    history = response(api, HISTORY.format(asset=asset), reader)["items"]
    streams = {signal: [] for signal in catalog["signals"]}
    for original in originals:
        for sample in original["samples"]:
            streams[sample["signal_id"]].append((original["sim_time_s"], sample))
    current = {row["signal_id"]: row["observation"]["sample"] for row in snapshot["items"]}
    count = 20 if asset == ASSETS[0] else 21
    require(
        snapshot["run_id"] == run
        and len(streams) == len(current) == count
        and [row["payload"] for row in history] == originals
        and current == {key: rows[-1][1] for key, rows in streams.items()},
        "Shared history and current projections differ from the source originals",
    )
    for signal, originals_for_signal in streams.items():
        parameters = urlencode(
            {"asset_id": asset, "as_of": snapshot["cursor"], "signal_id": signal}
        )
        rows = response(api, f"/v1/history?{parameters}", reader)["items"]
        require(
            [(row["sim_time_s"], row["sample"]) for row in rows] == originals_for_signal,
            f"History for {signal} differs from its exact original samples",
        )
    if asset == ASSETS[0]:
        require(COOLANT not in streams, "Additional signal escaped into the original bus")
    else:
        coolant = streams[COOLANT]
        require(
            [stamp for stamp, _ in coolant] == list(range(2, SAMPLES + 1, 2))
            and catalog["signals"][COOLANT]["unit"] == "K"
            and [(stamp, row["quality"]) for stamp, row in coolant if row["quality"] != "good"]
            == [(4, "missing"), (8, "invalid")],
            "The extension did not preserve its cadence, units or quality",
        )
    other = next(value for value in ASSETS if value != asset)
    body = {"asset_id": asset, "source_run_id": run, "from_s": 0, "to_s": SAMPLES}
    endpoint = "/v1/replay/sessions"
    package = response(replay, endpoint, playback, body=body)
    recorded = [
        base64.b64decode(packet["raw_base64"], validate=True) for packet in package["packets"]
    ]
    require(
        package["source_run_id"] == run
        and package["replay_run_id"] != run
        and not package["capabilities"]["dispatch"]
        and [json.loads(raw) for raw in recorded] == originals
        and [hashlib.sha256(raw).hexdigest() for raw in recorded]
        == [row["wire_sha256"] for row in report["events"]],
        "Replay did not preserve the scoped original recording",
    )
    response(api, f"/v1/snapshot?asset_id={other}", reader, 403)
    response(api, f"/v1/snapshot?{base}", playback, 401)
    response(replay, endpoint, playback, 403, {**body, "asset_id": other})
    response(replay, endpoint, playback, 403, {**body, "source_run_id": str(uuid4())})
    response(replay, endpoint, reader, 401, body)
    return {"originals": len(history), "signals": count, "replay_run_id": package["replay_run_id"]}


def run_extension(
    compose, shell, runtime, override, env, tokens, run, output, image, recording=None
):
    prepare_extension(compose, shell, runtime, override, env, tokens, run, image)
    if recording:
        configure_experiment(compose, runtime, override, recording, image)
    started = time.monotonic()
    compose(
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "45",
        "--pull",
        "never",
        "api",
        "replay",
        "ingest",
        "registry",
        *SENDERS,
    )
    api, replay = "http://127.0.0.1:18765", "http://127.0.0.1:18766"
    wait_ready(api)
    wait_ready(replay)
    startup = time.monotonic() - started
    require(startup <= 120, "The extension startup exceeded 120 seconds")
    compose("up", "-d", "--no-deps", "--pull", "never", *SOURCES)
    ids = [compose("ps", "-aq", name).decode().strip() for name in SOURCES]
    require(
        len(set(ids)) == 2 and all(value and "\n" not in value for value in ids),
        "Missing owned sources",
    )
    require(
        shell("docker", "wait", *ids, timeout=55).split() == [b"0", b"0"],
        "A simulated source did not finish",
    )

    def drained():
        for sender in SENDERS:
            command = "import json; from evdt.monitoring.transport import open_spool; print(json.dumps(open_spool().counts()))"
            counts = json.loads(compose("exec", "-T", sender, "python", "-c", command))
            if counts != {"pending": 0, "accepted": SAMPLES}:
                return False
        return True

    wait_for(drained, "An extension source queue did not drain")
    compose("stop", "--timeout", "1", *SENDERS, "ingest", "broker", "registry")
    result = {}
    for asset, source in zip(ASSETS, SOURCES, strict=True):
        filename = output / f"{asset}-originals.json"
        compose("cp", f"{source}:/source-output/source-report.json", str(filename))
        report = read(filename)
        result[asset] = check_asset(api, replay, tokens, run, asset, report)
        if recording:
            result[asset].update(
                compare_experiment(api, replay, tokens, run, asset, report, recording)
            )
    require(
        len({row.pop("replay_run_id") for row in result.values()}) == 2,
        "Replay session identities collided",
    )
    dashboard_assets(api)
    dashboard_assets(replay)
    return {
        "startup_s": startup,
        "assets": result,
        "both_queues_empty": True,
        "asset_permissions_checked": True,
        "built_dashboard_served": True,
        "recorded_experiment": recording is not None,
    }
