"""Commit history/receipt before acknowledging an incoming broker delivery."""

import json
import os

import paho.mqtt.client as mqtt

from evdt.monitoring.contracts import monitoring_contract, receipt_topic
from evdt.monitoring.repository import store
from evdt.monitoring.trace import emit, stamp, wire
from evdt.monitoring.transport import client


def main():
    from evdt.monitoring.metrics import start

    run = os.environ["EVDT_RUN_ID"]
    contract = monitoring_contract()
    value = client("ingest-" + run, manual_ack=True)
    start(lambda: {"broker_connected": value.is_connected()})

    def on_connect(c, u, f, code, p):
        if code.is_failure:
            raise RuntimeError("Broker authentication refused")
        c.subscribe(
            f"evdt/simulation/{run}/+/+/observations",
            options=mqtt.SubscribeOptions(qos=1, retainAsPublished=True, retainHandling=2),
        )

    def on_subscribe(c, u, mid, codes, p):
        if any(code.is_failure for code in codes):
            raise RuntimeError("Observation subscription refused")
        print(json.dumps({"event": "monitoring_ingest_ready"}), flush=True)

    def on_message(c, u, message):
        arrived = stamp()
        result = store(message.payload, message.topic, run, contract, retained=message.retain)
        emit(
            "receiver_callback",
            result["obj"],
            at=arrived,
            wire_sha256=wire(message.payload),
            broker_mid=message.mid,
            duplicate_flag=message.dup,
            qos=message.qos,
        )
        emit(
            "delivery_complete",
            result["obj"],
            outcome=result["outcome"],
            wire_sha256=wire(message.payload),
        )
        if result["receipt"] is not None:
            obj = result["obj"]
            info = c.publish(
                receipt_topic(run, obj["asset_id"], obj["source_id"]),
                result["receipt"],
                qos=1,
                retain=False,
            )
            if info.rc != 0:
                raise RuntimeError("Receipt could not be queued; sender retains its event")
        # Never block the network callback waiting for a publish callback.
        c.ack(message.mid, message.qos)

    value.on_connect = on_connect
    value.on_subscribe = on_subscribe
    value.on_message = on_message
    value.connect(os.environ.get("MQTT_HOST", "broker"), 1883)
    value.loop_forever()


if __name__ == "__main__":
    main()
