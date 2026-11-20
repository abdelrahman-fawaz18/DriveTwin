"""Controlled network-library lock ordering and early acknowledgment regression."""

import json
import multiprocessing
import threading
import uuid
from types import SimpleNamespace

import pytest

from evdt.monitoring.spool import Identity, Pending


def callback_case(connection, rc, callback_enabled):
    from evdt.monitoring import metrics, transport

    identity = Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    body = {**identity.__dict__, "event_id": str(uuid.uuid4())}
    original = Pending(body["event_id"], json.dumps(body).encode(), "0" * 64)
    records = []
    metrics.start = lambda snapshot: None

    def emit(stage, obj=None, *, at=None, **metadata):
        records.append({"stage": stage, "identity": obj, "at": at or transport.stamp(), **metadata})

    transport.emit = emit

    class Stop:
        calls = 0

        def wait(self, interval):
            self.calls += 1
            return self.calls > 1

    class Client:
        def __init__(self):
            self.inner = threading.RLock()
            self.begin, self.held = threading.Event(), threading.Event()
            self.thread = None

        def connect_async(self, *args):
            pass

        def loop_start(self):
            self.on_subscribe(self, None, 1, [SimpleNamespace(is_failure=False)], None)

            def callback():
                assert self.begin.wait(2)
                # Pinned Paho invokes QoS-1 on_publish while its outgoing-message
                # mutex is held. Force that documented source ordering here.
                with self.inner:
                    self.held.set()
                    self.on_publish(self, None, 7, SimpleNamespace(is_failure=False), None)

            if callback_enabled:
                self.thread = threading.Thread(target=callback, daemon=True)
                self.thread.start()

        def publish(self, *args, **kwargs):
            assert args == (identity.topic, original.raw) and kwargs == {"qos": 1, "retain": False}
            if not callback_enabled:
                return SimpleNamespace(mid=7, rc=rc)
            self.begin.set()
            assert self.held.wait(2)
            # As in Paho publish(), taking the outgoing mutex must not invert an
            # application lock that its acknowledgment callback also requires.
            with self.inner:
                return SimpleNamespace(mid=7, rc=rc)

        def disconnect(self):
            pass

        def loop_stop(self):
            if self.thread is not None:
                self.thread.join(2)
                assert not self.thread.is_alive()

    value = Client()
    transport.client = lambda name: value
    spool = SimpleNamespace(identity=identity, pending=lambda: [original])
    transport.publish_pending(spool, Stop())
    attempted = [r for r in records if r["stage"] == "publish_attempt"]
    returned = [r for r in records if r["stage"] == "publish_return"]
    acknowledged = [r for r in records if r["stage"] == "broker_ack"]
    assert len(attempted) == len(returned) == 1 and len(acknowledged) == int(callback_enabled)
    assert returned[0]["rc"] == rc and returned[0]["attempt_id"] == attempted[0]["attempt_id"]
    if callback_enabled:
        assert attempted[0]["attempt_id"] == acknowledged[0]["attempt_id"]
        assert acknowledged[0]["identity"] == body
        assert acknowledged[0]["mid"] == 7 and acknowledged[0]["failed"] is False
        assert acknowledged[0]["at"]["monotonic_s"] <= returned[0]["at"]["monotonic_s"]
    assert spool.pending() == [original]
    connection.send({"completed": True, "acknowledgments": len(acknowledged), "return_code": rc})
    connection.close()


@pytest.mark.parametrize("rc,callback_enabled", [(0, True), (4, True), (15, False)])
def test_publisher_and_early_broker_callback_cannot_deadlock_or_lose_correlation(
    rc, callback_enabled
):
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=callback_case, args=(child, rc, callback_enabled))
    process.start()
    process.join(4)
    stuck = process.is_alive()
    if stuck:
        process.terminate()
        process.join(2)
    try:
        assert not stuck, (
            "Publisher and broker callback deadlocked across the outgoing-message lock"
        )
        assert process.exitcode == 0 and parent.poll(1)
        assert parent.recv() == {
            "completed": True,
            "acknowledgments": int(callback_enabled),
            "return_code": rc,
        }
    finally:
        parent.close()
        child.close()
