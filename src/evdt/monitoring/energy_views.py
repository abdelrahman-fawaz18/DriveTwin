"""Trusted, bounded observation admission and read-only forecast presentation."""

import json
import os
from datetime import datetime

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg import sql
from psycopg.types.json import Jsonb

from evdt.config import ROOT
from evdt.energy.observations import fingerprint, observation_point
from evdt.monitoring.database import connection

SIGNALS = (
    "battery.pack.energy",
    "motion.distance",
    "battery.pack.soc",
    "motion.speed",
    "auxiliary.power",
    "location.latitude",
    "location.longitude",
    "motion.heading",
)
INPUT_VERSION = "trusted-energy-input/1.0.0"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def route_hash(context):
    routes = [f for f in context["features"] if f["properties"].get("layer") == "route"]
    if len(routes) != 1:
        raise ValueError("One assigned route is required")
    return fingerprint({"id": routes[0]["properties"]["id"], "geometry": routes[0]["geometry"]})


def provision(conn, asset, source, release_sha, route_sha):
    """Administrator operation; never available as a browser write endpoint."""
    run = os.environ["EVDT_RUN_ID"]
    profile = json.loads((ROOT / "qualification/energy/product-profile-1.1.0.json").read_bytes())
    navigation_raw = (ROOT / profile["navigation_file"]).read_bytes()
    import hashlib

    if hashlib.sha256(navigation_raw).hexdigest() != profile["navigation_file_sha256"]:
        raise ValueError("Navigation profile file changed")
    if release_sha != profile["release_sha256"] or route_sha != profile["route_sha256"]:
        raise ValueError("Release/route is outside the reviewed product profile")
    if any(
        not isinstance(h, str) or len(h) != 64 or any(c not in "0123456789abcdef" for c in h)
        for h in (release_sha, route_sha)
    ):
        raise ValueError("Reviewed release and route fingerprints required")
    row = conn.execute(
        "SELECT raw_context FROM m_map_contexts WHERE run_id=%s AND asset_id=%s", (run, asset)
    ).fetchone()
    if not row or route_hash(json.loads(bytes(row["raw_context"]))) != route_sha:
        raise ValueError("Assigned route does not match the reviewed route")
    value = {
        "version": "energy-assignment/1.0.0",
        "source_id": source,
        "route_sha256": route_sha,
        "release_sha256": release_sha,
        "profile_version": profile["version"],
        "mission_id": profile["mission_id"],
        "profile_sha256": fingerprint(profile),
        "declared_capacity_kwh": profile["declared_capacity_kwh"],
        "reserve_soc": profile["reserve_soc"],
        "external_charging_excluded": True,
        "charging_basis": "Operator-approved simulated source without external charging",
        "cutoff_sim_s": profile["cutoff_sim_s"],
        "history_s": profile["history_s"],
        "max_age_s": profile["max_age_s"],
        "navigation_file_sha256": profile["navigation_file_sha256"],
        "navigation_content_sha256": profile["navigation_content_sha256"],
    }
    conn.execute(
        "INSERT INTO m_energy_assignments(run_id,asset_id,source_id,assignment_sha256,assignment) VALUES(%s,%s,%s,%s,%s)",
        (run, asset, source, fingerprint(value), Jsonb(value)),
    )


def collect(conn, run, asset):
    """Read one consistent server-owned view, never caller-supplied live state."""
    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    assignment = conn.execute(
        "SELECT * FROM m_energy_assignments WHERE run_id=%s AND asset_id=%s", (run, asset)
    ).fetchone()
    result = {
        "version": INPUT_VERSION,
        "mode": "simulation",
        "run_id": run,
        "asset_id": asset,
        "as_of": now.isoformat(),
        "assignment": assignment["assignment"] if assignment else None,
        "assignment_sha256": assignment["assignment_sha256"] if assignment else None,
        "source_id": assignment["source_id"] if assignment else None,
        "clock": None,
        "packets": [],
        "wire_references": [],
        "reason_codes": [],
    }
    if not assignment:
        result["reason_codes"] = ["forecast_not_configured"]
    else:
        source = assignment["source_id"]
        row = conn.execute(
            "SELECT c.*,r.session_id,h.source_epoch AS head_epoch FROM m_source_clocks c JOIN m_source_heads h USING(run_id,asset_id,source_id) JOIN m_registrations r ON (r.run_id,r.asset_id,r.source_id,r.source_epoch)=(c.run_id,c.asset_id,c.source_id,c.source_epoch) WHERE c.run_id=%s AND c.asset_id=%s AND c.source_id=%s",
            (run, asset, source),
        ).fetchone()
        result["clock"] = jsonable_encoder(row) if row else None
        policy = conn.execute(
            "SELECT policy FROM m_run_definitions WHERE run_id=%s", (run,)
        ).fetchone()["policy"]
        owners = [
            s for s in policy["sources"] if s["asset_id"] == asset and s["source_id"] == source
        ]
        result["heartbeat_s"] = (
            owners[0]["expected"]["heartbeat_deadline_s"] if len(owners) == 1 else 0
        )
        context = conn.execute(
            "SELECT raw_context FROM m_map_contexts WHERE run_id=%s AND asset_id=%s", (run, asset)
        ).fetchone()
        result["route_sha256"] = (
            route_hash(json.loads(bytes(context["raw_context"]))) if context else None
        )
        if row:
            rows = conn.execute(
                "SELECT payload,event_id,wire_sha256,observed_at,received_at FROM m_observations WHERE run_id=%s AND asset_id=%s AND source_id=%s AND source_epoch=%s AND session_id=%s AND sim_time_s BETWEEN %s AND %s ORDER BY sim_time_s,event_id LIMIT 1001",
                (
                    run,
                    asset,
                    source,
                    row["source_epoch"],
                    row["session_id"],
                    max(0, row["sim_time_s"] - 60),
                    row["sim_time_s"],
                ),
            ).fetchall()
            for observation in rows:
                p = observation["payload"]
                packet = {
                    k: p[k]
                    for k in (
                        "schema_version",
                        "mode",
                        "run_id",
                        "asset_id",
                        "source_id",
                        "source_epoch",
                        "session_id",
                        "clock_domain",
                        "event_id",
                        "stream_seq",
                        "sim_time_s",
                    )
                }
                packet["samples"] = [
                    {
                        k: s[k]
                        for k in ("signal_id", "value", "unit", "quality", "provenance")
                        if k in s
                    }
                    for s in p["samples"]
                    if s["signal_id"] in SIGNALS
                ]
                result["packets"].append(packet)
                result["wire_references"].append(
                    {
                        "event_id": str(observation["event_id"]),
                        "wire_sha256": observation["wire_sha256"],
                        "observed_at": observation["observed_at"].isoformat(),
                        "received_at": observation["received_at"].isoformat(),
                    }
                )
        result["reason_codes"] = admission_reasons(result, now)
    # Wall-clock read time is presentation metadata, not input identity.
    result["input_id"] = fingerprint({k: v for k, v in result.items() if k != "as_of"})
    return result


def admission_reasons(value, now):
    a, clock, packets = value["assignment"], value["clock"], value["packets"]
    if not a:
        return ["forecast_not_configured"]
    if fingerprint(a) != value["assignment_sha256"]:
        return ["assignment_integrity_failure"]
    if value["mode"] != "simulation" or value.get("route_sha256") != a["route_sha256"]:
        return ["route_or_mode_outside_domain"]
    if not a["external_charging_excluded"]:
        return ["external_charging_not_excluded"]
    if not clock or clock["source_epoch"] != clock["head_epoch"]:
        return ["source_identity_not_ready"]
    if clock["state"] != "running":
        return ["source_" + clock["state"]]
    age = (now - datetime.fromisoformat(clock["observed_at"])).total_seconds()
    if not 0 <= age <= value["heartbeat_s"]:
        return ["source_disconnected"]
    if abs(clock["sim_time_s"] - a["cutoff_sim_s"]) > 1e-8:
        return ["prediction_cutoff_outside_evaluated_domain"]
    if not 2 <= len(packets) <= 1000:
        return ["insufficient_or_excessive_history"]
    last = packets[-1]
    if last["sim_time_s"] != clock["sim_time_s"]:
        return ["latest_observation_missing"]
    if any(
        p["mode"] != "simulation"
        or p["clock_domain"] != "simulation"
        or any(p[k] != value[k] for k in ("run_id", "asset_id", "source_id"))
        or p["source_epoch"] != clock["source_epoch"]
        or p["session_id"] != clock["session_id"]
        for p in packets
    ):
        return ["observation_scope_mismatch"]
    if len(value["wire_references"]) != len(packets):
        return ["observation_lineage_missing"]
    for p, ref in zip(packets, value["wire_references"], strict=True):
        age = (now - datetime.fromisoformat(ref["observed_at"])).total_seconds()
        if p["event_id"] != ref["event_id"] or age < 0 or (p is last and age > a["max_age_s"]):
            return ["stale_or_future_observation"]
        try:
            observation_point(p)
        except ValueError:
            return ["required_observation_invalid"]
    auxiliary = [s for s in last["samples"] if s["signal_id"] == "auxiliary.power"]
    if (
        len(auxiliary) != 1
        or auxiliary[0].get("quality") != "good"
        or auxiliary[0].get("unit") != "W"
        or auxiliary[0].get("provenance") != "simulated-observation"
    ):
        return ["auxiliary_observation_invalid"]
    return []


def input_view(asset):
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        return collect(conn, os.environ["EVDT_RUN_ID"], asset)


def save(conn, record):
    """Only the dedicated trusted writer calls this; conflict is never success."""
    body = {k: v for k, v in record.items() if k != "record_id"}
    if (
        record["version"] != "persistent-energy-forecast/1.0.0"
        or fingerprint(body) != record["record_id"]
        or len(canonical(record)) > 262144
    ):
        raise ValueError("Invalid forecast record identity/size")
    if record["run_id"] != os.environ["EVDT_RUN_ID"]:
        raise ValueError("Forecast run mismatch")
    assignment = conn.execute(
        "SELECT assignment_sha256,source_id FROM m_energy_assignments WHERE run_id=%s AND asset_id=%s",
        (record["run_id"], record["asset_id"]),
    ).fetchone()
    if (
        not assignment
        or assignment["assignment_sha256"] != record["assignment_sha256"]
        or assignment["source_id"] != record["source_id"]
    ):
        raise ValueError("Forecast assignment mismatch")
    for ref in record["observation_references"]:
        row = conn.execute(
            "SELECT * FROM m_energy_observation_refs WHERE run_id=%s AND asset_id=%s AND source_id=%s AND event_id=%s",
            (record["run_id"], record["asset_id"], record["source_id"], ref["event_id"]),
        ).fetchone()
        if not row or row["wire_sha256"] != ref["wire_sha256"]:
            raise ValueError("Forecast observation lineage mismatch")
    conn.execute(
        "INSERT INTO m_energy_forecasts(record_id,run_id,asset_id,source_id,input_id,payload) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (
            record["record_id"],
            record["run_id"],
            record["asset_id"],
            record["source_id"],
            record["input_id"],
            Jsonb(record),
        ),
    )
    saved = conn.execute(
        "SELECT payload FROM m_energy_forecasts WHERE run_id=%s AND asset_id=%s AND input_id=%s",
        (record["run_id"], record["asset_id"], record["input_id"]),
    ).fetchone()
    if not saved or saved["payload"] != record:
        raise ValueError("Conflicting immutable forecast")


def read(asset, before=None, limit=20, record_id=None):
    run = os.environ["EVDT_RUN_ID"]
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='3000ms'")
        current = collect(conn, run, asset)
        if record_id:
            row = conn.execute(
                "SELECT sequence,created_at,payload FROM m_energy_forecasts WHERE run_id=%s AND asset_id=%s AND record_id=%s",
                (run, asset, record_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Forecast not found in this asset")
            return {
                "version": "energy-record-detail/1.0.0",
                "run_id": run,
                "asset_id": asset,
                "historical": True,
                "item": row,
            }
        rows = conn.execute(
            "SELECT sequence,created_at,payload FROM m_energy_forecasts WHERE run_id=%s AND asset_id=%s AND sequence<%s ORDER BY sequence DESC LIMIT %s",
            (run, asset, before or 9007199254740991, limit + 1),
        ).fetchall()
    reasons = current["reason_codes"]
    latest = rows[0]["payload"] if rows else None
    if before is not None:
        reasons = ["historical_page"]
    elif not reasons and (
        not latest
        or latest["assignment_sha256"] != current["assignment_sha256"]
        or latest["observation_references"] != current["wire_references"]
    ):
        reasons = ["forecast_not_computed_for_current_input"]
    elif not reasons and latest["status"] != "advisory":
        reasons = latest["reason_codes"]
    return {
        "version": "energy-forecast-view/1.0.0",
        "run_id": run,
        "asset_id": asset,
        "as_of": current["as_of"],
        "status": "unavailable" if reasons else "advisory",
        "reason_codes": reasons,
        "values": None if reasons else latest["values"],
        "uncertainty": None if reasons else latest["uncertainty"],
        "current_record_id": latest["record_id"] if latest and not reasons else None,
        "input_age_s": max(
            0,
            (
                datetime.fromisoformat(current["as_of"])
                - datetime.fromisoformat(current["wire_references"][-1]["observed_at"])
            ).total_seconds(),
        )
        if current["wire_references"]
        else None,
        "extensions": latest.get("extensions") if latest and not reasons else None,
        "reference_values": latest["worker_outputs"]["reference"]["forecast"]["values"]
        if latest and not reasons
        else None,
        "items": rows[:limit],
        "next": rows[limit - 1]["sequence"] if len(rows) > limit else None,
        "model_qualified": False,
        "automatic_dispatch_approved": False,
    }


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["provision", "save", "enable-writer"])
    parser.add_argument("--asset")
    parser.add_argument("--source", default="sumo-adapter")
    parser.add_argument("--release-sha")
    parser.add_argument("--route-sha")
    args = parser.parse_args()
    with connection() as conn:
        if args.action == "save":
            raw = sys.stdin.buffer.read(262145)
            if len(raw) > 262144:
                raise ValueError("Forecast record too large")
            save(conn, json.loads(raw))
        else:
            if args.action == "provision":
                provision(conn, args.asset, args.source, args.release_sha, args.route_sha)
            password = os.environ.get("FORECAST_DB_PASSWORD_FILE")
            if password:
                from pathlib import Path

                conn.execute(
                    sql.SQL("ALTER ROLE evdt_forecast LOGIN PASSWORD {}").format(
                        sql.Literal(Path(password).read_text().strip())
                    )
                )
    print(json.dumps({"saved": True}))


if __name__ == "__main__":
    main()
