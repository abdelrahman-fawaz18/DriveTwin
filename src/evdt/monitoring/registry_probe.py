"""Controlled negative/positive protocol fixtures against real private services."""

import copy
import json
import os
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from evdt.config import ROOT
from evdt.monitoring.probe_support import actor, api, canonical, require, wait_receipt
from evdt.monitoring.repository import connection
from evdt.monitoring.run_policy import digest, encoded
from evdt.monitoring.transport import open_spool


def token(label):
    if label == "reader":
        return json.loads(Path(os.environ["READERS_FILE"]).read_text())["readers"][0]["token"]
    return Path("/run/secrets/" + label).read_text().strip()


def metadata(path, value, credential="producer1", *, raw=None):
    headers = {"Content-Type": "application/json"}
    if credential:
        headers["Authorization"] = "Bearer " + token(credential)
    raw = raw if raw is not None else encoded(value)
    try:
        with urlopen(Request("http://registry:9000" + path, raw, headers), timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


class Clock:
    def __init__(self, manifest, fingerprint, credential="producer1"):
        self.identity = manifest["identity"]
        self.fingerprint, self.credential = fingerprint, credential
        self.revision = 0
        self.last = None

    def declare(self, state, sim_time):
        event = {
            **self.identity,
            "schema_version": "source-clock/1.0.0",
            "manifest_sha256": self.fingerprint,
            "event_id": str(uuid.uuid4()),
            "revision": self.revision + 1,
            "state": state,
            "sim_time_s": sim_time,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        status, reply = metadata("/v1/clock-events", event, self.credential)
        require(status == 200 and reply["accepted"], "Controlled source clock refused")
        self.revision += 1
        self.last = event
        return event


def current_lat():
    return next(
        r
        for r in api("/v1/current?asset_id=bus-001")[1]["items"]
        if r["signal_id"] == "location.latitude"
    )


def deny_database_write(role, file, statement):
    try:
        with psycopg.connect(
            host="db", dbname="evdt", user=role, password=Path(file).read_text().strip()
        ) as conn:
            conn.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        return
    raise RuntimeError("Restricted database role can change protected state")


def legacy_upgrade(first):
    """Apply actual SQL to an owned isolated schema holding a pre-registry packet."""
    from evdt.monitoring.contracts import monitoring_contract, observation_topic

    old = copy.deepcopy(first)
    old.update(schema_version="2.1.0", catalog_version="1.1.0")
    del old["manifest_sha256"]
    raw = encoded(old)
    monitoring_contract().validate(
        raw, observation_topic(old["run_id"], old["asset_id"], old["source_id"]), old["run_id"]
    )
    name = "upgrade_" + uuid.uuid4().hex
    with connection() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(name)))
        conn.execute((ROOT / "migrations/monitoring/001_observations.sql").read_text())
        conn.execute(
            "INSERT INTO m_observations(run_id,event_id,asset_id,source_id,source_epoch,session_id,stream_seq,sim_time_s,observed_at,received_at,wire_sha256,raw_payload,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp(),%s,%s,%s)",
            (
                old["run_id"],
                old["event_id"],
                old["asset_id"],
                old["source_id"],
                old["source_epoch"],
                old["session_id"],
                old["stream_seq"],
                old["sim_time_s"],
                old["observed_at"],
                digest(raw),
                raw,
                Jsonb(old),
            ),
        )
        conn.execute((ROOT / "migrations/monitoring/002_registry.sql").read_text())
        for migration in sorted((ROOT / "migrations/monitoring").glob("*.sql")):
            if migration.name > "002_registry.sql":
                conn.execute(migration.read_text())
        row = conn.execute("SELECT raw_payload,payload,admission FROM m_observations").fetchone()
        require(
            bytes(row["raw_payload"]) == raw and row["payload"] == old and row["admission"] == {},
            "Registry migration changed historical data",
        )
        require(
            conn.execute("SELECT count(*) AS n FROM m_sample_history").fetchone()["n"]
            == len(old["samples"]),
            "Dashboard history backfill lost samples",
        )
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def run_final():
    source = json.loads(Path("/source-output/source-report.json").read_text())
    manifest_raw = Path("/source-output/run-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    fingerprint = digest(manifest_raw)
    run = os.environ["EVDT_RUN_ID"]
    require(
        source["passed"] and source["paced"] and len(source["events"]) == 18,
        "Live source did not finish",
    )
    require(source["manifest_sha256"] == fingerprint, "Source recipe fingerprint mismatch")
    require(
        all(
            len(e["body"]["samples"]) == 20 and e["body"]["manifest_sha256"] == fingerprint
            for e in source["events"]
        ),
        "Missing initial signals or recipe reference",
    )
    spool = open_spool()
    require(
        spool.counts() == {"pending": 0, "accepted": 18}, "Original durable events not reconciled"
    )
    with connection() as conn:
        rows = conn.execute(
            "SELECT event_id,raw_payload FROM m_observations WHERE run_id=%s", (run,)
        ).fetchall()
    originals = {e["event_id"]: canonical(e["body"]) for e in source["events"]}
    require(
        len(rows) == 18
        and all(bytes(r["raw_payload"]) == originals[str(r["event_id"])] for r in rows),
        "Source bytes/history differ",
    )
    accepted = set(originals)
    rejected = Counter()

    # The registry was restarted by the controller; immutable recipes remain readable.
    for _ in range(20):
        try:
            status, reply = metadata("/v1/registrations", None, raw=manifest_raw)
            break
        except URLError:
            time.sleep(0.2)
    else:
        raise RuntimeError("Registry did not recover")
    require(status == 200 and reply["duplicate"], "Registration did not survive restart")
    with ThreadPoolExecutor(max_workers=4) as pool:
        repeated = list(
            pool.map(lambda _: metadata("/v1/registrations", None, raw=manifest_raw), range(4))
        )
    require(
        all(s == 200 and r["duplicate"] for s, r in repeated),
        "Concurrent duplicate registration changed state",
    )
    require(
        metadata("/v1/registrations", manifest, "reader")[0] == 401, "Reader enrolled a producer"
    )
    require(metadata("/v1/registrations", manifest, None)[0] == 401, "Anonymous producer enrolled")
    for field in ("input_sha256", "signals", "sampling", "schema_version", "identity"):
        wrong = copy.deepcopy(manifest)
        if field == "input_sha256":
            wrong[field][next(iter(wrong[field]))] = "0" * 64
        elif field == "signals":
            wrong[field]["location.latitude"]["period_s"] = 2
        elif field == "sampling":
            wrong[field]["default_period_s"] = 2
        elif field == "schema_version":
            wrong[field] = "2.1.0"
        else:
            wrong[field]["source_id"] = "competing-source"
        require(metadata("/v1/registrations", wrong)[0] in (403, 409), "Tampered recipe enrolled")
    require(
        metadata("/v1/registrations", None, raw=encoded(manifest) + b" ")[0] == 409,
        "Conflicting raw recipe replaced registration",
    )
    wrong_asset = copy.deepcopy(manifest)
    wrong_asset["identity"]["asset_id"] = "bus-002"
    wrong_asset["asset_id"] = "bus-002"
    require(metadata("/v1/registrations", wrong_asset)[0] == 403, "Producer crossed asset scope")
    headers = {"Authorization": "Bearer " + token("reader")}
    with urlopen(
        Request("http://api:8000/v1/recipes/" + fingerprint + "?asset_id=bus-001", headers=headers),
        timeout=5,
    ) as response:
        require(digest(response.read()) == fingerprint, "Recipe query changed raw registered bytes")
    require(
        "faults" not in manifest["sampling"] and "seed" not in manifest["sampling"],
        "Hidden sensor schedule leaked",
    )

    with connection() as conn:
        original_clock = conn.execute(
            "SELECT payload FROM m_clock_events WHERE run_id=%s AND asset_id='bus-001' ORDER BY revision DESC LIMIT 1",
            (run,),
        ).fetchone()["payload"]
    original_clock.update(
        event_id=str(uuid.uuid4()),
        revision=original_clock["revision"] + 1,
        state="running",
        observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    require(
        metadata("/v1/clock-events", original_clock)[1].get("detail") == "clock_finished",
        "Finished session resumed without registration",
    )
    resumed = copy.deepcopy(manifest)
    resumed["identity"].update(source_epoch=2, session_id=str(uuid.uuid4()))
    require(
        metadata("/v1/registrations", resumed)[1].get("detail") == "simulation_time_reset",
        "Producer time reset accepted",
    )
    resumed.update(minimum_sim_time_s=18, source_epoch_start_sim_s=18, initial_state_sim_s=18.2)
    reused = copy.deepcopy(resumed)
    reused["identity"]["session_id"] = manifest["identity"]["session_id"]
    require(
        metadata("/v1/registrations", reused)[1].get("detail") == "session_reused",
        "Session identity reused across epochs",
    )
    skipped = copy.deepcopy(resumed)
    skipped["identity"]["source_epoch"] = 3
    require(
        metadata("/v1/registrations", skipped)[1].get("detail") == "epoch_not_next",
        "Unregistered source epoch skipped",
    )
    resumed_raw = encoded(resumed)
    status, reply = metadata("/v1/registrations", None, raw=resumed_raw)
    require(status == 200, "Explicit session continuation refused")
    resumed_hash = reply["manifest_sha256"]
    clock = Clock(resumed, resumed_hash)
    clock.declare("running", 20)
    original_clock["observed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    require(
        metadata("/v1/clock-events", original_clock)[1].get("detail") == "retired_clock_source",
        "Retired producer changed active clock",
    )
    for changes, reason in [
        ({"revision": clock.revision}, "clock_revision"),
        ({"sim_time_s": 19}, "clock_regression"),
        (
            {
                "observed_at": (datetime.now(timezone.utc) + timedelta(seconds=60))
                .isoformat()
                .replace("+00:00", "Z")
            },
            "clock_timestamp",
        ),
    ]:
        wrong_clock = {
            **clock.last,
            "event_id": str(uuid.uuid4()),
            "revision": clock.revision + 1,
            **changes,
        }
        require(
            metadata("/v1/clock-events", wrong_clock)[1].get("detail") == reason,
            "Invalid clock transition accepted",
        )
    require(
        metadata("/v1/clock-events", {**clock.last, "state": "paused"})[1].get("detail")
        == "clock_identity_conflict",
        "Clock event identity rewritten",
    )

    with actor("adapter-bus-001", "/run/secrets/adapter_mqtt") as (writer, messages, subscribed):
        from evdt.monitoring.contracts import receipt, receipt_topic

        writer.subscribe(receipt_topic(run, "bus-001", "sumo-adapter"), qos=1)
        require(subscribed.wait(5), "Receipt subscription not ready")
        first = source["events"][0]["body"]
        wait_receipt(writer, messages, first, expected_status="accepted")

        def observe(body, reason=None):
            wait_receipt(
                writer,
                messages,
                body,
                expected_status="rejected" if reason else "accepted",
                expected_reason=reason,
            )
            if reason:
                rejected[reason] += 1
            else:
                accepted.add(body["event_id"])

        def packet(seq, t, samples=None):
            obj = copy.deepcopy(source["events"][-1]["body"])
            obj.update(
                resumed["identity"],
                event_id=str(uuid.uuid4()),
                stream_seq=seq,
                sim_time_s=t,
                manifest_sha256=resumed_hash,
                observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )
            obj["annotations"]["manifest_sha256"] = resumed_hash
            if samples is not None:
                obj["samples"] = samples
            return obj

        conflict = copy.deepcopy(first)
        conflict["samples"][0]["value"] += 0.001
        observe(conflict, "identity_conflict")
        for change, reason in [
            ({"manifest_sha256": "0" * 64}, "manifest_mismatch"),
            ({"source_epoch": 99}, "source_not_registered"),
            ({"session_id": str(uuid.uuid4())}, "session_mismatch"),
            ({"sim_time_s": 21}, "ahead_of_declared_clock"),
            ({"sim_time_s": 17}, "simulation_time_reset"),
            ({"observed_at": first["observed_at"]}, "observation_predates_registration"),
            (
                {
                    "observed_at": (datetime.now(timezone.utc) + timedelta(seconds=60))
                    .isoformat()
                    .replace("+00:00", "Z")
                },
                "future_timestamp",
            ),
            ({"mode": "replay"}, "envelope_schema"),
        ]:
            obj = packet(10, 20)
            obj.update(change)
            observe(obj, reason)
        wrong = packet(10, 20)
        wrong["samples"][0]["unit"] = "rad"
        observe(wrong, "wrong_unit")
        concurrent_invalid = packet(30, 20)
        concurrent_invalid["observed_at"] = (
            (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        )
        from evdt.monitoring.contracts import monitoring_contract, observation_topic
        from evdt.monitoring.repository import store

        raw_invalid = encoded(concurrent_invalid)
        with ThreadPoolExecutor(max_workers=2) as pool:
            dispositions = list(
                pool.map(
                    lambda _: store(
                        raw_invalid,
                        observation_topic(run, "bus-001", "sumo-adapter"),
                        run,
                        monitoring_contract(),
                    ),
                    range(2),
                )
            )
        require(
            dispositions[0]["receipt"] == dispositions[1]["receipt"],
            "Concurrent rejected retry changed its receipt",
        )
        rejected["future_timestamp"] += 1
        wait_receipt(
            writer,
            messages,
            concurrent_invalid,
            expected_status="rejected",
            expected_reason="future_timestamp",
        )
        old = packet(10, 20)
        old.update(schema_version="2.1.0", catalog_version="1.1.0")
        del old["manifest_sha256"]
        observe(old, "registered_format_required")
        observe(packet(2, 20))
        observe(packet(0, 18))
        observe(packet(1, 19))
        require(
            current_lat()["observation"]["sim_time_s"] == 20, "Late observations regressed current"
        )
        retired = copy.deepcopy(source["events"][-1]["body"])
        retired.update(event_id=str(uuid.uuid4()), stream_seq=18)
        observe(retired)
        retired_bad = copy.deepcopy(retired)
        retired_bad.update(event_id=str(uuid.uuid4()), stream_seq=19, sim_time_s=18.1)
        observe(retired_bad, "retired_source_time")
        observe(packet(3, 19), "sequence_time_conflict")

        from evdt.monitoring.sensors import SensorBank

        sensor = SensorBank(
            {
                "signals": {
                    "location.latitude": monitoring_contract().catalog["signals"][
                        "location.latitude"
                    ]
                }
            },
            0.2,
            {
                "default_period_s": 1,
                "period_overrides_s": {},
                "faults": {"location.latitude": {"missing": [[21, 22]]}},
            },
        )
        sample = copy.deepcopy(first["samples"][0])
        clock.declare("running", 21)
        missing = sensor.sample(105, {"location.latitude": sample["value"]})[0]
        observe(packet(3, 21, [missing]))
        require(
            current_lat()["quality"] == "missing"
            and current_lat()["observation"]["sample"]["value"] is None,
            "Missing sensor fabricated a number",
        )
        clock.declare("running", 22)
        invalid = sensor.sample(110, {"location.latitude": 120})[0]
        observe(packet(4, 22, [invalid]))
        require(
            current_lat()["quality"] == "invalid"
            and current_lat()["observation"]["sample"]["raw_value"] == 120,
            "Invalid sensor lost raw audit value",
        )
        clock.declare("running", 23)
        sample = sensor.sample(115, {"location.latitude": sample["value"]})[0]
        observe(packet(5, 23, [sample]))
        require(
            current_lat()["quality"] == "good" and current_lat()["freshness"] == "fresh",
            "Sensor recovery not visible",
        )
        clock.declare("paused", 23)
        for _ in range(6):
            time.sleep(1)
            clock.declare("paused", 23)
        require(current_lat()["freshness"] == "paused", "Healthy pause became disconnected")
        bad_clock = {
            **clock.last,
            "event_id": str(uuid.uuid4()),
            "revision": clock.revision + 1,
            "sim_time_s": 24,
        }
        require(
            metadata("/v1/clock-events", bad_clock)[1].get("detail") == "paused_clock_advanced",
            "Paused clock advanced",
        )
        clock.declare("running", 23)
        require(current_lat()["freshness"] == "stale", "Resume relabeled old UTC data fresh")
        clock.declare("running", 24)
        observe(packet(6, 24, [sample]))
        clock.declare("paused", 24)
        time.sleep(5.2)
        require(
            current_lat()["source_state"] == "disconnected"
            and current_lat()["freshness"] == "stale",
            "Lost paused heartbeat remained healthy",
        )
        require(
            metadata("/v1/clock-events", clock.last)[1]["duplicate"],
            "Duplicate clock not idempotent",
        )
        require(
            current_lat()["source_state"] == "disconnected", "Replayed clock refreshed liveness"
        )
        clock.declare("running", 24)
        clock.declare("running", 25)
        observe(packet(7, 25, [sample]))
        before = len(messages)
        with actor("observer", "/run/secrets/observer_mqtt") as (observer, _, _):
            observer.publish(
                receipt_topic(run, "bus-001", "sumo-adapter"),
                receipt(first, canonical(first), "accepted"),
                qos=1,
            ).wait_for_publish(timeout=3)
            time.sleep(0.5)
        require(len(messages) == before, "Observer forged a persistence receipt")

    second = copy.deepcopy(manifest)
    second["identity"].update(asset_id="bus-002", session_id=str(uuid.uuid4()))
    second["asset_id"] = "bus-002"
    status, reply = metadata("/v1/registrations", second, "producer2")
    require(status == 200, "Second authorized asset refused")
    second_hash = reply["manifest_sha256"]
    Clock(second, second_hash, "producer2").declare("running", 1)
    obj = copy.deepcopy(first)
    obj.update(
        second["identity"],
        event_id=str(uuid.uuid4()),
        manifest_sha256=second_hash,
        observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    obj["samples"] = obj["samples"][:1]
    with actor("adapter-bus-002", "/run/secrets/adapter2_mqtt") as (writer, messages, subscribed):
        from evdt.monitoring.contracts import receipt_topic

        writer.subscribe(receipt_topic(run, "bus-002", "sumo-adapter"), qos=1)
        require(subscribed.wait(5), "Second asset receipt subscription not ready")
        wait_receipt(writer, messages, obj, expected_status="accepted")
        accepted.add(obj["event_id"])
    require(
        api("/v1/recipes/" + second_hash + "?asset_id=bus-002", credential="bus-001")[0] == 403,
        "Recipe crossed reader asset scope",
    )
    sources = api("/v1/run", credential="bus-001")[1]["sources"]
    require(
        len(sources) == 2 and all(r["asset_id"] == "bus-001" for r in sources),
        "Run discovery leaked another asset",
    )
    require(
        api("/v1/observations?asset_id=bus-002", credential="bus-001")[0] == 403,
        "History crossed reader scope",
    )
    require(
        api("/commands", method="POST")[0] == 404
        and api("/v1/registrations", method="POST")[0] == 404,
        "Read API exposes mutation route",
    )
    deny_database_write(
        "evdt_read", "/run/secrets/read_db", "UPDATE m_source_clocks SET revision=0 WHERE false"
    )
    deny_database_write(
        "evdt_registry",
        "/run/secrets/registry_db",
        "UPDATE m_observations SET sim_time_s=0 WHERE false",
    )
    deny_database_write(
        "evdt_ingest",
        "/run/secrets/ingest_db",
        "UPDATE m_registrations SET minimum_sim_time_s=0 WHERE false",
    )
    with connection() as conn:
        from evdt.monitoring.registry import provision

        definition = conn.execute(
            "SELECT raw_policy FROM m_run_definitions WHERE run_id=%s", (run,)
        ).fetchone()
    changed = json.loads(bytes(definition["raw_policy"]))
    changed["sources"][0]["expected"]["heartbeat_deadline_s"] = 6
    try:
        with connection() as conn:
            provision(conn, encoded(changed))
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Existing run policy was overwritten")
    with connection() as conn:
        actual = conn.execute(
            "SELECT event_id,admission FROM m_observations WHERE run_id=%s", (run,)
        ).fetchall()
        rejected_rows = conn.execute(
            "SELECT reason FROM m_quarantine WHERE run_id=%s", (run,)
        ).fetchall()
    require(
        {str(r["event_id"]) for r in actual} == accepted, "Accepted identities do not reconcile"
    )
    require(
        Counter(r["reason"] for r in rejected_rows) == rejected,
        "Rejection reasons do not reconcile",
    )
    require(any(r["admission"].get("late") for r in actual), "History lateness not recorded")
    legacy_upgrade(first)
    print(
        json.dumps(
            {
                "passed": True,
                "live_source_events": 18,
                "live_source_scalar_samples": 360,
                "manifest_reference_verified": True,
                "historical_sql_upgrade_preserved_bytes": True,
                "sensor_layer_quality_through_ingress": True,
                "registered_recipes": 3,
                "accepted_events_including_controlled_cases": len(accepted),
                "terminal_rejections": dict(rejected),
                "spool": spool.counts(),
                "checks": [
                    "registry restart",
                    "concurrent idempotence",
                    "recipe hash/version/cadence and identity binding",
                    "read-only recipe/run discovery",
                    "producer and reader scopes",
                    "run policy immutable",
                    "session continuation/time-reset rejection",
                    "retired backlog",
                    "sequence/time ordering",
                    "ahead-clock rejection",
                    "missing/invalid/recovery",
                    "healthy pause and heartbeat loss",
                    "clock replay does not renew liveness",
                    "resume does not renew old sample",
                    "restricted DB roles",
                    "exact bytes and receipts",
                    "no command endpoint",
                ],
            }
        )
    )
