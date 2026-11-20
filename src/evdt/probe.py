"""Deterministic real-broker/database probe; deliberate errors are test inputs."""

import argparse
import copy
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from evdt.config import run_id


def get(path):
    with urlopen("http://api:8000" + path, timeout=5) as response:
        return json.load(response)


def event(batch, sequence):
    run = run_id()
    return {
        "schema_version": "1.0.0",
        "catalog_version": "0.1.0",
        "event_id": str(uuid.uuid5(uuid.UUID(run), f"{batch}:event:{sequence}")),
        "asset_id": "bus-001",
        "source_id": "fixture-adapter",
        "session_id": str(uuid.uuid5(uuid.UUID(run), f"{batch}:session")),
        "stream_seq": sequence,
        "run_id": run,
        "mode": "qualification",
        "observed_at": (
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=batch * 100 + sequence)
        )
        .isoformat()
        .replace("+00:00", "Z"),
        "clock_domain": "simulation",
        "sim_time_s": batch * 100 + sequence,
        "samples": [
            {
                "signal_id": "battery.pack.soc",
                "value": 0.72 - sequence * 0.01,
                "unit": "1",
                "quality": "good",
                "provenance": "synthetic-fixture",
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    expected = {"accepted": 3 * args.batch, "duplicates": args.batch, "rejected": 5 * args.batch}
    if not args.verify_only:
        connected = threading.Event()
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"probe-{run_id()}-{args.batch}",
            protocol=mqtt.MQTTv5,
        )

        def on_connect(client, userdata, flags, reason_code, properties):
            if not reason_code.is_failure:
                connected.set()

        client.on_connect = on_connect
        client.connect("broker", 1883)
        client.loop_start()
        if not connected.wait(10):
            raise RuntimeError("Publisher could not connect")
        try:
            # Require an active subscriber: a broker PUBACK is not proof of ingestion.
            first = event(args.batch, 1)
            conflict = copy.deepcopy(first)
            conflict["samples"][0]["value"] = 0.1
            bad_unit = event(args.batch, 4)
            bad_unit["samples"][0]["unit"] = "%"
            bad_version = event(args.batch, 5)
            bad_version["schema_version"] = "99.0.0"
            bad_identity = event(args.batch, 6)
            bad_identity["asset_id"] = "bus-999"
            payloads = [
                first,
                first,
                event(args.batch, 3),
                event(args.batch, 2),
                conflict,
                bad_unit,
                bad_version,
                bad_identity,
                b"{invalid-json",
            ]
            topic = f"evdt/qualification/{run_id()}/bus-001/observations"
            for payload in payloads:
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                props = Properties(PacketTypes.PUBLISH)
                props.MessageExpiryInterval = 60
                receipt = client.publish(topic, raw, qos=1, properties=props)
                receipt.wait_for_publish(timeout=10)
                if not receipt.is_published():
                    raise RuntimeError("Broker publish acknowledgment timed out")
        finally:
            client.disconnect()
            client.loop_stop()
    deadline = time.monotonic() + 30
    while True:
        status = get("/qualification/status")
        counters = status["counters"] or {}
        actual = {key: counters.get(key, 0) for key in expected}
        if actual == expected:
            break
        if time.monotonic() > deadline:
            raise AssertionError(f"Accounting mismatch: {actual} != {expected}")
        time.sleep(0.2)
    history = get("/v1/observations?asset_id=bus-001&signal_id=battery.pack.soc&limit=100")
    items = history["items"]
    assert len(items) == 3 * args.batch, history
    assert [x["observed_at"] for x in items] == sorted(x["observed_at"] for x in items)
    assert len({x["event_id"] for x in items}) == len(items)
    assert all(x["payload"]["run_id"] == run_id() for x in items)
    assert get("/v1/observations?asset_id=bus-999")["items"] == []
    assert get("/v1/observations?asset_id=bus-001&signal_id=unknown")["items"] == []
    assert {x["reason"]: x["count"] for x in status["reasons"]} == {
        key: args.batch
        for key in (
            "identity_conflict",
            "wrong_unit",
            "envelope_schema",
            "topic_identity",
            "invalid_json",
        )
    }
    print(
        json.dumps(
            {
                "passed": True,
                "batch": args.batch,
                "verify_only": args.verify_only,
                "counters": actual,
                "reasons": status["reasons"],
                "history": history,
            },
            default=str,
        )
    )


if __name__ == "__main__":
    main()
