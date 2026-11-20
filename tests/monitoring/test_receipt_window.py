import math

import pytest

from evdt.monitoring.receipt_window import ReceiptWindow


def test_bounded_originals_wait_for_receipts_and_retry_after_elapsed_deadline():
    window = ReceiptWindow(limit=2, retry_s=5)
    window.sent("first", 0)
    window.sent("second", 0.5)
    assert not window.eligible("third", 1)
    assert not window.eligible("first", 4.999)
    assert window.eligible("first", 5)
    window.sent("first", 5)
    assert not window.eligible("first", 9.999)
    assert window.eligible("first", 10)
    assert set(window.waiting) == {"first", "second"}


def test_only_queue_disappearance_frees_slots_broker_ack_does_not():
    window = ReceiptWindow(limit=1)
    window.sent("first", 0)
    window.refresh({"first", "second"})
    assert not window.eligible("second", 1)
    window.refresh({"second"})
    assert window.eligible("second", 1)
    window.sent("second", 1)
    assert window.waiting == {"second": 1}


@pytest.mark.parametrize(
    "limit,retry",
    [(0, 5), (257, 5), (True, 5), (1, True), (1, 0.49), (1, 30.01), (1, math.nan), (1, math.inf)],
)
def test_unbounded_or_ambiguous_window_settings_fail(limit, retry):
    with pytest.raises(ValueError):
        ReceiptWindow(limit=limit, retry_s=retry)


def test_invalid_or_backward_retry_clock_cannot_trigger_a_retry():
    window = ReceiptWindow()
    window.sent("first", 10)
    for now in (9, math.nan, math.inf):
        with pytest.raises(ValueError):
            window.eligible("first", now)


def test_full_window_refuses_an_unadmitted_send_without_changing_cache():
    window = ReceiptWindow(limit=1)
    window.sent("first", 0)
    with pytest.raises(RuntimeError):
        window.sent("second", 1)
    assert window.waiting == {"first": 0}


def test_normal_publisher_limits_originals_and_does_not_retry_each_poll(monkeypatch):
    import json
    import uuid
    from types import SimpleNamespace

    from evdt.monitoring import metrics, transport
    from evdt.monitoring.spool import Identity, Pending

    monkeypatch.setenv("EVDT_RECEIPT_WINDOW_LIMIT", "32")
    monkeypatch.setenv("EVDT_RECEIPT_RETRY_SECONDS", "5")
    identity = Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    originals = [Pending(str(uuid.uuid4()), b"", "0" * 64) for _ in range(100)]
    originals = [
        Pending(
            p.event_id,
            json.dumps({**identity.__dict__, "event_id": p.event_id}).encode(),
            p.wire_sha256,
        )
        for p in originals
    ]
    clock = {"now": 0, "iteration": 0}
    calls = []
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(metrics, "start", lambda snapshot: None)
    monkeypatch.setattr(transport, "emit", lambda *args, **kwargs: None)

    class Stop:
        def wait(self, interval):
            assert interval == 0.5
            clock["iteration"] += 1
            if clock["iteration"] > 4:
                return True
            clock["now"] = (0, 0.5, 5, 5.5)[clock["iteration"] - 1]
            return False

    class Client:
        def connect_async(self, *args):
            pass

        def loop_start(self):
            self.on_subscribe(self, None, 1, [SimpleNamespace(is_failure=False)], None)

        def publish(self, topic, raw, **kwargs):
            calls.append((clock["iteration"], json.loads(raw)["event_id"]))
            mid = len(calls)
            self.on_publish(self, None, mid, SimpleNamespace(is_failure=False), None)
            return SimpleNamespace(mid=mid, rc=0)

        def disconnect(self):
            pass

        def loop_stop(self):
            pass

    monkeypatch.setattr(transport, "client", lambda name: Client())
    spool = SimpleNamespace(
        identity=identity, pending=lambda: originals[16:] if clock["iteration"] == 4 else originals
    )
    transport.publish_pending(spool, Stop())
    assert [sum(i == n for i, _ in calls) for n in (1, 2, 3, 4)] == [32, 0, 32, 16]
    assert [event for i, event in calls if i == 3] == [p.event_id for p in originals[:32]]
    assert [event for i, event in calls if i == 4] == [p.event_id for p in originals[32:48]]
