"""Administrator-only deterministic product fixture; never an application route."""

import argparse
import copy
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evdt.monitoring.contracts import monitoring_contract, observation_topic
from evdt.monitoring.energy_views import canonical, provision, route_hash
from evdt.monitoring.registry import clock_event, register
from evdt.monitoring.repository import connection, store

FIXTURE = Path("/fixture")


def state_path(asset):
    return FIXTURE / ("fixture-state.json" if asset == "bus-001" else "fixture-state-2.json")


def load(asset="bus-001"):
    return json.loads(state_path(asset).read_bytes())


def clock(state, sim=None, asset="bus-001"):
    saved = load(asset)
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM m_source_clocks WHERE run_id=%s AND asset_id=%s AND source_id='sumo-adapter'",
            (os.environ["EVDT_RUN_ID"], asset),
        ).fetchone()
    obj = {
        **saved["manifest"]["identity"],
        "schema_version": "source-clock/1.0.0",
        "manifest_sha256": saved["manifest_sha256"],
        "event_id": str(uuid.uuid4()),
        "revision": row["revision"] + 1,
        "state": state,
        "sim_time_s": row["sim_time_s"] if sim is None else sim,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    clock_event(canonical(obj))


def setup(asset="bus-001"):
    manifest = json.loads((FIXTURE / "manifest.json").read_bytes())
    manifest["identity"].update(
        run_id=os.environ["EVDT_RUN_ID"],
        session_id=str(uuid.uuid4()),
        asset_id=asset,
        source_epoch=1,
    )
    manifest.update(asset_id=asset, minimum_sim_time_s=0, source_epoch_start_sim_s=0)
    raw = canonical(manifest)
    result = register(raw)
    state_path(asset).write_text(
        json.dumps({"manifest": manifest, "manifest_sha256": result["manifest_sha256"]})
    )
    with connection() as conn:
        if asset != "bus-001":
            from evdt.monitoring.map_context import provision as provision_map

            context = conn.execute(
                "SELECT raw_context FROM m_map_contexts WHERE run_id=%s AND asset_id='bus-001'",
                (os.environ["EVDT_RUN_ID"],),
            ).fetchone()["raw_context"]
            context = json.loads(bytes(context))
            context["asset_id"] = asset
            file = Path("/tmp/second-energy-context.json")
            file.write_bytes(canonical(context))
            provision_map(conn, file)
        context = conn.execute(
            "SELECT raw_context FROM m_map_contexts WHERE run_id=%s AND asset_id=%s",
            (os.environ["EVDT_RUN_ID"], asset),
        ).fetchone()
        rsha = route_hash(json.loads(bytes(context["raw_context"])))
        provision(
            conn, asset, "sumo-adapter", (FIXTURE / "release-sha.txt").read_text().strip(), rsha
        )
    return {"registered": True, "route_sha256": rsha}


def seed(asset="bus-001", missing=False):
    state = load(asset)
    clock("running", 120, asset)
    packets = [json.loads(line) for line in (FIXTURE / "prefix.jsonl").read_text().splitlines()]
    contract = monitoring_contract()
    admitted = 0
    for packet in packets:
        packet = copy.deepcopy(packet)
        packet.update(
            state["manifest"]["identity"],
            manifest_sha256=state["manifest_sha256"],
            event_id=str(uuid.uuid4()),
            observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        if missing and packet["sim_time_s"] == 120:
            for sample in packet["samples"]:
                if sample["signal_id"] == "battery.pack.soc":
                    sample.update(value=None, quality="missing", reason="dropout")
        result = store(
            canonical(packet),
            observation_topic(packet["run_id"], packet["asset_id"], packet["source_id"]),
            packet["run_id"],
            contract,
        )
        if result["outcome"] != "accepted":
            raise RuntimeError("Controlled energy packet not accepted: " + str(result))
        admitted += 1
    clock("running", 120, asset)
    return {
        "admitted": admitted,
        "uses_retained_training_values": True,
        "held_out_regenerated": False,
        "missing_required_input": missing,
    }


def denial():
    outcomes = []
    for role, file, statement in [
        ("evdt_read", "read_db", "INSERT INTO m_energy_forecasts DEFAULT VALUES"),
        ("evdt_forecast", "forecast_db", "SELECT * FROM m_observations LIMIT 1"),
        ("evdt_forecast", "forecast_db", "SELECT * FROM m_run_definitions LIMIT 1"),
        ("evdt_forecast", "forecast_db", "SELECT * FROM m_quarantine LIMIT 1"),
        ("evdt_forecast", "forecast_db", "UPDATE m_observations SET asset_id=asset_id"),
        ("evdt_forecast", "forecast_db", "UPDATE m_energy_forecasts SET record_id=record_id"),
        ("evdt_forecast", "forecast_db", "DELETE FROM m_energy_forecasts"),
        ("evdt_forecast", "forecast_db", "UPDATE m_energy_assignments SET source_id=source_id"),
        ("evdt_replay", "replay_db", "SELECT * FROM m_energy_forecasts"),
    ]:
        try:
            with psycopg.connect(
                host="db",
                dbname="evdt",
                user=role,
                password=Path("/run/secrets", file).read_text().strip(),
            ) as conn:
                conn.execute(statement)
        except psycopg.errors.InsufficientPrivilege:
            outcomes.append({"role": role, "denied": True})
        else:
            raise RuntimeError("Database access restriction failed")
    with connection() as conn:
        try:
            with conn.transaction():
                conn.execute("UPDATE m_energy_forecasts SET payload=payload")
        except psycopg.errors.RaiseException:
            outcomes.append({"owner_immutability": True})
        else:
            raise RuntimeError("Owner changed immutable forecast")
    return {"passed": True, "checks": len(outcomes)}


def audit():
    with connection() as conn:
        records = conn.execute(
            "SELECT payload FROM m_energy_forecasts ORDER BY sequence"
        ).fetchall()
        originals = conn.execute(
            "SELECT wire_sha256,raw_payload FROM m_observations ORDER BY event_id"
        ).fetchall()
        for r in originals:
            if hashlib.sha256(bytes(r["raw_payload"])).hexdigest() != r["wire_sha256"]:
                raise RuntimeError("Original packet bytes changed")
    return {
        "records": len(records),
        "available": sum(r["payload"]["status"] == "advisory" for r in records),
        "unavailable": sum(r["payload"]["status"] == "unavailable" for r in records),
        "original_packets_intact": len(originals),
    }


def parity():
    from dataclasses import asdict

    from evdt.energy.jobs import execute_job
    from evdt.energy.observations import fingerprint
    from evdt.energy.product import prepare

    folder = FIXTURE / "validation"
    model = json.loads((folder / "model.json").read_bytes())
    records = json.loads((folder / "predictions.json").read_bytes())
    checked = 0
    for record in records:
        old_plan = json.loads((folder / (record["mission_id"] + "-planned-job.json")).read_bytes())
        old_reference = json.loads(
            (folder / (record["mission_id"] + "-baseline-job.json")).read_bytes()
        )
        packets = copy.deepcopy(old_reference["observations"])
        packets[-1]["samples"].append(
            {
                "signal_id": "auxiliary.power",
                "value": old_plan["plan"][0]["auxiliary_power_w"],
                "unit": "W",
                "quality": "good",
                "provenance": "simulated-observation",
            }
        )
        value = {
            "version": "trusted-energy-input/1.0.0",
            "mode": "simulation",
            "packets": packets,
            "reason_codes": [],
            "assignment": {
                "release_sha256": "a" * 64,
                "declared_capacity_kwh": 350,
                "reserve_soc": 0.1,
                "cutoff_sim_s": 120,
            },
        }
        value["input_id"] = fingerprint(value)
        prepared = prepare(value, model, "a" * 64)
        if prepared["uncertainty"] != record["envelope"]:
            raise RuntimeError("Frozen validation uncertainty parity failed")
        for method, old in (("candidate", old_plan), ("reference", old_reference)):
            if asdict(execute_job(prepared["jobs"][method]).values) != asdict(
                execute_job(old).values
            ):
                raise RuntimeError("Frozen validation forecast value parity failed")
        checked += 1
    if checked != 6:
        raise RuntimeError("Six validation cases required")
    return {
        "passed": True,
        "validation_cases": checked,
        "candidate_reference_values_and_envelopes_identical": True,
        "held_out_opened": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "setup",
            "seed",
            "pause",
            "resume",
            "advance",
            "denial",
            "audit",
            "parity",
            "setup-second",
            "seed-missing",
            "advance-second",
        ],
    )
    args = parser.parse_args()
    if args.action == "setup-second":
        result = setup("bus-002")
    elif args.action == "seed-missing":
        result = seed("bus-002", missing=True)
    elif args.action == "advance-second":
        clock("running", 121, "bus-002")
        result = {"state": "advanced-second"}
    elif args.action == "parity":
        result = parity()
    elif args.action == "setup":
        result = setup()
    elif args.action == "seed":
        result = seed()
    elif args.action == "denial":
        result = denial()
    elif args.action == "audit":
        result = audit()
    else:
        clock(
            "paused" if args.action == "pause" else "running",
            121 if args.action == "advance" else None,
        )
        result = {"state": args.action}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
