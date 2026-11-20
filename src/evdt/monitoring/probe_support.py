"""Real-service assertions for the first monitoring slice; no simulated responses."""

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt

from evdt.monitoring.contracts import observation_topic


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(path, *, credential="all", method="GET"):
    headers = {}
    if credential:
        readers = json.loads(Path(os.environ["READERS_FILE"]).read_text())["readers"]
        entry = next(r for r in readers if r["label"] == credential)
        headers["Authorization"] = "Bearer " + entry["token"]
    try:
        with urlopen(
            Request("http://api:8000" + path, headers=headers, method=method), timeout=5
        ) as result:
            return result.status, json.load(result)
    except HTTPError as exc:
        return exc.code, None


@contextmanager
def actor(username, password_file):
    connected = threading.Event()
    subscribed = threading.Event()
    messages = []
    codes = []
    value = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="probe-" + uuid.uuid4().hex,
        protocol=mqtt.MQTTv5,
    )
    value.username_pw_set(username, Path(password_file).read_text().strip())

    def on_connect(c, u, f, code, p):
        codes.append(code)
        connected.set()

    def on_subscribe(c, u, mid, reasons, p):
        if all(not code.is_failure for code in reasons):
            subscribed.set()

    value.on_connect = on_connect
    value.on_subscribe = on_subscribe
    value.on_message = lambda c, u, m: messages.append(m.payload)
    value.connect("broker", 1883)
    value.loop_start()
    try:
        require(connected.wait(5) and not codes[0].is_failure, "Probe broker authentication failed")
        yield value, messages, subscribed
    finally:
        value.disconnect()
        value.loop_stop()


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def wait_receipt(client, messages, body, *, expected_status, expected_reason=None):
    raw = canonical(body)
    digest = hashlib.sha256(raw).hexdigest()
    result = client.publish(
        observation_topic(body["run_id"], body["asset_id"], body["source_id"]), raw, qos=1
    )
    result.wait_for_publish(timeout=5)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        for payload in list(messages):
            reply = json.loads(payload)
            if reply["event_id"] == body["event_id"] and reply["wire_sha256"] == digest:
                require(reply["status"] == expected_status, "Unexpected persistence disposition")
                require(reply["reason"] == expected_reason, "Unexpected persistence reason")
                return payload
        time.sleep(0.05)
    raise RuntimeError("No matching durable application receipt")
