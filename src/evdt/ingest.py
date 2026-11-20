"""Acknowledge MQTT delivery only after observation or rejection commits."""

import json
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from evdt.config import run_id
from evdt.contracts import Contract
from evdt.repository import store


def main():
    run = run_id()
    contract = Contract()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="p0-ingest-" + run,
        protocol=mqtt.MQTTv5,
        manual_ack=True,
    )
    client.reconnect_delay_set(1, 10)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            raise RuntimeError(str(reason_code))
        client.subscribe(
            f"evdt/qualification/{run}/+/observations",
            options=mqtt.SubscribeOptions(qos=1, retainAsPublished=True, retainHandling=2),
        )

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        if any(code.is_failure for code in reason_codes):
            raise RuntimeError("Subscription refused")
        print(json.dumps({"event": "subscription_ready", "run_id": run}), flush=True)

    def on_message(client, userdata, message):
        # Database failures intentionally terminate the worker without ACK.
        # Broker redelivery alone is not the future adapter's durable-spool guarantee.
        result = store(
            message.payload,
            message.topic,
            datetime.now(timezone.utc),
            run,
            contract,
            retained=message.retain,
        )
        print(json.dumps({"event": "ingest", **result}), flush=True)
        client.ack(message.mid, message.qos)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    props = Properties(PacketTypes.CONNECT)
    props.SessionExpiryInterval = 3600
    client.connect(os.environ.get("MQTT_HOST", "broker"), 1883, clean_start=False, properties=props)
    client.loop_forever()


if __name__ == "__main__":
    main()
