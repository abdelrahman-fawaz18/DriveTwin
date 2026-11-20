"""Submit sealed scalar frames as new observations in an isolated experiment."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evdt.monitoring.registration_client import RegistrationClient
from evdt.monitoring.run_policy import digest, encoded, validate_manifest, validate_policy
from evdt.monitoring.transport import open_spool

ASSETS = ("bus-001", "bus-002")
SAMPLES = 16
MAX_TRACE_BYTES = 512 * 1024


def read_trace(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_TRACE_BYTES + 1)
    if len(raw) > MAX_TRACE_BYTES:
        raise ValueError("Recorded trace exceeds its local size bound")
    report = json.loads(raw)
    events = report["events"]
    if (
        not report["passed"]
        or report["paced"]
        or len(events) != SAMPLES
        or len({row["event_id"] for row in events}) != SAMPLES
        or any(
            digest(encoded(row["body"])) != row["wire_sha256"]
            or row["body"]["event_id"] != row["event_id"]
            or row["body"]["sim_time_s"] != number + 1
            or row["body"]["stream_seq"] != number
            or row["body"]["manifest_sha256"] != report["manifest_sha256"]
            for number, row in enumerate(events)
        )
    ):
        raise ValueError("Recorded trace does not match its original message fingerprints")
    return report, digest(raw)


def source_manifest(spec, original, sampling):
    expected = spec["expected"]
    version = {"1.2.0": "source-run/2.0.0", "1.3.0": "source-run/2.1.0"}[
        expected["catalog_version"]
    ]
    return {
        **expected,
        "manifest_version": version,
        "mode": "simulation",
        "identity": {
            name: original[name]
            for name in ("run_id", "asset_id", "source_id", "session_id", "source_epoch")
        },
        "asset_id": spec["asset_id"],
        "evidence_class": "assumed",
        "sampling": {key: sampling[key] for key in ("default_period_s", "period_overrides_s")},
        "minimum_sim_time_s": 0,
        "source_epoch_start_sim_s": 0,
        "initial_state_sim_s": expected["step_s"],
        "driver_options_sha256": digest(
            json.dumps({"pause_at_s": None, "pause_seconds": 0}, sort_keys=True).encode()
        ),
        "battery_power_source": "device.battery.energyConsumed (Wh per completed step) * 3600 / step_s; emission rate query is diagnostic only",
        "insertion_policy": "First SUMO insertion initializes energy; check device_step=0 and initial charge, then reconcile each completed physical interval",
    }


def prepare(directory, trace_dir):
    policy_path = directory / "run_policy.json"
    policy = validate_policy(policy_path.read_bytes())
    previous_runs = set()
    files = {}
    for spec, scenario in zip(
        policy["sources"], (Path("/scenario"), Path("/scenario2")), strict=True
    ):
        asset = spec["asset_id"]
        report, trace_hash = read_trace(trace_dir / f"{asset}-originals.json")
        originals = [row["body"] for row in report["events"]]
        plant = json.loads((scenario / "plant.json").read_bytes())
        manifest = source_manifest(spec, originals[0], plant["sampling"])
        original = encoded(manifest) + b"\n"
        if digest(original) != report["manifest_sha256"] or any(
            row[key] != value for row in originals for key, value in manifest["identity"].items()
        ):
            raise ValueError("Recorded source recipe differs from the current compatible inputs")
        previous_runs.add(manifest["identity"]["run_id"])
        files[directory / f"{asset}-reference.json"] = original
        spec["expected"]["input_sha256"].update(
            trace_recording=trace_hash, trace_adapter=digest(Path(__file__).read_bytes())
        )
        manifest["identity"]["run_id"] = policy["run_id"]
        manifest["input_sha256"] = spec["expected"]["input_sha256"]
        manifest["driver_options_sha256"] = digest(encoded({"adapter": "recorded-scalar/1.0.0"}))
        manifest["battery_power_source"] = "Recorded SUMO observations from the sealed input trace"
        manifest["insertion_policy"] = "The experiment adapter does not instantiate a vehicle"
        files[directory / f"{asset}-experiment.json"] = encoded(manifest)
    if len(previous_runs) != 1 or policy["run_id"] in previous_runs:
        raise ValueError(
            "The source traces must share one earlier run distinct from the experiment"
        )
    for path, raw in files.items():
        if path.name.endswith("-experiment.json"):
            validate_manifest(raw, policy)
        path.write_bytes(raw)
    policy_path.write_bytes(encoded(policy))


def deny_registration(registration, raw):
    request = Request(
        registration.base_url + "/v1/registrations",
        raw,
        {"Authorization": "Bearer " + registration.token, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=3):
            raise RuntimeError("Registry accepted metadata outside this credential's scope")
    except HTTPError as exc:
        with exc:
            if exc.code != 403:
                raise RuntimeError("Registry rejected the probe for an unexpected reason") from exc


def deny_publish(own_asset, original_run, run, raw):
    import threading

    from evdt.monitoring.contracts import observation_topic
    from evdt.monitoring.transport import client

    connection = client(f"experiment-scope-probe-{own_asset}-{run}")
    ready, delivered = threading.Event(), threading.Event()
    connection_codes, publish_codes = [], []

    def connected(_client, _userdata, _flags, reason, _properties):
        connection_codes.append(reason.value)
        ready.set()

    def published(_client, _userdata, _message, reason, _properties):
        publish_codes.append(reason.value)
        delivered.set()

    connection.on_connect, connection.on_publish = connected, published
    connection.connect("broker", 1883)
    connection.loop_start()
    try:
        if not ready.wait(3) or connection_codes != [0]:
            raise RuntimeError("Broker probe did not authenticate")
        other_asset = next(asset for asset in ASSETS if asset != own_asset)
        for target_run, target_asset in ((original_run, own_asset), (run, other_asset)):
            delivered.clear()
            topic = observation_topic(target_run, target_asset, "sumo-adapter")
            info = connection.publish(topic, raw, qos=1)
            if info.rc or not delivered.wait(3) or publish_codes[-1] != 135:
                raise RuntimeError("Broker did not refuse an out-of-scope write")
    finally:
        connection.disconnect()
        connection.loop_stop()


def play(directory):
    spool = open_spool()
    asset, run = spool.identity.asset_id, spool.identity.run_id
    report, trace_hash = read_trace(directory / "originals.json")
    manifest = json.loads((directory / "recipe.json").read_bytes())
    reference = (directory / "reference.json").read_bytes()
    previous = json.loads(reference)["identity"]
    bindings = manifest["input_sha256"]
    if (
        previous["asset_id"] != asset
        or previous["run_id"] == run
        or digest(reference) != report["manifest_sha256"]
        or bindings["trace_recording"] != trace_hash
        or bindings["trace_adapter"] != digest(Path(__file__).read_bytes())
        or any(manifest["identity"][key] != value for key, value in spool.identity.__dict__.items())
    ):
        raise ValueError("Experiment trace, adapter or source identity differs from its binding")
    session = spool.start_session(require_fresh=True)
    manifest["identity"] = {**spool.identity.__dict__, "session_id": session, "source_epoch": 1}
    registration = RegistrationClient(manifest["identity"], Path(os.environ["PRODUCER_TOKEN_FILE"]))
    registration.register(encoded(manifest))
    observations = []
    for original in report["events"]:
        body = original["body"]
        registration.clock("running", body["sim_time_s"])
        event = spool.enqueue(
            body["samples"],
            body["sim_time_s"],
            manifest_sha256=registration.manifest_hash,
            annotations={
                "origin": "recorded-scalar-experiment",
                "original_event_id": original["event_id"],
                "original_wire_sha256": original["wire_sha256"],
            },
        )
        observations.append(
            {
                "event_id": event.event_id,
                "wire_sha256": event.wire_sha256,
                "body": json.loads(event.raw),
            }
        )
        if len(observations) == 1:
            deny_registration(registration, reference)
            other = next(name for name in ASSETS if name != asset)
            deny_registration(
                registration,
                encoded(
                    {
                        **manifest,
                        "asset_id": other,
                        "identity": {**manifest["identity"], "asset_id": other},
                    }
                ),
            )
            deny_publish(asset, previous["run_id"], run, event.raw)
    registration.clock("finished", body["sim_time_s"])
    result = {
        "passed": True,
        "events": observations,
        "origin_run_id": previous["run_id"],
        "registry_denials": 2,
        "broker_denials": 2,
        "trace_sha256": trace_hash,
    }
    path = Path(os.environ["SOURCE_OUTPUT"]) / "source-report.json"
    with path.open("xb") as output:
        output.write(encoded(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare(Path("/policy"), Path("/reference-traces"))
    else:
        play(Path("/experiment"))


if __name__ == "__main__":
    main()
