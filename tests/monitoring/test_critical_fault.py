import fcntl
import json
import os
import subprocess
import sys
import uuid

import pytest

from evdt.monitoring.critical_fault import PAGE, FaultLatch, FaultUnavailable
from evdt.monitoring.spool import Identity


@pytest.fixture
def latch(tmp_path):
    return FaultLatch(
        tmp_path / "fault", Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter"), create=True
    )


def test_preallocated_fault_is_sticky_and_identity_bound_across_reopen(latch):
    assert latch.read().healthy
    for index in (0, 1):
        info = (latch.folder / ("fault." + str(index))).stat()
        assert info.st_size == PAGE and info.st_blocks * 512 >= PAGE
    first = latch.trip("telemetry_sqlite_full")
    second = latch.trip("critical_space_full")
    assert not first.healthy and not second.healthy
    assert second.body["first_reason"] == "telemetry_sqlite_full"
    assert second.body["last_reason"] == "critical_space_full"
    assert second.body["occurrences"] == 2
    assert FaultLatch(latch.folder, latch.identity).read() == second
    with pytest.raises(FileExistsError):
        FaultLatch(latch.folder, latch.identity, create=True)
    with pytest.raises(FaultUnavailable, match="another identity"):
        FaultLatch(latch.folder, Identity(latch.identity.run_id, "bus-002", "sumo-adapter"))


def test_torn_update_falls_back_to_old_valid_fault_and_repairs_only_existing_slot(latch):
    old = latch.trip("telemetry_sqlite_full")
    # Initial tie chooses slot one; the first committed update occupies slot zero.
    with (latch.folder / "fault.1").open("r+b", buffering=0) as stream:
        stream.write(b"\0" * 128)
        os.fsync(stream.fileno())
    recovered = FaultLatch(latch.folder, latch.identity).read()
    assert recovered.body == old.body and recovered.valid_copies == 1 and not recovered.healthy
    repaired = latch.trip("critical_space_full")
    assert repaired.valid_copies == 2 and repaired.body["occurrences"] == 2


def test_missing_copy_is_degraded_not_recreated_and_both_bad_fail_closed(latch):
    (latch.folder / "fault.0").unlink()
    assert not latch.read().healthy
    with pytest.raises(FaultUnavailable, match="unavailable"):
        latch.trip("missing_copy")
    assert not (latch.folder / "fault.0").exists()
    (latch.folder / "fault.1").write_bytes(b"\0" * PAGE)
    with pytest.raises(FaultUnavailable, match="Neither"):
        latch.read()


@pytest.mark.parametrize("reason", ["", "x" * 129, "line\nbreak", None])
def test_invalid_reason_never_changes_committed_status(latch, reason):
    before = latch.read()
    with pytest.raises(ValueError):
        latch.trip(reason)
    assert latch.read() == before


def test_status_symlink_is_never_followed(latch, tmp_path):
    target = tmp_path / "unrelated"
    target.write_bytes(b"u" * PAGE)
    (latch.folder / "fault.0").unlink()
    (latch.folder / "fault.0").symlink_to(target)
    assert not latch.read().healthy
    with pytest.raises(FaultUnavailable):
        latch.trip("linked_copy")
    assert target.read_bytes() == b"u" * PAGE


def test_parallel_processes_cannot_lose_fault_occurrences(latch):
    program = """import json,sys
from pathlib import Path
from evdt.monitoring.critical_fault import FaultLatch
from evdt.monitoring.spool import Identity
value=FaultLatch(Path(sys.argv[1]),Identity(**json.loads(sys.argv[2])))
for i in range(5): value.trip('controlled_parallel_fault')
"""
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", program, str(latch.folder), json.dumps(latch.identity.__dict__)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    for worker in workers:
        _, error = worker.communicate(timeout=10)
        assert worker.returncode == 0, error.decode()
    result = latch.read()
    assert result.body["occurrences"] == 10 and result.valid_copies == 2 and not result.healthy


def test_checksum_valid_but_conflicting_generations_are_rejected(latch):
    old = latch.trip("first_fault").body
    other = {**old, "last_reason": "conflicting_fault"}
    (latch.folder / "fault.1").write_bytes(FaultLatch.encode(other))
    with pytest.raises(FaultUnavailable, match="conflict"):
        latch.read()


def test_real_process_exit_during_slot_update_preserves_old_committed_fault(latch):
    old = latch.trip("first_committed_fault")
    program = """import json,os,sys
from pathlib import Path
from evdt.monitoring.critical_fault import FaultLatch
from evdt.monitoring.spool import Identity
value=FaultLatch(Path(sys.argv[1]),Identity(**json.loads(sys.argv[2])))
write=os.pwrite
def torn(fd,payload,offset):
    write(fd,b'\\0'*128,offset)
    os._exit(23)
os.pwrite=torn
value.trip('interrupted_second_fault')
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(latch.folder), json.dumps(latch.identity.__dict__)],
        capture_output=True,
    )
    assert result.returncode == 23, result.stderr.decode()
    recovered = FaultLatch(latch.folder, latch.identity).read()
    assert recovered.body == old.body and recovered.valid_copies == 1 and not recovered.healthy


def test_blocked_status_lock_times_out_without_changing_files(latch):
    before = [(latch.folder / ("fault." + str(i))).read_bytes() for i in (0, 1)]
    with (latch.folder / "fault.lock").open("r+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(FaultUnavailable, match="timed out"):
            latch.read()
    assert [(latch.folder / ("fault." + str(i))).read_bytes() for i in (0, 1)] == before
    assert latch.read().healthy
