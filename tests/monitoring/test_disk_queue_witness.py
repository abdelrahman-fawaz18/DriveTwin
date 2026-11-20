import hashlib
import json
import uuid

import pytest

from evdt.monitoring.contracts import monitoring_contract, read_receipt
from evdt.monitoring.spool import Identity, Pending, Spool
from evdt.workload.capacity import STAMP, annotations, samples
from evdt.workload.disk_queue import original_record


@pytest.fixture
def packet(tmp_path):
    identity = Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    spool = Spool(
        tmp_path / "fixture.db",
        identity,
        monitoring_contract().validate,
        read_receipt,
        schema_version="3.2.0",
        catalog_version="1.4.0",
    )
    spool.start_session()
    values = samples()
    next(item for item in values if item["signal_id"] == "motion.speed")["value"] = 0
    event = spool.enqueue(
        values,
        0,
        observed_at=STAMP,
        annotations=annotations(spool, 0, values, 8192),
        manifest_sha256="0" * 64,
    )
    return identity, event, values


def test_worst_packet_original_witness_keeps_identity_hash_and_sequence(packet):
    identity, event, values = packet
    result = original_record(event, 0, values, identity, "3.2.0", "1.4.0")
    assert result["event_id"] == event.event_id
    assert result["wire_sha256"] == hashlib.sha256(event.raw).hexdigest()
    assert result["stream_seq"] == 0 and result["source_epoch"] == 1


@pytest.mark.parametrize("fault", ["value", "boolean_zero", "run", "sequence", "unit"])
def test_rehashed_witness_cannot_hide_wrong_storage_fixture_fields(packet, fault):
    identity, event, values = packet
    body = json.loads(event.raw)
    if fault == "value":
        body["samples"][0]["value"] += 1
    elif fault == "boolean_zero":
        sample = next(s for s in body["samples"] if s["value"] == 0)
        sample["value"] = False
    elif fault == "run":
        body["run_id"] = str(uuid.uuid4())
    elif fault == "sequence":
        body["stream_seq"] = 1
    else:
        body["samples"][0]["unit"] = "rad"
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    while len(raw) > 8192:
        key = next(key for key, value in body["annotations"].items() if value)
        body["annotations"][key] = body["annotations"][key][:-1]
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    # Keep valid UTF-8 JSON at the exact size even when a changed field shrinks it.
    if len(raw) < 8192:
        raw += b" " * (8192 - len(raw))
    assert len(raw) == 8192
    changed = Pending(event.event_id, raw, hashlib.sha256(raw).hexdigest())
    with pytest.raises(RuntimeError):
        original_record(changed, 0, values, identity, "3.2.0", "1.4.0")
