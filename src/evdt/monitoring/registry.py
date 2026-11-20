"""Immutable registered recipes and transactionally ordered clock declarations."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from evdt.contract_profile import Rejected
from evdt.monitoring.formats import checked_manifest, supported_pair
from evdt.monitoring.repository import connection
from evdt.monitoring.run_policy import (
    checked,
    digest,
    identity_key,
    source_spec,
    validate_manifest,
    validate_policy,
)


def source_lock(conn, obj):
    key = "/".join(identity_key(obj)).encode()
    number = int.from_bytes(hashlib.sha256(key).digest()[:8], "big", signed=True)
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (number,))


def provision(conn, raw):
    policy = validate_policy(raw)
    from evdt.monitoring.contracts import monitoring_contract

    contract = monitoring_contract()
    for source in policy["sources"]:
        catalog = contract.readers[source["expected"]["catalog_version"]].catalog
        asset = catalog["assets"].get(source["asset_id"])
        if (
            not asset
            or source["source_id"] not in asset["sources"]
            or set(source["expected"]["signals"]) - set(catalog["signals"])
        ):
            raise ValueError("Policy references an unregistered asset/source/signal")
    run = policy["run_id"]
    conn.execute(
        "INSERT INTO m_run_definitions(run_id,policy_sha256,raw_policy,policy) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (run, digest(raw), raw, Jsonb(policy)),
    )
    previous = conn.execute(
        "SELECT policy_sha256 FROM m_run_definitions WHERE run_id=%s", (run,)
    ).fetchone()
    if previous["policy_sha256"] != digest(raw):
        raise RuntimeError("Run policy is immutable; provision a new run")


def policy_for(conn, run):
    row = conn.execute("SELECT policy FROM m_run_definitions WHERE run_id=%s", (run,)).fetchone()
    if not row:
        raise Rejected("run_not_registered")
    return row["policy"]


def register(raw):
    manifest = checked_manifest(raw)
    obj = manifest["identity"]
    key = identity_key(obj)
    with connection() as conn:
        source_lock(conn, obj)
        manifest = validate_manifest(raw, policy_for(conn, key[0]))
        previous = conn.execute(
            "SELECT manifest_sha256 FROM m_registrations WHERE run_id=%s AND asset_id=%s AND source_id=%s AND source_epoch=%s",
            (*key, obj["source_epoch"]),
        ).fetchone()
        if previous:
            if previous["manifest_sha256"] != digest(raw):
                raise Rejected("registration_conflict")
            return {"registered": True, "manifest_sha256": digest(raw), "duplicate": True}
        reused = conn.execute(
            "SELECT 1 FROM m_registrations WHERE run_id=%s AND asset_id=%s AND source_id=%s AND session_id=%s",
            (*key, obj["session_id"]),
        ).fetchone()
        if reused:
            raise Rejected("session_reused")
        head = conn.execute(
            "SELECT source_epoch FROM m_source_heads WHERE run_id=%s AND asset_id=%s AND source_id=%s",
            key,
        ).fetchone()
        if obj["source_epoch"] != (head["source_epoch"] + 1 if head else 1):
            raise Rejected("epoch_not_next")
        clock = conn.execute(
            "SELECT sim_time_s FROM m_source_clocks WHERE run_id=%s AND asset_id=%s AND source_id=%s",
            key,
        ).fetchone()
        latest = conn.execute(
            "SELECT max(sim_time_s) AS t FROM m_observations WHERE run_id=%s AND asset_id=%s AND source_id=%s",
            key,
        ).fetchone()["t"]
        floor = max(clock["sim_time_s"] if clock else 0, latest or 0)
        minimum = manifest["minimum_sim_time_s"]
        if minimum < floor:
            raise Rejected("simulation_time_reset")
        if head:
            conn.execute(
                "INSERT INTO m_retired_sources VALUES(%s,%s,%s,%s,%s)",
                (*key, head["source_epoch"], minimum),
            )
        conn.execute(
            "INSERT INTO m_registrations(run_id,asset_id,source_id,source_epoch,session_id,manifest_sha256,minimum_sim_time_s,raw_manifest,manifest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                *key,
                obj["source_epoch"],
                obj["session_id"],
                digest(raw),
                minimum,
                raw,
                Jsonb(manifest),
            ),
        )
        conn.execute(
            "INSERT INTO m_source_heads VALUES(%s,%s,%s,%s) ON CONFLICT(run_id,asset_id,source_id) DO UPDATE SET source_epoch=excluded.source_epoch",
            (*key, obj["source_epoch"]),
        )
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO m_source_clocks VALUES(%s,%s,%s,%s,0,'running',%s,%s,%s,NULL) ON CONFLICT(run_id,asset_id,source_id) DO UPDATE SET source_epoch=excluded.source_epoch,revision=0,state='running',sim_time_s=excluded.sim_time_s,observed_at=excluded.observed_at,received_at=excluded.received_at,pause_started_at=NULL",
            (*key, obj["source_epoch"], minimum, now, now),
        )
    return {"registered": True, "manifest_sha256": digest(raw), "duplicate": False}


def binding(conn, obj):
    key = identity_key(obj)
    row = conn.execute(
        "SELECT * FROM m_registrations WHERE run_id=%s AND asset_id=%s AND source_id=%s AND source_epoch=%s",
        (*key, obj["source_epoch"]),
    ).fetchone()
    if row is None:
        raise Rejected("source_not_registered")
    if row["session_id"] != uuid.UUID(obj["session_id"]):
        raise Rejected("session_mismatch")
    if row["manifest_sha256"] != obj["manifest_sha256"]:
        raise Rejected("manifest_mismatch")
    return row


def clock_event(raw):
    obj = checked(raw, "source-clock.schema.json")
    key = identity_key(obj)
    now = datetime.now(timezone.utc)
    stamp = datetime.fromisoformat(obj["observed_at"].replace("Z", "+00:00"))
    with connection() as conn:
        source_lock(conn, obj)
        registration = binding(conn, obj)
        previous = conn.execute(
            "SELECT wire_sha256 FROM m_clock_events WHERE run_id=%s AND event_id=%s",
            (key[0], obj["event_id"]),
        ).fetchone()
        if previous:
            if previous["wire_sha256"] != digest(raw):
                raise Rejected("clock_identity_conflict")
            return {"accepted": True, "duplicate": True}
        if (stamp - now).total_seconds() > 0.05 or (now - stamp).total_seconds() > registration[
            "manifest"
        ]["heartbeat_deadline_s"]:
            raise Rejected("clock_timestamp")
        current = conn.execute(
            "SELECT * FROM m_source_clocks WHERE run_id=%s AND asset_id=%s AND source_id=%s", key
        ).fetchone()
        if current["source_epoch"] != obj["source_epoch"]:
            raise Rejected("retired_clock_source")
        if obj["revision"] != current["revision"] + 1:
            raise Rejected("clock_revision")
        if current["state"] == "finished":
            raise Rejected("clock_finished")
        if stamp < current["observed_at"] or obj["sim_time_s"] < current["sim_time_s"]:
            raise Rejected("clock_regression")
        if current["state"] == "paused" and obj["sim_time_s"] != current["sim_time_s"]:
            raise Rejected("paused_clock_advanced")
        conn.execute(
            "INSERT INTO m_clock_events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                *key,
                obj["source_epoch"],
                obj["event_id"],
                obj["revision"],
                digest(raw),
                raw,
                Jsonb(obj),
            ),
        )
        pause_started = (
            (current["pause_started_at"] if current["state"] == "paused" else stamp)
            if obj["state"] == "paused"
            else None
        )
        conn.execute(
            "UPDATE m_source_clocks SET revision=%s,state=%s,sim_time_s=%s,observed_at=%s,received_at=%s,pause_started_at=%s WHERE run_id=%s AND asset_id=%s AND source_id=%s",
            (obj["revision"], obj["state"], obj["sim_time_s"], stamp, now, pause_started, *key),
        )
    return {"accepted": True, "duplicate": False}


def admit(conn, obj, now):
    if not supported_pair(obj["schema_version"], obj["catalog_version"]):
        raise Rejected("registered_format_required")
    source_lock(conn, obj)
    row = binding(conn, obj)
    observed = datetime.fromisoformat(obj["observed_at"].replace("Z", "+00:00"))
    if observed < row["registered_at"] - timedelta(seconds=0.05):
        raise Rejected("observation_predates_registration")
    key = identity_key(obj)
    spec = source_spec(policy_for(conn, key[0]), obj)["expected"]
    if any(obj[k] != row["manifest"][k] for k in ("schema_version", "catalog_version")) or any(
        s["signal_id"] not in spec["signals"] for s in obj["samples"]
    ):
        raise Rejected("signal_owner_mismatch")
    if obj["sim_time_s"] < row["minimum_sim_time_s"]:
        raise Rejected("simulation_time_reset")
    clock = conn.execute(
        "SELECT * FROM m_source_clocks WHERE run_id=%s AND asset_id=%s AND source_id=%s", key
    ).fetchone()
    if obj["sim_time_s"] > clock["sim_time_s"] + 1e-9:
        raise Rejected("ahead_of_declared_clock")
    retired = conn.execute(
        "SELECT maximum_sim_time_s FROM m_retired_sources WHERE run_id=%s AND asset_id=%s AND source_id=%s AND source_epoch=%s",
        (*key, obj["source_epoch"]),
    ).fetchone()
    if retired and obj["sim_time_s"] > retired["maximum_sim_time_s"]:
        raise Rejected("retired_source_time")
    neighbors = []
    for operator, ordering in [("<", "DESC"), (">", "ASC")]:
        neighbor = conn.execute(
            "SELECT stream_seq,sim_time_s FROM m_observations WHERE run_id=%s AND asset_id=%s AND source_id=%s AND source_epoch=%s AND stream_seq "
            + operator
            + " %s ORDER BY stream_seq "
            + ordering
            + " LIMIT 1",
            (*key, obj["source_epoch"], obj["stream_seq"]),
        ).fetchone()
        if neighbor:
            neighbors.append(neighbor)
    for other in neighbors:
        if (
            other["stream_seq"] < obj["stream_seq"] and other["sim_time_s"] > obj["sim_time_s"]
        ) or (other["stream_seq"] > obj["stream_seq"] and other["sim_time_s"] < obj["sim_time_s"]):
            raise Rejected("sequence_time_conflict")
    return {
        "late": obj["sim_time_s"] < clock["sim_time_s"],
        "registered_manifest": row["manifest_sha256"],
    }
