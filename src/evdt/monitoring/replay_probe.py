"""Real replay reads, scoped credentials, database denials and broker ACL negatives."""

import base64
import hashlib
import json
import os
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
from fastapi import HTTPException

from evdt.monitoring import replay_repository
from evdt.monitoring.probe_support import actor, require
from evdt.monitoring.repository import connection


def request(path, body=None, label="all", method=None):
    readers = json.loads(Path("/run/secrets/replay_readers").read_text())["readers"]
    headers = {"Content-Type": "application/json"}
    if label:
        headers["Authorization"] = "Bearer " + next(
            r["token"] for r in readers if r["label"] == label
        )
    try:
        with urlopen(
            Request(
                "http://replay:8000" + path,
                json.dumps(body).encode() if body is not None else None,
                headers,
                method=method,
            ),
            timeout=10,
        ) as r:
            return r.status, r.read(), r.headers
    except HTTPError as e:
        return e.code, e.read(), e.headers


def state_hash():
    result = {}
    with connection() as conn:
        # Fixed reviewed table names; no request text participates in SQL.
        for table in (
            "m_observations",
            "m_current",
            "m_registrations",
            "m_source_heads",
            "m_source_clocks",
            "m_clock_events",
            "m_dashboard_heads",
            "m_sample_history",
            "m_map_contexts",
            "m_dashboard_events",
            "m_quarantine",
            "m_receipts",
        ):
            rows = conn.execute(
                "SELECT to_jsonb(t)::text AS row FROM " + table + " t ORDER BY to_jsonb(t)::text"
            ).fetchall()
            result[table] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return result


def main():
    before = state_hash()
    status, raw, _ = request("/v1/replay/sources", label=None)
    require(status == 401, "Anonymous replay reader accepted")
    status, raw, _ = request("/v1/replay/sources", label="bus-001")
    require(status == 200, "Scoped replay discovery failed")
    discovered = json.loads(raw)
    require(
        [a["asset_id"] for a in discovered["assets"]] == ["bus-001"],
        "Replay discovery leaked another asset",
    )
    body = {
        "source_run_id": os.environ["EVDT_RUN_ID"],
        "asset_id": "bus-001",
        "from_s": 3,
        "to_s": 18,
    }
    status, raw, headers = request("/v1/replay/sessions", body, label="bus-001")
    require(status == 200, "Replay package creation failed: " + raw[:200].decode(errors="replace"))
    require(
        hashlib.sha256(raw).hexdigest() == headers["X-Content-SHA256"],
        "Whole package digest differs",
    )
    p = json.loads(raw)
    require(
        p["mode"] == "replay"
        and p["namespace"] == "replay/" + p["replay_run_id"]
        and p["source_run_id"] != p["replay_run_id"],
        "Replay namespace is operational",
    )
    require(all(not flag for flag in p["capabilities"].values()), "Replay advertises mutation")
    require(any(x["seed"] for x in p["packets"]), "Replay did not include before-window state")
    with connection() as conn:
        for envelope in p["packets"]:
            original = base64.b64decode(envelope["raw_base64"], validate=True)
            packet = json.loads(original)
            stored = conn.execute(
                "SELECT raw_payload,wire_sha256,received_at,persisted_at FROM m_observations WHERE run_id=%s AND event_id=%s",
                (os.environ["EVDT_RUN_ID"], packet["event_id"]),
            ).fetchone()
            require(
                bytes(stored["raw_payload"]) == original
                and stored["wire_sha256"] == envelope["wire_sha256"],
                "Replay altered original bytes",
            )
            require(
                stored["received_at"].isoformat() == envelope["received_at"]
                and stored["persisted_at"].isoformat() == envelope["persisted_at"],
                "Replay altered source receipt/persistence times",
            )
    for changed, expected in [
        ({"asset_id": "bus-002"}, 403),
        ({"source_run_id": "00000000-0000-0000-0000-000000000000"}, 403),
        ({"from_s": 20, "to_s": 1}, 422),
        ({"to_s": 86404}, 422),
        ({"dispatch": True}, 422),
    ]:
        require(
            request("/v1/replay/sessions", {**body, **changed}, label="bus-001")[0] == expected,
            "Replay request boundary failed",
        )
    for route in (
        "/v1/commands",
        "/v1/observations",
        "/v1/registrations",
        "/v1/source-clocks",
        "/v1/configuration",
    ):
        require(
            request(route, {"asset_id": "bus-001", "command": "charge"})[0] in (404, 405),
            "Replay mutation route exists",
        )
    denied = []
    for sql in (
        "UPDATE m_replay_packets SET sim_time_s=0 WHERE false",
        "UPDATE m_observations SET sim_time_s=0 WHERE false",
        "CREATE TABLE replay_forbidden_test(n integer)",
    ):
        conn = psycopg.connect(
            host="db",
            dbname="evdt",
            user="evdt_replay",
            password=Path("/run/secrets/replay_db").read_text().strip(),
        )
        try:
            try:
                conn.execute(sql)
            except psycopg.errors.InsufficientPrivilege:
                denied.append(True)
            else:
                raise RuntimeError("Replay role can mutate protected state")
        finally:
            conn.rollback()
            conn.close()
    publish_denials = []
    with actor("replay-observer", "/run/secrets/replay_mqtt") as (client, _, _):
        done = threading.Event()

        def on_publish(c, u, mid, reason, properties):
            publish_denials.append(reason.is_failure)
            done.set()

        client.on_publish = on_publish
        for topic in (
            f"evdt/simulation/{os.environ['EVDT_RUN_ID']}/bus-001/sumo-adapter/observations",
            f"evdt/commands/{os.environ['EVDT_RUN_ID']}/bus-001/charge",
        ):
            done.clear()
            client.publish(topic, b'{"mode":"replay"}', qos=1)
            require(
                done.wait(5) and publish_denials[-1],
                "Broker accepted a replay observer publication",
            )
    os.environ["SOURCE_RUN_ID"] = os.environ["EVDT_RUN_ID"]
    limits_checked = []
    for name in ("MAX_PACKETS", "MAX_RAW_BYTES", "MAX_PACKAGE_BYTES"):
        original_limit = getattr(replay_repository, name)
        setattr(replay_repository, name, 1)
        try:
            try:
                replay_repository.make_package("bus-001", 3, 18, os.environ["EVDT_RUN_ID"])
            except HTTPException as exc:
                require(exc.status_code == 413, "Replay resource boundary returned wrong status")
                limits_checked.append(name)
            else:
                raise RuntimeError("Replay ignored a resource limit")
        finally:
            setattr(replay_repository, name, original_limit)
    require(state_hash() == before, "Replay reads/negative attempts changed operational state")
    print(
        json.dumps(
            {
                "passed": True,
                "packets_compared": len(p["packets"]),
                "seed_packets": sum(x["seed"] for x in p["packets"]),
                "scoped_discovery": True,
                "scope_run_limits_mutation_routes": True,
                "database_write_denials": len(denied),
                "broker_publish_denials": len(publish_denials),
                "real_recording_resource_branches": limits_checked,
                "original_bytes_and_times": True,
                "operational_state_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
