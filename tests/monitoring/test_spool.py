import copy
import json
import subprocess
import sys
import uuid

import pytest

from evdt.contracts import Contract, Rejected
from evdt.monitoring.contracts import monitoring_contract, read_receipt, receipt
from evdt.monitoring.spool import Identity, Spool, SpoolFull


def sample(value=45.51):
    return [
        {
            "signal_id": "location.latitude",
            "value": value,
            "unit": "deg",
            "quality": "good",
            "provenance": "simulated-observation",
        }
    ]


def test_queue_cannot_change_format_even_when_empty(tmp_path):
    identity = Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    path = tmp_path / "format.db"
    Spool(
        path,
        identity,
        monitoring_contract().validate,
        read_receipt,
        schema_version="3.0.0",
        catalog_version="1.2.0",
    )
    with pytest.raises(ValueError, match="immutable"):
        Spool(
            path,
            identity,
            monitoring_contract().validate,
            read_receipt,
            schema_version="3.1.0",
            catalog_version="1.3.0",
        )


@pytest.fixture
def queue(tmp_path):
    return Spool(
        tmp_path / "queue.db",
        Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter"),
        monitoring_contract().validate,
        read_receipt,
    )


def test_committed_bytes_survive_restart_and_caller_mutation(queue):
    queue.start_session()
    values = sample()
    event = queue.enqueue(values, 1)
    values[0]["value"] = -90
    reopened = Spool(queue.path, queue.identity, monitoring_contract().validate, read_receipt)
    assert reopened.pending() == [event]
    assert json.loads(event.raw)["samples"][0]["value"] == 45.51


def test_only_matching_terminal_receipt_can_retire_event(queue):
    queue.start_session()
    event = queue.enqueue(sample(), 1)
    body = json.loads(event.raw)
    good = json.loads(receipt(body, event.raw, "accepted"))
    for key, value in [
        ("event_id", str(uuid.uuid4())),
        ("asset_id", "bus-002"),
        ("wire_sha256", "0" * 64),
        ("run_id", str(uuid.uuid4())),
    ]:
        wrong = copy.deepcopy(good)
        wrong[key] = value
        assert not queue.acknowledge(json.dumps(wrong).encode())
        assert queue.pending() == [event]
    assert queue.acknowledge(json.dumps(good).encode())
    assert not queue.acknowledge(json.dumps(good).encode())
    assert queue.counts() == {"pending": 0, "accepted": 1}


def test_terminal_rejection_is_recorded_without_retry_loop(queue):
    queue.start_session()
    event = queue.enqueue(sample(), 1)
    assert queue.acknowledge(
        receipt(json.loads(event.raw), event.raw, "rejected", "future_timestamp")
    )
    assert queue.counts() == {"pending": 0, "rejected": 1}


def test_session_identity_and_sequence_are_persistent(queue):
    first = queue.start_session()
    a = json.loads(queue.enqueue(sample(), 1).raw)
    b = json.loads(queue.enqueue(sample(), 2).raw)
    second = queue.start_session()
    c = json.loads(queue.enqueue(sample(), 3).raw)
    assert first != second and a["session_id"] == b["session_id"]
    assert (a["source_epoch"], a["stream_seq"]) == (1, 0)
    assert (b["source_epoch"], b["stream_seq"]) == (1, 1)
    assert (c["source_epoch"], c["stream_seq"]) == (2, 0)
    with pytest.raises(ValueError, match="another run"):
        Spool(
            queue.path,
            Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter"),
            monitoring_contract().validate,
            read_receipt,
        )


def test_refused_fresh_session_preserves_queue_ownership_and_pending_bytes(queue):
    session = queue.start_session(require_fresh=True)
    event = queue.enqueue(sample(), 1)

    with pytest.raises(RuntimeError, match="Plant restart requires a new run"):
        queue.start_session(require_fresh=True)

    assert queue.pending() == [event]
    with queue.connection() as conn:
        row = conn.execute("SELECT source_epoch,session_id,next_seq FROM metadata").fetchone()
        assert tuple(row) == (1, session, 1)


def test_capacity_backpressures_without_deleting_pending_event(queue):
    queue.start_session()
    one = queue.enqueue(sample(), 1)
    queue.max_pending_bytes = len(one.raw) + 1
    with pytest.raises(SpoolFull):
        queue.enqueue(sample(), 2)
    assert queue.pending() == [one]
    assert queue.acknowledge(receipt(json.loads(one.raw), one.raw, "accepted"))
    next_body = json.loads(queue.enqueue(sample(), 3).raw)
    assert next_body["stream_seq"] == 1


def test_wrong_mode_unit_and_retired_profile_cannot_enter_queue(queue):
    queue.start_session()
    with pytest.raises(Rejected, match="wrong_unit"):
        bad = sample()
        bad[0]["unit"] = "rad"
        queue.enqueue(bad, 1)
    event = queue.enqueue(sample(), 1)
    with pytest.raises(Rejected):
        Contract().validate(event.raw, queue.identity.topic, queue.identity.run_id)
    obj = json.loads(event.raw)
    obj["mode"] = "replay"
    with pytest.raises(Rejected):
        monitoring_contract().validate(
            json.dumps(obj).encode(), queue.identity.topic, queue.identity.run_id
        )


def test_process_exit_without_python_cleanup_preserves_committed_queue(tmp_path):
    file = tmp_path / "crash.db"
    run = str(uuid.uuid4())
    program = """import os,sys
from pathlib import Path
from evdt.monitoring.spool import Spool,Identity
from evdt.monitoring.contracts import monitoring_contract,read_receipt
s=Spool(Path(sys.argv[1]),Identity(sys.argv[2],'bus-001','sumo-adapter'),monitoring_contract().validate,read_receipt)
s.start_session()
s.enqueue([{'signal_id':'location.latitude','value':45.51,'unit':'deg','quality':'good','provenance':'simulated-observation'}],1)
os._exit(37)
"""
    result = subprocess.run([sys.executable, "-c", program, str(file), run], capture_output=True)
    assert result.returncode == 37
    reopened = Spool(
        file, Identity(run, "bus-001", "sumo-adapter"), monitoring_contract().validate, read_receipt
    )
    assert len(reopened.pending()) == 1


def test_receipt_detail_pruning_preserves_lifetime_accounting(queue):
    queue.terminal_history_limit = 2
    queue.start_session()
    for index in range(4):
        event = queue.enqueue(sample(), index)
        assert queue.acknowledge(receipt(json.loads(event.raw), event.raw, "accepted"))
    with queue.connection() as conn:
        assert conn.execute("SELECT count(*) FROM terminal").fetchone()[0] == 2
    assert queue.counts() == {"pending": 0, "accepted": 4}
