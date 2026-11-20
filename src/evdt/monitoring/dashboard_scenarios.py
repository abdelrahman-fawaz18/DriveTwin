"""Owned test producer for real browser quality/clock scenarios; never a product API."""

import argparse
import copy
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract, observation_topic, receipt_topic
from evdt.monitoring.map_context import provision
from evdt.monitoring.probe_support import actor, require, wait_receipt
from evdt.monitoring.registry_probe import Clock, metadata
from evdt.monitoring.repository import connection
from evdt.monitoring.run_policy import encoded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    args = parser.parse_args()
    control = args.control
    original = json.loads(Path("/source-output/run-manifest.json").read_text())
    report = json.loads(Path("/source-output/source-report.json").read_text())
    base = report["events"][-1]["body"]
    clock = None
    manifest = None
    fingerprint = None
    sim = 0
    sequence = 0
    heartbeat = False
    state = "running"
    last_beat = 0
    last_nonce = None
    accepted = 0
    stages = []
    with actor("adapter-bus-002", "/run/secrets/adapter2_mqtt") as (writer, messages, subscribed):
        writer.subscribe(receipt_topic(os.environ["EVDT_RUN_ID"], "bus-002", "sumo-adapter"), qos=1)
        require(subscribed.wait(5), "Scenario receipt subscription unavailable")
        (control / "scenarios-ready").touch()
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline and not (control / "scenarios-done").exists():
            try:
                command = json.loads((control / "scenario-command.json").read_text())
            except (OSError, ValueError):
                command = {}
            if command.get("nonce") and command["nonce"] != last_nonce:
                last_nonce = command["nonce"]
                stage = command["stage"]
                reply = {"nonce": last_nonce, "stage": stage, "passed": False}
                try:
                    if stage == "setup":
                        with connection() as conn:
                            head = conn.execute(
                                "SELECT source_epoch,sim_time_s FROM m_source_clocks WHERE run_id=%s AND asset_id='bus-002' AND source_id='sumo-adapter'",
                                (os.environ["EVDT_RUN_ID"],),
                            ).fetchone()
                            context = conn.execute(
                                "SELECT context FROM m_map_contexts WHERE run_id=%s AND asset_id='bus-001'",
                                (os.environ["EVDT_RUN_ID"],),
                            ).fetchone()["context"]
                            context["asset_id"] = "bus-002"
                            path = Path("/tmp/scenario-map.json")
                            path.write_bytes(encoded(context))
                            provision(conn, path)
                        sim = head["sim_time_s"] + 1 if head else 0
                        epoch = head["source_epoch"] + 1 if head else 1
                        manifest = copy.deepcopy(original)
                        manifest["identity"].update(
                            asset_id="bus-002", source_epoch=epoch, session_id=str(uuid.uuid4())
                        )
                        manifest.update(
                            asset_id="bus-002", minimum_sim_time_s=sim, source_epoch_start_sim_s=sim
                        )
                        status, body = metadata("/v1/registrations", manifest, "producer2")
                        require(status == 200, "Controlled scenario registration failed")
                        fingerprint = body["manifest_sha256"]
                        clock = Clock(manifest, fingerprint, "producer2")
                        clock.declare("running", sim)
                        sequence = 0
                        heartbeat = True
                        state = "running"
                    elif stage in ("good-zero", "missing", "invalid", "recover"):
                        require(clock is not None, "Scenario needs setup")
                        if state == "paused":
                            clock.declare("running", sim)
                        sim += 1
                        clock.declare("running", sim)
                        state = "running"
                        heartbeat = True
                        body = copy.deepcopy(base)
                        body.update(
                            manifest["identity"],
                            event_id=str(uuid.uuid4()),
                            manifest_sha256=fingerprint,
                            sim_time_s=sim,
                            stream_seq=sequence,
                            observed_at=datetime.now(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        )
                        sequence += 1
                        for sample in body["samples"]:
                            if sample["signal_id"] in ("motion.speed", "location.latitude"):
                                sample.pop("raw_value", None)
                                sample.pop("reason", None)
                                sample["quality"] = "good"
                                if sample["signal_id"] == "motion.speed":
                                    sample["value"] = 7 if stage == "recover" else 0
                                if stage == "missing" and sample["signal_id"] == "motion.speed":
                                    sample.update(value=None, quality="missing", reason="dropout")
                                if stage == "invalid":
                                    sample.update(
                                        value=None,
                                        quality="invalid",
                                        reason="out_of_range",
                                        raw_value=1000,
                                    )
                        monitoring_contract().validate(
                            encoded(body),
                            observation_topic(body["run_id"], body["asset_id"], body["source_id"]),
                            body["run_id"],
                        )
                        wait_receipt(writer, messages, body, expected_status="accepted")
                        accepted += 1
                    elif stage == "pause":
                        clock.declare("paused", sim)
                        state = "paused"
                        heartbeat = True
                    elif stage == "heartbeat-loss":
                        heartbeat = False
                    elif stage == "resume":
                        clock.declare("running", sim)
                        state = "running"
                        heartbeat = True
                    elif stage == "finish":
                        clock.declare("finished", sim)
                        state = "finished"
                        heartbeat = False
                    else:
                        raise ValueError("Unknown owned scenario")
                    reply["passed"] = True
                    stages.append(stage)
                except Exception as exc:
                    reply["error"] = type(exc).__name__ + ": " + str(exc)
                (control / "scenario-reply.json").write_text(json.dumps(reply))
            if clock and heartbeat and time.monotonic() - last_beat > 1:
                clock.declare(state, sim)
                last_beat = time.monotonic()
            time.sleep(0.1)
    (control / "scenario-producer-result.json").write_text(
        json.dumps(
            {
                "passed": True,
                "accepted_controlled_events": accepted,
                "stages": stages,
                "scope": "Controlled test producer, not a second physical vehicle model",
            }
        )
    )


if __name__ == "__main__":
    main()
