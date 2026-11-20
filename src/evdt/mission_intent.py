"""Record a scoped operator request for a known simulated station."""

import hashlib
import json
import re
from datetime import datetime

from evdt.commands import WIRE_PROFILE
from evdt.contract_profile import Rejected, decode, validator

INTENT = validator("commands/mission-intent-1.0.0.schema.json")


def provision(conn, target, policy, station_file):
    raw = station_file.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    registered = {
        source["expected"]["input_sha256"].get("stations.json")
        for source in policy["sources"]
        if source["asset_id"] == target.asset_id
    }
    if registered != {digest}:
        return  # Historical targets can still use speed commands.
    stations = [entry["station_id"] for entry in json.loads(raw)["stations"]]
    if (
        not stations
        or len(set(stations)) != len(stations)
        or any(not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", station) for station in stations)
    ):
        raise ValueError("Invalid registered mission station list")
    for station in stations:
        conn.execute(
            "INSERT INTO commanding.mission_stations VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (target.run_id, target.asset_id, station, digest),
        )


def read(raw, target):
    intent = decode(raw, WIRE_PROFILE)
    if isinstance(intent, dict) and intent.get("mode") == "replay":
        raise Rejected("replay_mode")
    if not INTENT.is_valid(intent):
        raise Rejected("mission_intent_schema")
    if (intent["run_id"], intent["asset_id"]) != (target.run_id, target.asset_id):
        raise Rejected("wrong_target")
    return intent


def check_validity(intent, now):
    issued = datetime.fromisoformat(intent["issued_at"])
    expires = datetime.fromisoformat(intent["expires_at"])
    if not (0 < (expires - issued).total_seconds() <= 30 and issued <= now < expires):
        raise Rejected("wall_validity")


def fetch(conn, target, identity):
    return conn.execute(
        """SELECT i.wire,i.recorded_at,s.catalog_sha256
           FROM commanding.mission_intents i JOIN commanding.mission_stations s
           USING (run_id,asset_id,station_id)
           WHERE (run_id,asset_id,intent_id)=(%s,%s,%s)""",
        (target.run_id, target.asset_id, identity),
    ).fetchone()


def view(row):
    return {
        "intent": json.loads(row["wire"]),
        "state": "intent_recorded",
        "recorded_at": row["recorded_at"],
        "station_catalog_sha256": row["catalog_sha256"],
    }


def submit(conn, target, raw, now):
    intent = read(raw, target)
    identity, station = intent["intent_id"], intent["station_id"]
    saved = fetch(conn, target, identity)
    if saved is not None:
        if bytes(saved["wire"]) != raw:
            raise Rejected("intent_conflict")
        return False, view(saved)
    check_validity(intent, now)
    stations = conn.execute(
        "SELECT station_id FROM commanding.mission_stations WHERE (run_id,asset_id)=(%s,%s)",
        (target.run_id, target.asset_id),
    ).fetchall()
    if not stations:
        raise Rejected("mission_unavailable")
    if station not in {row["station_id"] for row in stations}:
        raise Rejected("unknown_station")
    created = conn.execute(
        """INSERT INTO commanding.mission_intents(intent_id,run_id,asset_id,station_id,wire)
           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING intent_id""",
        (identity, target.run_id, target.asset_id, station, raw),
    ).fetchone()
    saved = fetch(conn, target, identity)
    if saved is None or bytes(saved["wire"]) != raw:
        raise Rejected("intent_conflict")
    return created is not None, view(saved)
