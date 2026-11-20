"""Qualify one live command through the regular durable monitoring pipeline."""

import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import URLError

from live_speed_evidence import HISTORY, SAMPLES, speeds
from qualify_command_authority import LIMITS, check_authority_journal, seal_competing_proposals
from qualify_connected_speed_limit import (
    ASSET,
    ISSUER,
    digest,
    read,
    request,
    require,
    running,
    timestamp,
    wait_ready,
    write,
)


def prepare_live(
    compose, shell, runtime, override, env, tokens, run, commanded, *, stale_fence=False
):
    directory = runtime / "secrets"
    for name in ("ingest_mqtt", "adapter_mqtt", "producer1"):
        (directory / name).write_text(secrets.token_urlsafe(36) + "\n")
    write(
        directory / "writers.json",
        {
            "writers": [
                {
                    "token": (directory / "producer1").read_text().strip(),
                    "run_id": run,
                    "asset_id": ASSET,
                    "source_id": "sumo-adapter",
                }
            ]
        },
    )
    if commanded:
        monitor = secrets.token_urlsafe(36)
        (directory / "command_monitor_reader").write_text(monitor + "\n")
        write(
            directory / "readers.json",
            {
                "readers": [
                    {"token": token, "assets": [ASSET]} for token in (tokens["reader"], monitor)
                ]
            },
        )

    passwords = directory / "broker.passwords"
    passwords.write_text(
        "".join(
            name + ":" + (directory / key).read_text().strip() + "\n"
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
        str(directory) + ":/config",
        env["MOSQUITTO_IMAGE"],
        "-U",
        "/config/broker.passwords",
    )
    (directory / "broker.conf").write_text(
        "listener 1883\nallow_anonymous false\npassword_file /mosquitto/config/passwords\n"
        "acl_file /mosquitto/config/acl\npersistence true\npersistence_location /mosquitto/data/\n"
        "autosave_interval 1\nmessage_size_limit 65536\nmax_queued_messages 10000\n"
    )
    topic = f"evdt/simulation/{run}/{ASSET}/sumo-adapter"
    (directory / "broker.acl").write_text(
        f"user ingest\ntopic read {topic}/observations\ntopic write {topic}/receipts\n"
        f"user adapter-{ASSET}\ntopic write {topic}/observations\ntopic read {topic}/receipts\n"
    )

    configuration = read(override)
    app = configuration["services"]["api"]
    for service in ("registry", "ingest", "sender", "source"):
        configuration["services"][service] = dict(app)
    configuration["services"]["source"]["command"] = [
        "python",
        "-m",
        "evdt.monitoring.adapter",
        "--maximum-samples",
        str(SAMPLES),
    ]
    if stale_fence:
        source = configuration["services"]["source"]
        source["command"] = [
            "python",
            "/fixture/probe_live_stale_fence.py",
            "--maximum-samples",
            str(SAMPLES),
        ]
        source["volumes"] = [
            *source["volumes"],
            "./scripts/commands/probe_live_stale_fence.py:/fixture/probe_live_stale_fence.py:ro",
        ]
    write(override, configuration)
    (directory / "run_policy.json").unlink()
    compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--volume",
        str(directory) + ":/policy",
        "--entrypoint",
        "python",
        "source",
        "-m",
        "evdt.monitoring.provision_policy",
        "--run-id",
        run,
        "--output",
        "/policy/run_policy.json",
    )


def load_reference(folder, sources):
    prior = read(folder / "qualification.json")
    path = folder / "observations.json"
    reference = read(path)
    require(
        prior["live_mode"] == "baseline"
        and prior["passed"]
        and prior["cleanup_passed"]
        and prior["source_files_match"]
        and prior["source_files"] == sources
        and prior["observations_sha256"] == digest(path)
        and prior["run_id"] == reference["run_id"],
        "Baseline evidence is incomplete or incompatible with current sources",
    )
    speeds(reference, ASSET)
    return reference, prior


def verify_live(compose, shell, tokens, run, output, reference, authority_case=None):
    commanded = reference is not None
    services = ["registry", "api", "ingest", "sender"]
    if commanded:
        services.append("command-api")
    print("Starting the registered source delivery and monitoring services", flush=True)
    compose("up", "-d", "--wait", "--wait-timeout", "45", "--pull", "never", *services)

    def address(service):
        return "http://" + compose("port", service, "8000").decode().strip()

    def call(base, route, status, role, value=None):
        data = None if value is None else json.dumps(value).encode()
        result, response = request(base, route, tokens[role], data)
        require(result == status, f"{route} expected {status}, received {result}")
        return response

    api = address("api")
    wait_ready(api)
    history_path = HISTORY.format(asset=ASSET)
    call(api, history_path, 401, "operator")
    call(api, history_path, 401, "replay")
    if commanded:
        before, baseline = reference
        require(before["run_id"] != run, "Use a fresh command run")
        write(output / "reference-observations.json", before)
        commands = address("command-api")
        wait_ready(commands)
        call(commands, "/v1/commands", 401, "reader", {})
        if authority_case:
            decision, proposed, reasons, proof = seal_competing_proposals(
                commands, tokens, run, authority_case
            )
            command_id = decision["command_id"]
            queued = call(commands, "/v1/commands/" + command_id, 200, "operator")
        else:
            command_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            command = {
                "schema_version": "evdt-command/1.0.0",
                "command_id": command_id,
                "run_id": run,
                "asset_id": ASSET,
                "issuer": ISSUER,
                "mode": "simulation",
                "capability": "set_speed_limit",
                "parameters": {"value": 4, "unit": "m/s"},
                "reason": "Live monitoring command qualification",
                "issued_at": timestamp(now),
                "expires_at": timestamp(now + timedelta(seconds=30)),
                "not_before_sim_s": 4,
                "expires_sim_s": 6,
            }
            queued = call(commands, "/v1/commands", 201, "operator", command)
        require(queued["state"] == "queued" and queued["effect"] is None, "Command was not queued")

    print("Driving the normal monitoring source and reading live snapshots", flush=True)
    compose("up", "-d", "--no-deps", "--pull", "never", "source")
    source_id = compose("ps", "-aq", "source").decode().strip()
    require(bool(source_id) and "\n" not in source_id, "Unexpected live source ownership")
    mounts = (
        shell(
            "docker",
            "inspect",
            "--format",
            "{{range .Mounts}}{{println .Destination}}{{end}}",
            source_id,
        )
        .decode()
        .splitlines()
    )
    source_secrets = {p for p in mounts if p.startswith("/run/secrets/")}
    expected_secrets = {"/run/secrets/producer1"}
    if commanded:
        expected_secrets.add("/run/secrets/command_executor")
    require(source_secrets == expected_secrets, "Unexpected credentials mounted on source")

    live = {}
    end = time.monotonic() + 50
    snapshot_path = "/v1/snapshot?asset_id=" + ASSET
    while time.monotonic() < end:
        try:
            snapshot = call(api, snapshot_path, 200, "reader")
            history = call(api, history_path, 200, "reader")
        except (URLError, TimeoutError):
            time.sleep(0.25)
            continue
        require(snapshot["run_id"] == run and history["run_id"] == run, "Wrong monitoring run")
        item = next(i for i in snapshot["items"] if i["signal_id"] == "motion.speed")
        sample = item["observation"]
        if (item["source_state"], item["freshness"], item["quality"]) == (
            "running",
            "fresh",
            "good",
        ) and sample:
            t = sample["sim_time_s"]
            stage = "before" if t <= 4 else "after" if t >= 6 else None
            if stage:
                live[stage] = {"sim_time_s": t, "value_m_s": sample["sample"]["value"]}
        if len(history["items"]) >= SAMPLES:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("Timed out waiting for accepted monitoring events")
    require(set(live) == {"before", "after"}, "Live dashboard speed transitions were missed")
    values = speeds(history, ASSET)
    for witness in live.values():
        require(
            witness["value_m_s"] == values[int(witness["sim_time_s"]) - 1],
            "Dashboard speed disagrees with the stored original",
        )

    shell("docker", "wait", source_id, timeout=15)
    require(
        shell("docker", "inspect", "--format", "{{.State.ExitCode}}", source_id).strip() == b"0",
        "Monitoring source did not exit successfully",
    )
    for file in ("source-report.json", "run-manifest.json"):
        compose("cp", "source:/source-output/" + file, str(output / file))
    originals = read(output / "source-report.json")
    manifest = read(output / "run-manifest.json")
    require(
        originals["passed"]
        and originals["manifest_sha256"] == digest(output / "run-manifest.json"),
        "Original source report or registered manifest was incomplete",
    )
    require(
        [event["body"] for event in originals["events"]]
        == [row["payload"] for row in history["items"]],
        "Stored monitoring payload differs from the original outbox payload",
    )
    require(
        all(len(event["body"]["samples"]) == 20 for event in originals["events"]),
        "Unexpected monitoring signal coverage",
    )
    write(output / "observations.json", history)
    report = {
        "live_mode": "arbitration" if authority_case else "commanded" if commanded else "baseline",
        "run_id": run,
        "observations_sha256": digest(output / "observations.json"),
        "accepted_originals": len(values),
        "accepted_readings": 20 * len(values),
        "live_snapshots": live,
        "maximum_post_command_speed_m_s": max(values[5:]),
        "effective_inputs": manifest["input_sha256"],
        "driver_options_sha256": manifest["driver_options_sha256"],
    }
    if not commanded:
        require(sum(v > 4.25 for v in values[5:]) >= 2, "Baseline cannot establish a speed effect")
        return report

    require(
        report["effective_inputs"] == baseline["effective_inputs"]
        and report["driver_options_sha256"] != baseline["driver_options_sha256"],
        "Commanded drive must share the reference inputs and declare its opt-in mode",
    )
    detail = "/v1/commands/" + command_id
    state = call(commands, detail, 200, "operator")
    denied = authority_case == "stale_fence"
    require(
        state["state"] == ("rejected_before_actuation" if denied else "acknowledged")
        and state["attempt_sim_s"] == 4
        and state["effect"] is None,
        "Unexpected live execution outcome before effect assessment",
    )
    claim = running(4)
    if authority_case:
        claim["fence_protocol"] = "evdt-fence/1"
        call(commands, "/internal/v1/verify-fence", 409, "executor", proof)
        require(
            call(commands, "/v1/authority", 200, "operator") == decision,
            "Live delivery changed the sealed decision",
        )
        for proposal in proposed.values():
            if proposal["command_id"] != command_id:
                call(commands, "/v1/commands/" + proposal["command_id"], 404, "operator")
        report.update(
            authority_case=authority_case,
            selected_proposer=decision["selected_proposer"],
            selected_cap_m_s=LIMITS[decision["selected_proposer"]],
            selection_reasons=reasons,
            generation=decision["generation"],
            decision_preserved=True,
            losing_proposals_never_queued=True,
        )
    call(commands, "/internal/v1/claim", 204, "executor", claim)
    if denied:
        stale = decision["generation"] - 1
        require(
            state["refusal_reason"] == "authority_fence_refused"
            and state["result"] is None
            and values == speeds(before, ASSET)
            and f"EV-DT stale live fence {stale} refused".encode()
            in shell("docker", "logs", source_id),
            "Stale authority did not produce a durable refusal and unchanged telemetry",
        )
        report["journal_counts"] = check_authority_journal(compose, denied=True)["counts"]
        return {
            **report,
            "execution_state": state["state"],
            "attempt_sim_s": state["attempt_sim_s"],
            "second_handoff_refused": True,
            "stale_generation_tested": stale,
            "refusal_reason": state["refusal_reason"],
            "unchanged_speed_trace": True,
            "independent_speed_effect": None,
        }
    print("Checking command effect from the regular monitoring API", flush=True)
    compose("run", "--rm", "--no-deps", "-T", "command-observer", "--live", command_id)
    assessed = call(commands, detail, 200, "operator")
    effect = assessed["effect"]
    require(
        {**assessed, "effect": None} == state
        and effect["confirmed"] is True
        and effect["status"] == "confirmed"
        and effect["maximum_commanded_m_s"] == max(values[5:])
        and effect["maximum_baseline_m_s"] == baseline["maximum_post_command_speed_m_s"],
        "Monitoring evidence failed to establish the requested speed reduction",
    )
    if authority_case:
        report["journal_counts"] = check_authority_journal(compose, denied=False)["counts"]
    return {
        **report,
        "execution_state": state["state"],
        "attempt_sim_s": state["attempt_sim_s"],
        "second_handoff_refused": True,
        "independent_speed_effect": effect,
    }
