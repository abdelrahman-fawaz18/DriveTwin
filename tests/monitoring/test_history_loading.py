import base64
import copy
import hashlib
import json
import uuid

import pytest

from evdt.monitoring.source import Frame
from evdt.workload import history_load, history_source


def identity():
    return {
        "run_id": str(uuid.uuid4()),
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "source_epoch": 1,
        "session_id": str(uuid.uuid4()),
    }


@pytest.mark.parametrize("registration_fails", [False, True])
def test_optional_registration_precedes_packets_and_only_terminal_clock_is_sent(
    monkeypatch, tmp_path, registration_fails
):
    calls = []

    class Registration:
        def register(self, raw):
            calls.append("registered")
            if registration_fails:
                raise RuntimeError("registry refused")

        def clock(self, state, simulated):
            assert (state, simulated) == ("finished", 1.0)
            assert (tmp_path / "output/originals.jsonl").exists()
            assert calls == ["registered", "before frame"]
            calls.append("finished")

    class Source:
        def __init__(self, *args, **kwargs):
            self.options = kwargs
            self.manifest_sha256 = "a" * 64
            self.summary = {}

        def frames(self):
            self.options["on_manifest"](b"actual-recipe")
            calls.append("before frame")
            self.options["on_clock"]("running", 1.0)
            catalog = history_source.monitoring_contract().readers["1.4.0"].catalog
            yield Frame(
                1.0,
                [
                    {
                        "signal_id": name,
                        "value": None,
                        "unit": spec["unit"],
                        "quality": "missing",
                        "reason": "dropout",
                        "provenance": "simulated-observation",
                    }
                    for name, spec in catalog["signals"].items()
                ],
            )
            self.options["on_clock"]("finished", 1.0)

    monkeypatch.setattr(history_source, "SumoVehicleSource", Source)
    result = history_source.generate(
        tmp_path / "output", tmp_path, tmp_path, identity(), 1, registration=Registration()
    )
    if registration_fails:
        assert result["passed"] is False and result["events"] == 0 and calls == ["registered"]
    else:
        assert result["passed"] and result["events"] == 1 and result["scalar_rows"] == 20, (
            result.get("error")
        )
        assert calls == ["registered", "before frame", "finished"]
        assert result["registration_mode"] == "registered-before-generation"


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "unregistered",
        "run",
        "hash",
        "truncated",
        "sequence",
        "recipe",
        "stream_hash",
        "count",
    ],
)
def test_loader_refuses_corrupt_unbound_and_incomplete_original_streams(
    monkeypatch, tmp_path, fault
):
    who = identity()
    monkeypatch.setenv("EVDT_RUN_ID", who["run_id"])
    body = {
        **who,
        "event_id": str(uuid.uuid4()),
        "stream_seq": 0,
        "sim_time_s": 1,
        "manifest_sha256": "a" * 64,
    }
    raw = json.dumps(body).encode()
    summary = {
        "passed": True,
        "paced": False,
        "registration_mode": "registered-before-generation",
        "identity": who,
        "manifest_sha256": "a" * 64,
        "events": 1,
        "expected_events": 1,
        "original_packets_sha256": hashlib.sha256(len(raw).to_bytes(8, "big") + raw).hexdigest(),
    }
    record = {
        "raw_base64": base64.b64encode(raw).decode(),
        "wire_sha256": hashlib.sha256(raw).hexdigest(),
        "event_id": body["event_id"],
    }
    if fault == "unregistered":
        summary["registration_mode"] = "offline-unregistered"
    elif fault == "run":
        monkeypatch.setenv("EVDT_RUN_ID", str(uuid.uuid4()))
    elif fault == "hash":
        record["wire_sha256"] = "b" * 64
    elif fault == "sequence":
        body["stream_seq"] = 2
    elif fault == "recipe":
        summary["manifest_sha256"] = "b" * 64
    elif fault == "stream_hash":
        summary["original_packets_sha256"] = "b" * 64
    elif fault == "count":
        summary["expected_events"] = 2
    if fault == "sequence":
        changed = json.dumps(body).encode()
        record.update(
            raw_base64=base64.b64encode(changed).decode(),
            wire_sha256=hashlib.sha256(changed).hexdigest(),
        )
    (tmp_path / "source-summary.json").write_text(json.dumps(summary))
    (tmp_path / "originals.jsonl").write_text(
        json.dumps(record) + ("" if fault == "truncated" else "\n")
    )
    if fault:
        with pytest.raises(RuntimeError):
            list(history_load.originals(tmp_path))
    else:
        assert list(history_load.originals(tmp_path)) == [(raw, body)]


@pytest.mark.parametrize("fault", ["raw", "payload", "identity", "sequence", "time", "hash"])
def test_independent_stored_original_comparison_rejects_projection_or_byte_corruption(fault):
    body = {"event_id": str(uuid.uuid4()), "stream_seq": 0, "sim_time_s": 1}
    raw = json.dumps(body).encode()
    row = {
        **body,
        "raw_payload": raw,
        "payload": copy.deepcopy(body),
        "wire_sha256": hashlib.sha256(raw).hexdigest(),
    }
    history_load.compare_stored(raw, body, row)
    if fault == "raw":
        row["raw_payload"] += b" "
    elif fault == "payload":
        row["payload"]["sim_time_s"] = 2
    elif fault == "identity":
        row["event_id"] = str(uuid.uuid4())
    elif fault == "sequence":
        row["stream_seq"] = 1
    elif fault == "time":
        row["sim_time_s"] = 2
    else:
        row["wire_sha256"] = "c" * 64
    with pytest.raises(RuntimeError):
        history_load.compare_stored(raw, body, row)
