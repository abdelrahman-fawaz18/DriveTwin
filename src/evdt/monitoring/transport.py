"""Authenticated MQTT transport; payloads remain owned by the durable queue."""

import os
import queue
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

from evdt.monitoring.contracts import monitoring_contract, read_receipt, receipt_topic
from evdt.monitoring.formats import operational_format
from evdt.monitoring.receipt_window import ReceiptWindow
from evdt.monitoring.spool import Identity, Spool
from evdt.monitoring.trace import emit, packet, stamp


def client(identity, *, manual_ack=False):
    value = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=identity,
        protocol=mqtt.MQTTv5,
        manual_ack=manual_ack,
    )
    value.username_pw_set(
        os.environ["MQTT_USER"], Path(os.environ["MQTT_PASSWORD_FILE"]).read_text().strip()
    )
    value.reconnect_delay_set(1, 5)
    value.max_inflight_messages_set(32)
    value.max_queued_messages_set(256)
    return value


def open_spool():
    identity = Identity(os.environ["EVDT_RUN_ID"], os.environ["ASSET_ID"], os.environ["SOURCE_ID"])
    contract = monitoring_contract()
    catalog_version = os.environ.get("CATALOG_VERSION", contract.profile["current_catalog_version"])
    binding_file = os.environ.get("EVDT_STORAGE_BINDING_FILE")
    if binding_file is not None:
        from evdt.monitoring.vehicle_storage import BoundedStorage, regular_bytes

        storage = BoundedStorage(regular_bytes(Path(binding_file)), identity)
        return storage.open(Path(os.environ["SPOOL_FILE"]), catalog_version)
    return Spool(
        Path(os.environ["SPOOL_FILE"]),
        identity,
        contract.validate,
        read_receipt,
        schema_version=operational_format(catalog_version)["schema_version"],
        catalog_version=catalog_version,
    )


def publish_pending(spool, stop):
    from evdt.monitoring.metrics import spool_status, start

    window = ReceiptWindow(
        int(os.environ.get("EVDT_RECEIPT_WINDOW_LIMIT", "32")),
        float(os.environ.get("EVDT_RECEIPT_RETRY_SECONDS", "5")),
    )
    connected = threading.Event()
    metrics = start(
        lambda: {
            **spool_status(spool, connected),
            "delivery": {
                "application_window_limit": window.limit,
                "application_retry_seconds": window.retry_s,
            },
        }
    )
    identity = spool.identity
    value = client("adapter-" + identity.asset_id + "-" + identity.run_id)
    attempts = {}
    acknowledgments = queue.Queue(maxsize=512)
    acknowledgment_overflow = threading.Event()

    def drain_acknowledgments():
        if acknowledgment_overflow.is_set():
            raise RuntimeError("Bounded broker-acknowledgment trace queue overflowed")
        while True:
            try:
                mid, acknowledged, failed = acknowledgments.get_nowait()
            except queue.Empty:
                return
            attempt = attempts.pop(mid, None)
            if attempt is None:
                emit("broker_ack_unmatched", at=acknowledged, mid=mid, failed=failed)
            else:
                attempt_id, correlation = attempt
                emit(
                    "broker_ack",
                    correlation,
                    at=acknowledged,
                    attempt_id=attempt_id,
                    mid=mid,
                    failed=failed,
                )

    def on_connect(c, u, f, code, p):
        if not code.is_failure:
            c.subscribe(
                receipt_topic(identity.run_id, identity.asset_id, identity.source_id), qos=1
            )

    def on_subscribe(c, u, mid, codes, p):
        if all(not code.is_failure for code in codes):
            connected.set()

    def on_disconnect(c, u, f, code, p):
        connected.clear()

    def on_message(c, u, message):
        try:
            applied = spool.acknowledge(message.payload)
            emit("receipt_applied", packet(message.payload), applied=applied)
        except (ValueError, TypeError):
            # Invalid receipts cannot retire queue entries.
            pass

    def on_publish(c, u, mid, reason, properties):
        # Paho can hold its outgoing-message mutex while calling this function.
        # Never wait for an application lock held across value.publish().
        try:
            acknowledgments.put_nowait((mid, stamp(), reason.is_failure))
        except queue.Full:
            acknowledgment_overflow.set()

    value.on_connect = on_connect
    value.on_subscribe = on_subscribe
    value.on_disconnect = on_disconnect
    value.on_message = on_message
    value.on_publish = on_publish
    value.connect_async(os.environ.get("MQTT_HOST", "broker"), 1883)
    value.loop_start()
    try:
        while not stop.wait(0.5):
            drain_acknowledgments()
            if connected.is_set():
                batch = spool.pending()
                window.refresh({pending.event_id for pending in batch})
                for pending in batch:
                    if not window.eligible(pending.event_id, time.monotonic()):
                        continue
                    correlation = packet(pending.raw)
                    attempt_id = str(uuid.uuid4())
                    emit("publish_attempt", correlation, attempt_id=attempt_id)
                    info = value.publish(identity.topic, pending.raw, qos=1, retain=False)
                    # Paho retains disconnected QoS-1 publications for reconnect.
                    if info.rc in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_NO_CONN):
                        if info.mid in attempts or len(attempts) >= 512:
                            raise RuntimeError(
                                "Broker-acknowledgment attempt tracking is inconsistent"
                            )
                        attempts[info.mid] = (attempt_id, correlation)
                        window.sent(pending.event_id, time.monotonic())
                    emit(
                        "publish_return",
                        correlation,
                        attempt_id=attempt_id,
                        mid=info.mid,
                        rc=int(info.rc),
                    )
                    # Registration precedes draining even when the callback ran
                    # before publish() returned. Only this thread owns the map.
                    drain_acknowledgments()
                    if info.rc != mqtt.MQTT_ERR_SUCCESS:
                        break
    finally:
        if metrics is not None:
            metrics.shutdown()
            metrics.server_close()
        value.disconnect()
        value.loop_stop()
        drain_acknowledgments()


if __name__ == "__main__":
    stop = threading.Event()
    try:
        publish_pending(open_spool(), stop)
    except KeyboardInterrupt:
        stop.set()
