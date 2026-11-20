"""Count reads must not mix states across a concurrent receipt transaction."""

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager

import pytest

from evdt.monitoring.contracts import monitoring_contract, read_receipt, receipt
from evdt.monitoring.spool import Identity, Spool
from evdt.monitoring.spool_capacity import DropOldestPolicy


@pytest.mark.parametrize("oldest_first", [False, True])
def test_counts_keep_one_snapshot_during_actual_concurrent_receipt(tmp_path, oldest_first):
    queue = Spool(
        tmp_path / "queue.db",
        Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter"),
        monitoring_contract().validate,
        read_receipt,
        capacity_policy=DropOldestPolicy() if oldest_first else None,
    )
    queue.start_session()
    original = queue.enqueue(
        [
            {
                "signal_id": "location.latitude",
                "value": 45.51,
                "unit": "deg",
                "quality": "good",
                "provenance": "simulated-observation",
            }
        ],
        1,
    )
    accepted = receipt(json.loads(original.raw), original.raw, "accepted")
    before = queue.counts()
    writer_start, writer_done = threading.Event(), threading.Event()
    writer_result = []
    real_connection = queue.connection
    paused = False

    def pause_between_count_queries(sql):
        nonlocal paused
        boundary = "SELECT * FROM queue_capacity" if oldest_first else "SELECT accepted_count"
        if not paused and sql.startswith(boundary):
            paused = True
            writer_start.set()
            # The writer either commits or encounters the real reader's SQLite
            # lock immediately. No scheduling sleep or lock-state mock is used.
            assert writer_done.wait(5), "Concurrent writer failed to complete its attempt"

    @contextmanager
    def instrumented_connection():
        with real_connection() as conn:
            if threading.current_thread() is writer:
                conn.execute("PRAGMA busy_timeout=0")
            else:
                conn.set_trace_callback(pause_between_count_queries)
            yield conn

    def acknowledge():
        try:
            assert writer_start.wait(5), "Count read never reached the interleaving boundary"
            writer_result.append(queue.acknowledge(accepted))
        except sqlite3.OperationalError as exc:
            writer_result.append(exc)
        except BaseException as exc:
            writer_result.append(exc)
        finally:
            writer_done.set()

    writer = threading.Thread(target=acknowledge)
    queue.connection = instrumented_connection
    writer.start()
    try:
        during = queue.counts()
    finally:
        writer.join(timeout=6)
        queue.connection = real_connection
    assert not writer.is_alive()
    assert paused and len(writer_result) == 1
    assert during == before
    outcome = writer_result[0]
    # The read transaction held its original snapshot; the competing commit
    # rolled back with SQLITE_BUSY and can safely retry after that read closes.
    assert isinstance(outcome, sqlite3.OperationalError)
    assert outcome.sqlite_errorcode == sqlite3.SQLITE_BUSY
    assert queue.acknowledge(accepted)
    after = queue.counts()
    assert after.get("accepted") == 1 and after["pending"] == 0
    if oldest_first:
        assert after["generated"] == sum(
            after[k] for k in ("accepted", "rejected", "dropped", "pending")
        )
