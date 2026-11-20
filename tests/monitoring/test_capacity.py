"""Size-envelope and refused-insert accounting checks on the production queue."""

import json
import uuid

import pytest

from evdt.monitoring.spool import Identity, SpoolFull
from evdt.workload.capacity import annotations, enqueue, inventory, open_queue, samples


def test_valid_maximum_workload_packet_size_across_sequence_widths(tmp_path):
    spool = open_queue(
        tmp_path / "capacity.db", Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    )
    spool.start_session()
    for index in [0, 9, 10, 999, 1000]:
        event = enqueue(spool, index)
        body = json.loads(event.raw)
        assert len(event.raw) == 8192
        assert len(body["samples"]) == 20
        assert all(len(value) <= 256 for value in body["annotations"].values())


def test_capacity_refusal_preserves_originals_and_next_sequence(tmp_path):
    spool = open_queue(
        tmp_path / "capacity.db", Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    )
    spool.max_pending_bytes = 2 * 8192
    spool.start_session()
    enqueue(spool, 0)
    enqueue(spool, 1)
    before = inventory(spool)
    with pytest.raises(SpoolFull):
        enqueue(spool, 2)
    assert inventory(spool) == before
    assert before["state"]["next_seq"] == 2
    assert before["counts"] == {"pending": 2}


def test_impossible_packet_size_is_refused_before_enqueue(tmp_path):
    spool = open_queue(
        tmp_path / "capacity.db", Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    )
    spool.start_session()
    for size in [10, 65536]:
        with pytest.raises(ValueError):
            annotations(spool, 0, samples(), size)
    assert spool.counts() == {"pending": 0}
