import json
import os
import sqlite3
import subprocess
import sys
import uuid

import pytest

from evdt.contracts import Rejected
from evdt.monitoring.contracts import monitoring_contract, read_receipt, receipt
from evdt.monitoring.spool import Identity, Spool, SpoolFull
from evdt.monitoring.spool_capacity import DropOldestPolicy

STAMP = "2026-11-04T00:00:00Z"


def samples():
    return [
        {
            "signal_id": "location.latitude",
            "value": 45.51,
            "unit": "deg",
            "quality": "good",
            "provenance": "simulated-observation",
        }
    ]


@pytest.fixture
def queue(tmp_path):
    identity = Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    validator = monitoring_contract().validate
    prototype = Spool(tmp_path / "size.db", identity, validator, read_receipt)
    prototype.start_session()
    limit = 2 * len(prototype.enqueue(samples(), 1, observed_at=STAMP).raw)
    return Spool(
        tmp_path / "queue.db",
        identity,
        validator,
        read_receipt,
        max_pending_bytes=limit,
        capacity_policy=DropOldestPolicy(limit, 8),
    )


def enqueue(queue, t):
    return queue.enqueue(samples(), t, observed_at=STAMP)


def dump(queue):
    with queue.connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return list(conn.iterdump())


def reopen(queue, *, policy=True):
    return Spool(
        queue.path,
        queue.identity,
        monitoring_contract().validate,
        read_receipt,
        max_pending_bytes=queue.max_pending_bytes,
        capacity_policy=queue.capacity_policy if policy else None,
    )


def test_oldest_evictions_keep_exact_identity_time_hash_and_surviving_bytes_across_sessions(queue):
    queue.start_session()
    first, second = enqueue(queue, 1), enqueue(queue, 2)
    queue.start_session()
    third = enqueue(queue, 3)
    assert queue.pending() == [second, third]
    audit = queue.eviction_audit()
    assert len(audit) == 1
    original = json.loads(first.raw)
    assert audit[0] | {} == {
        "audit_sequence": 1,
        "event_id": first.event_id,
        "wire_sha256": first.wire_sha256,
        "source_epoch": original["source_epoch"],
        "stream_seq": original["stream_seq"],
        "sim_time_s": 1.0,
        "observed_at": STAMP,
        "evicted_at": audit[0]["evicted_at"],
        "disposition": "unconfirmed",
        "drop_audit": None,
        "receipt": None,
        "resolved_at": None,
    }
    assert queue.counts() == {
        "generated": 3,
        "queued": 2,
        "pending": 3,
        "accepted": 0,
        "rejected": 0,
        "dropped": 0,
        "evicted_unconfirmed": 1,
        "evicted_lifetime": 1,
    }
    other = reopen(queue)
    assert other.pending() == queue.pending() and other.eviction_audit() == audit
    assert other.counts() == queue.counts()


def test_invalid_and_individually_oversized_packets_never_evict_existing_data(queue):
    queue.start_session()
    enqueue(queue, 1)
    enqueue(queue, 2)
    before = dump(queue)
    wrong = samples()
    wrong[0]["unit"] = "rad"
    with pytest.raises(Rejected):
        queue.enqueue(wrong, 3, observed_at=STAMP)
    assert dump(queue) == before
    with pytest.raises(SpoolFull, match="One event"):
        queue.enqueue(
            samples(),
            3,
            observed_at=STAMP,
            annotations={"fill" + str(i): "x" * 256 for i in range(8)},
        )
    assert dump(queue) == before


def test_loss_ledger_exhaustion_preserves_all_committed_data_and_next_sequence(queue):
    queue = Spool(
        queue.path.with_name("small.db"),
        queue.identity,
        queue.validate,
        read_receipt,
        max_pending_bytes=queue.max_pending_bytes,
        capacity_policy=DropOldestPolicy(queue.max_pending_bytes, 1),
    )
    queue.start_session()
    enqueue(queue, 1)
    enqueue(queue, 2)
    enqueue(queue, 3)
    before = dump(queue)
    with pytest.raises(SpoolFull, match="audit capacity exhausted"):
        enqueue(queue, 4)
    assert dump(queue) == before
    assert queue.counts()["generated"] == 3


def test_failed_new_insert_rolls_back_eviction_audit_counts_and_sequence(queue):
    queue.start_session()
    enqueue(queue, 1)
    enqueue(queue, 2)
    with queue.connection() as conn:
        conn.execute(
            "CREATE TRIGGER refuse_next BEFORE INSERT ON pending BEGIN SELECT RAISE(ABORT,'controlled insert failure'); END"
        )
    before = dump(queue)
    with pytest.raises(sqlite3.IntegrityError, match="controlled"):
        enqueue(queue, 3)
    assert dump(queue) == before
    with queue.connection() as conn:
        conn.execute("DROP TRIGGER refuse_next")
    assert json.loads(enqueue(queue, 3).raw)["stream_seq"] == 2
    assert queue.counts()["generated"] == 3


def test_actual_process_crash_after_eviction_before_insert_recovers_original_queue(queue):
    queue.start_session()
    enqueue(queue, 1)
    enqueue(queue, 2)
    before = dump(queue)
    program = """import json,os,sys
from pathlib import Path
from evdt.monitoring.contracts import monitoring_contract,read_receipt
from evdt.monitoring.spool import Spool,Identity
from evdt.monitoring.spool_capacity import DropOldestPolicy
identity=Identity(**json.loads(sys.argv[2]))
limit=int(sys.argv[3])
queue=Spool(Path(sys.argv[1]),identity,monitoring_contract().validate,read_receipt,max_pending_bytes=limit,capacity_policy=DropOldestPolicy(limit,8))
original=DropOldestPolicy.admit
def crash(self,conn,raw,limit):
    original(self,conn,raw,limit)
    os._exit(23)
DropOldestPolicy.admit=crash
queue.enqueue(json.loads(sys.argv[4]),3,observed_at=sys.argv[5])
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(queue.path),
            json.dumps(queue.identity.__dict__),
            str(queue.max_pending_bytes),
            json.dumps(samples()),
            STAMP,
        ],
        env=os.environ.copy(),
        capture_output=True,
    )
    assert result.returncode == 23, result.stderr.decode()
    assert dump(reopen(queue)) == before


@pytest.mark.parametrize("status,reason", [("accepted", None), ("rejected", "future_timestamp")])
def test_late_verified_receipt_settles_evicted_event_once_and_preserves_lifetime_audit(
    queue, status, reason
):
    queue.start_session()
    first = enqueue(queue, 1)
    enqueue(queue, 2)
    enqueue(queue, 3)
    raw = receipt(json.loads(first.raw), first.raw, status, reason)
    bad = json.loads(raw)
    bad["wire_sha256"] = "0" * 64
    assert not queue.acknowledge(json.dumps(bad).encode())
    bad = json.loads(raw)
    bad["asset_id"] = "bus-002"
    assert not queue.acknowledge(json.dumps(bad).encode())
    assert queue.counts()["evicted_unconfirmed"] == 1
    assert queue.acknowledge(raw) and not queue.acknowledge(raw)
    counts = queue.counts()
    assert counts["accepted" if status == "accepted" else "rejected"] == 1
    assert counts["pending"] == 2 and counts["dropped"] == 0
    assert counts["evicted_unconfirmed"] == 0 and counts["evicted_lifetime"] == 1
    assert json.loads(queue.eviction_audit()[0]["receipt"]) == json.loads(raw)


def test_explicit_loss_assertion_and_later_contradicting_receipt_remain_auditable(queue):
    queue.start_session()
    first = enqueue(queue, 1)
    enqueue(queue, 2)
    enqueue(queue, 3)
    assert not queue.confirm_evicted_loss(
        first.event_id, "0" * 64, "controlled-never-published-audit"
    )
    before = dump(queue)
    for ref in (None, "", "x" * 513, "line\nbreak"):
        with pytest.raises(ValueError):
            queue.confirm_evicted_loss(first.event_id, first.wire_sha256, ref)
    assert dump(queue) == before
    assert queue.confirm_evicted_loss(
        first.event_id, first.wire_sha256, "controlled-never-published-audit"
    )
    assert not queue.confirm_evicted_loss(first.event_id, first.wire_sha256, "duplicate-audit")
    assert queue.counts()["dropped"] == 1 and queue.counts()["pending"] == 2
    assert queue.acknowledge(receipt(json.loads(first.raw), first.raw, "accepted"))
    assert queue.counts()["dropped"] == 0 and queue.counts()["accepted"] == 1
    audit = queue.eviction_audit()[0]
    assert audit["drop_audit"] == "controlled-never-published-audit"
    assert audit["disposition"] == "accepted" and audit["receipt"] is not None


def test_legacy_policy_and_bound_configuration_cannot_be_silently_changed(queue, tmp_path):
    queue.start_session()
    enqueue(queue, 1)
    before = dump(queue)
    with pytest.raises(ValueError, match="explicitly bound"):
        reopen(queue, policy=False)
    with pytest.raises(ValueError, match="immutable"):
        Spool(
            queue.path,
            queue.identity,
            queue.validate,
            read_receipt,
            max_pending_bytes=queue.max_pending_bytes,
            capacity_policy=DropOldestPolicy(queue.max_pending_bytes, 9),
        )
    assert dump(queue) == before
    legacy = Spool(tmp_path / "used.db", queue.identity, queue.validate, read_receipt)
    legacy.start_session()
    original = legacy.enqueue(samples(), 1, observed_at=STAMP)
    with pytest.raises(ValueError, match="used legacy"):
        Spool(
            legacy.path,
            legacy.identity,
            legacy.validate,
            read_receipt,
            capacity_policy=DropOldestPolicy(),
        )
    assert legacy.pending() == [original]
    assert legacy.counts() == {"pending": 1}


def test_eviction_audit_pages_are_bounded_ordered_and_complete(queue):
    queue.start_session()
    for t in range(1, 7):
        enqueue(queue, t)
    result = []
    after = 0
    while page := queue.eviction_audit(limit=1, after=after):
        result.extend(page)
        after = page[-1]["audit_sequence"]
    assert [row["stream_seq"] for row in result] == [0, 1, 2, 3]
    assert result == queue.eviction_audit()
    for kwargs in ({"limit": 0}, {"limit": 1001}, {"after": -1}, {"after": True}):
        with pytest.raises(ValueError):
            queue.eviction_audit(**kwargs)


@pytest.mark.parametrize("original_time", [2**53 - 1, 1.2345678901234567, -0.0])
def test_loss_metadata_preserves_admitted_integer_fraction_and_signed_zero_times(
    queue, original_time
):
    queue.start_session()
    first = enqueue(queue, original_time)
    enqueue(queue, 2)
    enqueue(queue, 3)
    lost = next(row for row in queue.eviction_audit() if row["event_id"] == first.event_id)
    assert json.dumps(lost["sim_time_s"]) == json.dumps(original_time)
    assert json.dumps(reopen(queue).eviction_audit()[0]["sim_time_s"]) == json.dumps(original_time)


@pytest.mark.parametrize(
    "changes",
    [
        {"encoded_limit": True},
        {"encoded_limit": 0},
        {"loss_record_limit": 0},
        {"loss_record_limit": True},
    ],
)
def test_capacity_configuration_rejects_nonpositive_and_boolean_limits(changes):
    with pytest.raises(ValueError):
        DropOldestPolicy(**changes)
