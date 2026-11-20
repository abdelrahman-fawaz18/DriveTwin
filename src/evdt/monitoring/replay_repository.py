"""Copy bounded source recordings through SELECT-only replay views."""

import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from evdt.config import ROOT
from evdt.monitoring.contracts import monitoring_contract, observation_topic
from evdt.monitoring.repository import connection

MAX_PACKETS = 10000
MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_BYTES = 16 * 1024 * 1024


def encoded(value):
    return json.dumps(
        jsonable_encoder(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source_run():
    return os.environ["SOURCE_RUN_ID"]


def sources(assets):
    with connection() as conn:
        rows = conn.execute(
            """SELECT asset_id,count(*) AS packets,min(sim_time_s) AS from_s,max(sim_time_s) AS to_s
            FROM m_replay_packets WHERE run_id=%s AND asset_id=ANY(%s) GROUP BY asset_id ORDER BY asset_id""",
            (source_run(), assets),
        ).fetchall()
    by_asset = {row["asset_id"]: row for row in rows}
    return {
        "mode": "replay",
        "source_run_id": source_run(),
        "assets": [
            by_asset.get(asset, {"asset_id": asset, "packets": 0, "from_s": None, "to_s": None})
            for asset in assets
        ],
        "limits": {
            "packets": MAX_PACKETS,
            "raw_bytes": MAX_RAW_BYTES,
            "package_bytes": MAX_PACKAGE_BYTES,
            "window_s": 86400,
        },
        "capabilities": {
            "dispatch": False,
            "publish_observations": False,
            "write_source_state": False,
            "clock": "browser-local simulation-time playback",
        },
    }


def original_catalog(policy, asset):
    owners = [s for s in policy["sources"] if s["asset_id"] == asset]
    if not owners:
        raise HTTPException(404, "Asset has no source definition")
    versions = {s["expected"]["catalog_version"] for s in owners}
    if len(versions) != 1:
        raise HTTPException(409, "Replay needs one verifiable source catalog")
    version = next(iter(versions))
    profile = json.loads((ROOT / "contracts/monitoring.profile.json").read_text())
    path = profile["catalogs"].get(version)
    if path is None:
        raise HTTPException(409, "Historical catalog artifact unavailable")
    raw = (ROOT / path).read_bytes()
    if any(s["expected"]["input_sha256"]["catalog"] != digest(raw) for s in owners):
        raise HTTPException(409, "Historical catalog bytes differ from recorded source inputs")
    catalog = json.loads(raw)
    signals = {
        name: {
            **catalog["signals"][name],
            "sample_period_s": timing["period_s"],
            "freshness_s": timing["freshness_s"],
            "source_id": owner["source_id"],
        }
        for owner in owners
        for name, timing in owner["expected"]["signals"].items()
    }
    return {"catalog_version": version, "source_catalog_sha256": digest(raw), "signals": signals}


def make_package(asset, from_s, to_s, requested_run):
    run = source_run()
    if requested_run != run:
        raise HTTPException(403, "Source run is outside this replay deployment")
    if from_s > to_s or to_s - from_s > 86400:
        raise HTTPException(422, "Use an ordered window of at most one simulation day")
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5000ms'")
        policy_row = conn.execute(
            "SELECT policy_sha256,policy FROM m_replay_definitions WHERE run_id=%s", (run,)
        ).fetchone()
        if not policy_row:
            raise HTTPException(404, "Source run definition unavailable")
        catalog = original_catalog(policy_row["policy"], asset)
        head = conn.execute(
            "SELECT revision FROM m_replay_heads WHERE run_id=%s AND asset_id=%s", (run, asset)
        ).fetchone()
        revision = head["revision"] if head else 0
        seeds = conn.execute(
            """SELECT DISTINCT ON(signal_id) event_id FROM m_replay_seed_samples
          WHERE run_id=%s AND asset_id=%s AND dashboard_revision<=%s AND sim_time_s<%s
          ORDER BY signal_id,sim_time_s DESC,source_epoch DESC,stream_seq DESC""",
            (run, asset, revision, from_s),
        ).fetchall()
        seed_ids = list({row["event_id"] for row in seeds})
        where = "run_id=%s AND asset_id=%s AND dashboard_revision<=%s AND (sim_time_s BETWEEN %s AND %s OR event_id=ANY(%s))"
        params = (run, asset, revision, from_s, to_s, seed_ids)
        size = conn.execute(
            "SELECT count(*) AS packets,coalesce(sum(octet_length(raw_payload)),0) AS bytes FROM m_replay_packets WHERE "
            + where,
            params,
        ).fetchone()
        if size["packets"] > MAX_PACKETS or size["bytes"] > MAX_RAW_BYTES:
            raise HTTPException(413, "Replay package exceeds limits; shorten the window")
        if not size["packets"]:
            raise HTTPException(404, "No recorded observations at or before this interval")
        rows = conn.execute(
            "SELECT * FROM m_replay_packets WHERE "
            + where
            + " ORDER BY sim_time_s,source_epoch,stream_seq,event_id",
            params,
        ).fetchall()
        hashes = list({row["payload"].get("manifest_sha256") for row in rows})
        recipes = conn.execute(
            "SELECT manifest_sha256,raw_manifest FROM m_replay_recipes WHERE run_id=%s AND asset_id=%s AND manifest_sha256=ANY(%s)",
            (run, asset, hashes),
        ).fetchall()
        context = conn.execute(
            "SELECT sha256,raw_context FROM m_replay_contexts WHERE run_id=%s AND asset_id=%s",
            (run, asset),
        ).fetchone()
    if len(recipes) != len(hashes):
        raise HTTPException(409, "Recorded recipe artifact unavailable")
    packets = []
    contract = monitoring_contract()
    for row in rows:
        raw = bytes(row["raw_payload"])
        payload = json.loads(raw)
        if (
            digest(raw) != row["wire_sha256"]
            or payload != row["payload"]
            or payload["mode"] != "simulation"
            or payload["catalog_version"] != catalog["catalog_version"]
        ):
            raise HTTPException(409, "Recorded packet integrity/version mismatch")
        contract.validate(raw, observation_topic(run, asset, payload["source_id"]), run)
        packets.append(
            {
                "wire_sha256": row["wire_sha256"],
                "raw_base64": base64.b64encode(raw).decode(),
                "received_at": row["received_at"],
                "persisted_at": row["persisted_at"],
                "source_revision": row["dashboard_revision"],
                "seed": payload["sim_time_s"] < from_s,
            }
        )
    recipe_items = []
    for row in recipes:
        raw = bytes(row["raw_manifest"])
        if digest(raw) != row["manifest_sha256"]:
            raise HTTPException(409, "Recorded recipe hash mismatch")
        recipe_items.append(
            {"sha256": row["manifest_sha256"], "raw_base64": base64.b64encode(raw).decode()}
        )
    map_item = None
    if context:
        raw = bytes(context["raw_context"])
        if digest(raw) != context["sha256"]:
            raise HTTPException(409, "Recorded map context hash mismatch")
        map_item = {"sha256": context["sha256"], "raw_base64": base64.b64encode(raw).decode()}
    replay_id = str(uuid.uuid4())
    package = {
        "package_version": "1.0.0",
        "mode": "replay",
        "replay_run_id": replay_id,
        "namespace": "replay/" + replay_id,
        "source_run_id": run,
        "asset_id": asset,
        "source_mode": "simulation",
        "source_cursor": f"{run}:{asset}:{revision}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from_s": from_s,
        "to_s": to_s,
        "time_basis": "retrospective recorded simulation time",
        "policy_sha256": policy_row["policy_sha256"],
        "catalog": catalog,
        "recipes": recipe_items,
        "map_context": map_item,
        "packets": packets,
        "capabilities": {
            "dispatch": False,
            "publish_observations": False,
            "write_source_state": False,
        },
    }
    raw = encoded(package)
    if len(raw) > MAX_PACKAGE_BYTES:
        raise HTTPException(413, "Serialized replay package exceeds limit")
    return raw
