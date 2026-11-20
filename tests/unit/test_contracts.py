import copy
import json
import uuid

import pytest

from evdt.contracts import Contract, Rejected
from evdt.probe import event


@pytest.fixture
def sample(monkeypatch):
    monkeypatch.setenv("EVDT_RUN_ID", str(uuid.uuid4()))
    return event(1, 1)


def validate(obj, contract=None):
    return (contract or Contract()).validate(
        json.dumps(obj).encode(),
        f"evdt/qualification/{obj['run_id']}/bus-001/observations",
        obj["run_id"],
    )


def test_valid_and_canonical_hash(sample):
    assert validate(sample) == validate(dict(reversed(list(sample.items()))))


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"schema_version": "2.0.0"}, "envelope_schema"),
        ({"stream_seq": True}, "envelope_schema"),
        ({"stream_seq": -1}, "envelope_schema"),
        ({"observed_at": "2026-01-01T00:00:00"}, "envelope_schema"),
        ({"event_id": "not-a-uuid"}, "envelope_schema"),
        ({"mode": "live"}, "envelope_schema"),
        ({"extra": "unrecognized"}, "envelope_schema"),
        ({"source_id": "unregistered"}, "unregistered_source"),
    ],
)
def test_reject_invalid_envelope(sample, change, reason):
    sample.update(change)
    with pytest.raises(Rejected, match=reason):
        validate(sample)


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("value", True, "envelope_schema"),
        ("value", "0.7", "envelope_schema"),
        ("value", 1.01, "out_of_range"),
        ("unit", "%", "wrong_unit"),
        ("signal_id", "unregistered", "unregistered_signal"),
    ],
)
def test_reject_invalid_sample(sample, key, value, reason):
    sample["samples"][0][key] = value
    with pytest.raises(Rejected, match=reason):
        validate(sample)


def test_signal_extension_only_changes_catalog(sample):
    catalog = copy.deepcopy(Contract().catalog)
    catalog["signals"]["coolant.inlet.temperature"] = {
        "unit": "K",
        "minimum": 200,
        "maximum": 500,
    }
    sample["samples"][0].update(signal_id="coolant.inlet.temperature", value=300, unit="K")
    validate(sample, Contract(catalog))
    with pytest.raises(Rejected, match="unregistered_signal"):
        validate(sample)


@pytest.mark.parametrize("raw", [b'{"x": 1, "x": 2}', b'{"x": NaN}', b"\xff"])
def test_invalid_json(raw):
    with pytest.raises(Rejected, match="invalid_json"):
        Contract().validate(raw, "unused", "unused")


def test_duplicate_signal(sample):
    sample["samples"].append(copy.deepcopy(sample["samples"][0]))
    with pytest.raises(Rejected, match="duplicate_signal"):
        validate(sample)


def test_payload_limit():
    with pytest.raises(Rejected, match="payload_too_large"):
        Contract().validate(b" " * 65_537, "unused", "unused")
