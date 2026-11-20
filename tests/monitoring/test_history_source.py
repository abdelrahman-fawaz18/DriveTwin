import base64
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from evdt.config import ROOT
from evdt.contract_profile import Rejected
from evdt.monitoring.contracts import monitoring_contract, observation_topic
from evdt.monitoring.source import Frame
from evdt.workload.history_source import encode_frame
from scripts.monitoring.history_source_audit import audit


def test_history_fixture_packet_preserves_frame_identity_time_units_and_quality():
    identity = {
        "run_id": str(uuid.uuid4()),
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "source_epoch": 1,
        "session_id": str(uuid.uuid4()),
    }
    samples = [
        {
            "signal_id": "location.latitude",
            "value": 45.51,
            "unit": "deg",
            "quality": "good",
            "provenance": "simulated-observation",
        }
    ]
    frame = Frame(7200.0, samples)
    now = "2026-11-04T00:00:00Z"
    raw = encode_frame(identity, 0, frame, "a" * 64, "1.4.0", now)
    body, _ = monitoring_contract().validate(
        raw,
        observation_topic(identity["run_id"], identity["asset_id"], identity["source_id"]),
        identity["run_id"],
    )
    assert body["sim_time_s"] == 7200 and body["observed_at"] == now and body["samples"] == samples
    assert body["manifest_sha256"] == "a" * 64 and body["schema_version"] == "3.2.0"
    assert "not live transport" in body["annotations"]["history_fixture"]
    assert encode_frame(identity, 0, frame, "a" * 64, "1.4.0", now) == raw
    assert (
        json.loads(encode_frame(identity, 1, frame, "a" * 64, "1.4.0", now))["event_id"]
        != body["event_id"]
    )
    changed = json.loads(raw)
    changed["samples"][0]["unit"] = "m"
    with pytest.raises(Rejected):
        monitoring_contract().validate(
            json.dumps(changed).encode(),
            observation_topic(identity["run_id"], identity["asset_id"], identity["source_id"]),
            identity["run_id"],
        )


def test_history_volume_protocol_is_distinct_from_live_duration_and_has_real_closed_route():
    protocol = json.loads((ROOT / "benchmarks/history.json").read_bytes())
    live = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())
    assert protocol["paced"] is False
    assert len(set(protocol["assets"])) == 10
    assert (
        protocol["samples_per_asset"] * len(protocol["assets"])
        == protocol["expected_events"]
        == 72000
    )
    assert (
        protocol["expected_events"] * protocol["signals_per_event"]
        == protocol["expected_scalar_rows"]
        == 1440000
    )
    assert len(set(protocol["query"]["signals"])) == 5 and protocol["query"]["window_s"] == 3600
    assert protocol["query"]["point_limit_total"] == 5000
    assert protocol["query"]["cold_cases"] >= 20 and protocol["query"]["warm_cases"] >= 20
    assert (
        live["phase_1_acceptance"]["measured_duration_s"] == {"W0": 120, "W1": 300}
        and live["phase_1_acceptance"]["warmup_s"] == 60
    )
    original = json.loads((ROOT / "simulation/montreal/mission.json").read_bytes())
    route = json.loads((ROOT / Path(protocol["scenario"]) / "mission.json").read_bytes())
    loop = original["route_edges"]
    assert loop[0] == loop[-1]
    assert route["route_edges"] == loop + (loop[1:] * 9)
    assert route["deadline_sim_s"] > 7200


@pytest.mark.parametrize(
    "mutation", ["bytes", "value_with_recomputed_hashes", "step_gap", "truncated"]
)
def test_independent_history_audit_rejects_corruption_even_with_recomputed_outer_hashes(
    tmp_path, mutation
):
    directory = tmp_path / "source"
    plant = directory / "plant"
    plant.mkdir(parents=True)
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "mission.json").write_text(json.dumps({"step_s": 0.2}))
    identity = {
        "run_id": str(uuid.uuid4()),
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "source_epoch": 1,
        "session_id": str(uuid.uuid4()),
    }
    manifest = json.dumps({"identity": identity}).encode()
    (plant / "run-manifest.json").write_bytes(manifest)
    fingerprint = hashlib.sha256(manifest).hexdigest()
    samples = [
        {
            "signal_id": "motion.speed",
            "unit": "m/s",
            "value": 0,
            "quality": "good",
            "provenance": "simulated-observation",
        },
        {
            "signal_id": "motion.distance",
            "unit": "m",
            "value": 1,
            "quality": "good",
            "provenance": "simulated-observation",
        },
    ]
    raw = encode_frame(
        identity, 0, Frame(1.0, samples), fingerprint, "1.4.0", "2026-11-04T00:00:00Z"
    )
    summary = {
        "passed": True,
        "paced": False,
        "manifest_sha256": fingerprint,
        "identity": identity,
        "last_sim_time_s": 1.0,
        "expected_events": 1,
        "events": 1,
        "scalar_rows": 2,
        "signals_per_event": 2,
        "maximum_wire_bytes": len(raw),
        "original_packets_sha256": hashlib.sha256(len(raw).to_bytes(8, "big") + raw).hexdigest(),
    }
    record = {
        "event_id": json.loads(raw)["event_id"],
        "wire_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_base64": base64.b64encode(raw).decode(),
    }
    trace = [
        {"sim_time_s": t, "values": {"motion.speed": 0, "motion.distance": 1}}
        for t in [0.4, 0.6, 0.8, 1.0]
    ]

    def save():
        (directory / "source-summary.json").write_text(json.dumps(summary))
        (directory / "originals.jsonl").write_text(json.dumps(record) + "\n")
        (plant / "plant-steps.json").write_text(json.dumps(trace))

    save()
    assert audit(directory, scenario)["all_packet_values_match_physical_steps"]
    if mutation in ("bytes", "value_with_recomputed_hashes"):
        body = json.loads(raw)
        body["samples"][0]["value"] = 2
        changed = json.dumps(body).encode()
        record["raw_base64"] = base64.b64encode(changed).decode()
        if mutation == "value_with_recomputed_hashes":
            record["wire_sha256"] = hashlib.sha256(changed).hexdigest()
            summary["original_packets_sha256"] = hashlib.sha256(
                len(changed).to_bytes(8, "big") + changed
            ).hexdigest()
            summary["maximum_wire_bytes"] = len(changed)
    elif mutation == "step_gap":
        trace[0]["sim_time_s"] = 0.5
    save()
    if mutation == "truncated":
        path = directory / "originals.jsonl"
        path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError):
        audit(directory, scenario)
