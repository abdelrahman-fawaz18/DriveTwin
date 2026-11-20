"""Opt-in charging extension of the authenticated simulation command service."""

import hashlib
import hmac
import json
import os
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from evdt import command_journal as journal
from evdt.charging_wire import (
    Claim,
    Departure,
    Effect,
    PolicyRequest,
    Scope,
    StationSnapshot,
    Submit,
)
from evdt.command_api import app, bounded_body, executor, observer, operator
from evdt.command_timing import ClockLease, host_clock
from evdt.config import ROOT, db_options
from evdt.contract_profile import Rejected, decode
from evdt.energy.charging_advisor import POLICY_VERSION, TARGET_KWH
from evdt.energy.charging_dispatch import (
    CHARGING_COMMAND_VERSION,
    DiversionContext,
    DiversionProposal,
    check_diversion,
)
from evdt.energy.station_arrival import RESERVE_KWH
from scripts.charging.run_charging_advisor import DESIGN, RELEASE, digest, inputs, load, replay

router = APIRouter(prefix="/v1/charging")
Body = Annotated[bytes, Depends(bounded_body)]
AUTO_PROFILE = ROOT / "qualification/charging/automatic-departure-1.0.0.json"
AUTO_PIN = "charging-automatic-profile"
API_EPOCH = str(uuid4())
MIGRATIONS = ("007_charging_diversions.sql", "008_charging_automatic.sql")


@app.middleware("http")
async def exclusive_charging_writes(request: Request, call_next):
    if request.method not in {"GET", "HEAD"} and not request.url.path.startswith("/v1/charging/"):
        return JSONResponse({"detail": "charging_profile_exclusive"}, status_code=409)
    return await call_next(request)


@lru_cache(maxsize=1)
def reviewed_inputs():
    return inputs()


def pins():
    return {
        "charging-release": digest(RELEASE),
        "charging-catalog": digest(ROOT / load(DESIGN)["catalog"]),
        "charging-network": reviewed_inputs()[-1]["network_sha256"],
    }


def automatic_profile():
    if os.environ.get("CHARGING_AUTO_QUALIFICATION") != "1":
        return None
    profile = load(AUTO_PROFILE)
    if profile != {
        "profile_version": "charging-automatic-departure/1.0.0",
        "scope": "isolated-simulation-qualification",
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "issuer": "charging-qualification",
        "maximum_diversions_per_run": 1,
        "maximum_departure_speed_m_s": 0,
        "maximum_simulation_validity_s": 1,
        "maximum_wall_validity_s": 3,
    }:
        raise ValueError("Unreviewed automatic charging profile")
    return digest(AUTO_PROFILE)


def assigned_profile(conn, scope):
    row = conn.execute(
        "SELECT profile_sha256 FROM commanding.charging_auto_targets WHERE run_id=%s AND asset_id=%s",
        scope,
    ).fetchone()
    return row["profile_sha256"] if row else None


def initialize():
    target = journal.scope()
    expected_pins = pins()
    profile_sha = automatic_profile()
    with psycopg.connect(**(db_options() | {"user": "evdt_admin"}), row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(37110403)")
        row = conn.execute(
            "SELECT policy FROM public.m_run_definitions WHERE run_id=%s", (target.run_id,)
        ).fetchone()
        sources = row["policy"]["sources"] if row and row["policy"]["mode"] == "simulation" else []
        if not any(
            source["asset_id"] == target.asset_id
            and source["source_id"] == "sumo-adapter"
            and all(
                source["expected"]["input_sha256"].get(name) == value
                for name, value in expected_pins.items()
            )
            for source in sources
        ):
            raise ValueError("Registered simulation policy must bind the reviewed charging inputs")
        if profile_sha is not None and (
            target.asset_id != "bus-001"
            or target.issuer != "charging-qualification"
            or len(sources) != 1
            or sources[0]["expected"]["input_sha256"].get(AUTO_PIN) != profile_sha
        ):
            raise ValueError("Automatic charging requires the exact isolated simulation profile")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS commanding.charging_migration (sha256 text PRIMARY KEY)"
        )
        applied = {
            row["sha256"]
            for row in conn.execute("SELECT sha256 FROM commanding.charging_migration")
        }
        paths = [ROOT / "migrations/commands" / name for name in MIGRATIONS]
        hashes = [digest(path) for path in paths]
        if applied != set(hashes[: len(applied)]):
            raise ValueError("Applied charging migration changed or is out of order")
        for path, sha in zip(paths[len(applied) :], hashes[len(applied) :], strict=True):
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO commanding.charging_migration VALUES (%s)", (sha,))
        conn.execute(
            """INSERT INTO commanding.charging_targets VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (target.run_id, target.asset_id, *expected_pins.values()),
        )
        assignment = conn.execute(
            """SELECT release_sha256,catalog_sha256,network_sha256
               FROM commanding.charging_targets WHERE run_id=%s AND asset_id=%s""",
            (target.run_id, target.asset_id),
        ).fetchone()
        if list(assignment.values()) != list(expected_pins.values()):
            raise ValueError("Charging assignment changed inside the same run")
        scope = (target.run_id, target.asset_id)
        if profile_sha is not None:
            # The launcher creates a fresh run. Do not upgrade an existing command in place.
            if (
                assigned_profile(conn, scope) is None
                and conn.execute(
                    "SELECT 1 FROM commanding.charging_diversions WHERE run_id=%s AND asset_id=%s",
                    scope,
                ).fetchone()
            ):
                raise ValueError("Automatic mode cannot take over an existing diversion")
            conn.execute(
                "INSERT INTO commanding.charging_auto_targets VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (*scope, profile_sha),
            )
        if assigned_profile(conn, scope) != profile_sha:
            raise ValueError("Automatic charging assignment changed inside the same run")


def problem(reason, code=409):
    raise HTTPException(code, reason)


def parse(raw, wire):
    try:
        value = wire.model_validate(decode(raw)).model_dump()
    except (ValueError, ValidationError):
        problem("charging_wire_invalid", 422)
    target = journal.scope()
    if (value["run_id"], value["asset_id"]) != (target.run_id, target.asset_id):
        problem("charging_scope_mismatch", 403)
    return value


def station_writer(authorization: Annotated[str | None, Header()] = None):
    token = Path("/run/secrets/command_station_writer").read_bytes().strip()
    if any(
        hmac.compare_digest(token, Path(f"/run/secrets/command_{role}").read_bytes().strip())
        for role in ("observer", "executor", "operator")
    ):
        problem("charging_station_credential_not_distinct", 503)
    if len(token) < 32 or not hmac.compare_digest(
        (authorization or "").encode(), b"Bearer " + token
    ):
        problem("credential_required", 401)


def registered(conn, scope, manifest):
    rows = conn.execute(
        "SELECT manifest_sha256 FROM commanding.charging_sources WHERE run_id=%s AND asset_id=%s",
        scope,
    ).fetchall()
    if rows != [{"manifest_sha256": manifest}]:
        problem("charging_source_unregistered")


def fresh(observed_at, clock):
    try:
        timestamp = datetime.fromisoformat(observed_at)
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
    except ValueError:
        problem("charging_observation_time_invalid", 422)
    if not -0.05 <= (clock.utc - timestamp).total_seconds() < 5:
        problem("charging_observation_stale_or_future")


def remember(raw, wire, kind):
    value = parse(raw, wire)
    scope = (value["run_id"], value["asset_id"])
    with journal.connection() as conn:
        conn.execute(journal.LOCK)
        if kind == "departure":
            registered(conn, scope, value["manifest_sha256"])
        else:
            catalog = reviewed_inputs()[2]
            identities = {row["station_id"] for row in catalog["stations"]}
            if (
                len(value["stations"]) != len(identities)
                or {row["station_id"] for row in value["stations"]} != identities
                or any(row["observed_sim_s"] > value["sim_s"] for row in value["stations"])
            ):
                problem("charging_station_snapshot_invalid", 422)
        previous = conn.execute(
            """SELECT payload FROM commanding.charging_observations
               WHERE run_id=%s AND asset_id=%s AND kind=%s AND observation_id=%s""",
            (*scope, kind, value["observation_id"]),
        ).fetchone()
        if previous:
            if previous["payload"] != value:
                problem("charging_observation_conflict")
        else:
            clock = host_clock()
            fresh(value["observed_at"], clock)
            head = conn.execute(
                """SELECT max(sim_s) AS sim_s FROM commanding.charging_observations
                   WHERE run_id=%s AND asset_id=%s AND kind=%s""",
                (*scope, kind),
            ).fetchone()["sim_s"]
            if head is not None and value["sim_s"] <= head:
                problem("charging_observation_out_of_order")
            conn.execute(
                """INSERT INTO commanding.charging_observations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    *scope,
                    kind,
                    value["observation_id"],
                    value["sim_s"],
                    Jsonb(value),
                    clock.utc,
                    clock.boot_id,
                    clock.elapsed_ns,
                ),
            )
    return {"observation_id": value["observation_id"]}


def planned(conn, scope, clock):
    snapshots = {}
    for kind in ("departure", "stations"):
        row = conn.execute(
            """SELECT payload,boot_id,elapsed_ns FROM commanding.charging_observations
               WHERE run_id=%s AND asset_id=%s AND kind=%s ORDER BY sim_s DESC LIMIT 1""",
            (*scope, kind),
        ).fetchone()
        if row is None or str(row["boot_id"]) != clock.boot_id:
            problem("charging_observation_unavailable")
        fresh(row["payload"]["observed_at"], clock)
        snapshots[kind] = row
    departure = snapshots["departure"]["payload"]
    stations = snapshots["stations"]["payload"]
    registered(conn, scope, departure["manifest_sha256"])
    if stations["sim_s"] > departure["sim_s"]:
        problem("charging_station_snapshot_future")
    net, mission, catalog, bus, release = reviewed_inputs()
    scenario = {
        "mission_id": mission["mission_id"],
        "decisions": [{**departure, "observed_sim_s": departure["sim_s"]}],
        "station_updates": [
            {**row, "available_sim_s": stations["sim_s"]} for row in stations["stations"]
        ],
    }
    record = replay(scenario, net, mission, catalog, bus, release)[0]
    evidence = {
        "record": record,
        "release_sha256": digest(RELEASE),
        "station_observation_id": stations["observation_id"],
        "departure_observation_id": departure["observation_id"],
    }
    fingerprint = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {**evidence, "decision_sha256": fingerprint}, snapshots["departure"]["elapsed_ns"]


def context_for(evidence, elapsed_ns, sim_s):
    record = evidence["record"]
    departure = record["departure"]
    station = record["recommended_station"]
    if station is None:
        problem(record["reason"])
    candidate = next(row for row in record["candidates"] if row["station_id"] == station)
    catalog = reviewed_inputs()[2]
    max_age = next(
        row["status_max_age_sim_s"] for row in catalog["stations"] if row["station_id"] == station
    )
    return DiversionContext(
        run_id=departure["run_id"],
        vehicle_id=departure["asset_id"],
        station_id=station,
        policy_version=POLICY_VERSION,
        release_sha256=evidence["release_sha256"],
        decision_sha256=evidence["decision_sha256"],
        departure_observation_id=departure["observation_id"],
        now_sim_s=sim_s,
        observation_sim_s=departure["sim_s"],
        observation_elapsed_ns=elapsed_ns,
        station_observed_sim_s=candidate["status"]["observed_sim_s"],
        station_available_sim_s=candidate["status"]["available_sim_s"],
        station_max_age_s=max_age,
        automatic_approved=False,
        operator_fixture_approved=True,
        operator_override=departure["operator_override"],
        thermal_block=departure["thermal_block"],
        local_protection_block=departure["local_protection_block"],
        station_eligible=candidate["eligible"],
    ), candidate


def verify(proposal, context, clock, *, automatic=False):
    try:
        check_diversion(proposal, context, clock, automatic=automatic)
    except Rejected as exc:
        problem(str(exc))


def policy_credential(authorization: Annotated[str | None, Header()] = None):
    if automatic_profile() is None:
        problem("automatic_dispatch_not_approved", 403)
    token = Path("/run/secrets/command_charging_policy").read_bytes().strip()
    if len(token) < 32 or any(
        hmac.compare_digest(token, Path(f"/run/secrets/command_{role}").read_bytes().strip())
        for role in ("observer", "executor", "operator", "station_writer")
    ):
        problem("charging_policy_credential_not_distinct", 503)
    if not hmac.compare_digest((authorization or "").encode(), b"Bearer " + token):
        problem("credential_required", 401)


def check_authority(conn, scope, *, automatic):
    profile = assigned_profile(conn, scope)
    if not automatic:
        if profile is not None:
            problem("charging_automatic_profile_exclusive")
        return None
    if profile is None or automatic_profile() != profile:
        problem("automatic_dispatch_not_approved", 403)
    if conn.execute(
        "SELECT 1 FROM commanding.charging_auto_overrides WHERE run_id=%s AND asset_id=%s", scope
    ).fetchone():
        problem("operator_override")
    return {"mode": "automatic", "issuer": "charging-qualification", "profile_sha256": profile}


def revalidate(conn, scope, value, payload):
    automatic = "authority" in payload
    authority = check_authority(conn, scope, automatic=automatic)
    if authority != payload.get("authority"):
        problem("charging_authority_changed")
    wire = payload["proposal"]
    proposal = DiversionProposal(**{**wire, "wall_lease": ClockLease.decode(wire["wall_lease"])})
    clock = host_clock()
    evidence, elapsed_ns = planned(conn, scope, clock)
    context, _ = context_for(evidence, elapsed_ns, value["sim_s"])
    verify(proposal, replace(context, automatic_approved=automatic), clock, automatic=automatic)
    return automatic


def command_row(conn, scope, command_id):
    row = conn.execute(
        """SELECT d.payload,c.reserved_at,e.payload AS effect
           FROM commanding.charging_diversions d
           LEFT JOIN commanding.charging_claims c USING (command_id)
           LEFT JOIN commanding.charging_effects e USING (command_id)
           WHERE d.run_id=%s AND d.asset_id=%s AND d.command_id=%s""",
        (*scope, command_id),
    ).fetchone()
    if row is None:
        problem("charging_command_not_found", 404)
    return row


@router.post("/departure", dependencies=[Depends(observer)])
def departure(raw: Body):
    return remember(raw, Departure, "departure")


@router.post("/stations", dependencies=[Depends(station_writer)])
def station_status(raw: Body):
    return remember(raw, StationSnapshot, "stations")


@router.get("/recommendation", dependencies=[Depends(operator)])
def recommendation():
    target = journal.scope()
    with journal.connection() as conn:
        return planned(conn, (target.run_id, target.asset_id), host_clock())[0]


@router.post("/diversions", dependencies=[Depends(operator)])
def submit(raw: Body):
    return queue(parse(raw, Submit), automatic=False)


@router.post("/automatic/diversions", dependencies=[Depends(policy_credential)])
def automatic_submit(raw: Body):
    return queue(parse(raw, PolicyRequest), automatic=True)


def queue(value, *, automatic):
    scope = (value["run_id"], value["asset_id"])
    with journal.connection() as conn:
        conn.execute(journal.LOCK)
        authority = check_authority(conn, scope, automatic=automatic)
        existing = conn.execute(
            "SELECT payload FROM commanding.charging_diversions WHERE run_id=%s AND asset_id=%s",
            scope,
        ).fetchone()
        if existing:
            if existing["payload"]["request"] != value:
                problem("charging_run_already_has_diversion")
            return existing["payload"]
        clock = host_clock()
        evidence, elapsed_ns = planned(conn, scope, clock)
        if not automatic and evidence["decision_sha256"] != value["decision_sha256"]:
            problem("charging_decision_changed")
        sim_s = evidence["record"]["decision_sim_s"]
        context, candidate = context_for(evidence, elapsed_ns, sim_s)
        proposal = DiversionProposal(
            version=CHARGING_COMMAND_VERSION,
            correlation_id=value["command_id"],
            run_id=context.run_id,
            vehicle_id=context.vehicle_id,
            station_id=context.station_id,
            policy_version=context.policy_version,
            release_sha256=context.release_sha256,
            decision_sha256=context.decision_sha256,
            departure_observation_id=context.departure_observation_id,
            valid_from_sim_s=sim_s,
            expires_sim_s=sim_s + 1,
            wall_lease=ClockLease.until(clock, clock.utc + timedelta(seconds=3)),
        )
        verify(proposal, replace(context, automatic_approved=automatic), clock, automatic=automatic)
        payload = {
            "request": value,
            "proposal": {**asdict(proposal), "wall_lease": proposal.wall_lease.encode()},
            "departure_manifest_sha256": evidence["record"]["departure"]["manifest_sha256"],
            "candidate": candidate,
        }
        if automatic:
            payload["authority"] = authority
        conn.execute(
            "INSERT INTO commanding.charging_diversions VALUES (%s,%s,%s,%s)",
            (*scope, value["command_id"], Jsonb(payload)),
        )
    return payload


@router.post("/claims", dependencies=[Depends(executor)])
def claim(raw: Body):
    value = parse(raw, Claim)
    scope = (value["run_id"], value["asset_id"])
    with journal.connection() as conn:
        conn.execute(journal.LOCK)
        row = command_row(conn, scope, value["command_id"])
        if row["reserved_at"] is not None:
            problem("charging_already_reserved")
        payload = row["payload"]
        automatic = revalidate(conn, scope, value, payload)
        conn.execute(
            "INSERT INTO commanding.charging_claims(command_id) VALUES (%s)", (value["command_id"],)
        )
        if automatic:
            conn.execute(
                "INSERT INTO commanding.charging_auto_reservations VALUES (%s,%s)",
                (value["command_id"], API_EPOCH),
            )
            # A reservation alone supplies no route or charger to the executor.
            return {"command_id": value["command_id"], "state": "reserved_unknown"}
    return payload


@router.post("/automatic/override", dependencies=[Depends(operator)])
def automatic_override(raw: Body):
    value = parse(raw, Scope)
    scope = (value["run_id"], value["asset_id"])
    with journal.connection() as conn:
        conn.execute(journal.LOCK)
        profile = assigned_profile(conn, scope)
        if profile is None:
            problem("automatic_dispatch_not_approved", 403)
        conn.execute(
            "INSERT INTO commanding.charging_auto_overrides(run_id,asset_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            scope,
        )
    return {"state": "override_latched"}


def has_grant(conn, command_id):
    return (
        conn.execute(
            "SELECT 1 FROM commanding.charging_auto_grants WHERE command_id=%s", (command_id,)
        ).fetchone()
        is not None
    )


@router.post("/automatic/actuation", dependencies=[Depends(executor)])
def automatic_actuation(raw: Body):
    value = parse(raw, Claim)
    scope = (value["run_id"], value["asset_id"])
    command_id = value["command_id"]
    with journal.connection() as conn:
        conn.execute(journal.LOCK)
        row = command_row(conn, scope, command_id)
        reservation = conn.execute(
            "SELECT api_epoch FROM commanding.charging_auto_reservations WHERE command_id=%s",
            (command_id,),
        ).fetchone()
        if not reservation:
            problem("charging_automatic_reservation_required")
        if has_grant(conn, command_id):
            problem("charging_actuation_already_granted")
        if str(reservation["api_epoch"]) != API_EPOCH:
            problem("charging_reservation_epoch_changed")
        if not revalidate(conn, scope, value, row["payload"]):
            problem("charging_automatic_reservation_required")
        conn.execute(
            "INSERT INTO commanding.charging_auto_grants(command_id) VALUES (%s)", (command_id,)
        )
        return row["payload"]


@router.post("/effects", dependencies=[Depends(observer)])
def reconcile(raw: Body):
    value = parse(raw, Effect)
    scope = (value["run_id"], value["asset_id"])
    with journal.connection() as conn:
        conn.execute(journal.LOCK)
        row = command_row(conn, scope, value["command_id"])
        if row["reserved_at"] is None:
            problem("charging_effect_without_claim")
        if "authority" in row["payload"] and not has_grant(conn, value["command_id"]):
            problem("charging_effect_without_automatic_grant")
        if row["effect"]:
            if row["effect"] != value:
                problem("charging_effect_conflict")
            return {"state": "effect_observed"}
        registered(conn, scope, value["manifest_sha256"])
        proposal = row["payload"]["proposal"]
        if not (
            value["manifest_sha256"] == row["payload"]["departure_manifest_sha256"]
            and value["station_id"] == proposal["station_id"]
            and proposal["valid_from_sim_s"]
            < value["arrived_sim_s"]
            < value["target_sim_s"]
            < value["resumed_sim_s"]
            < value["finished_sim_s"]
            and RESERVE_KWH
            <= value["minimum_kwh"]
            <= min(value["arrival_kwh"], value["finish_kwh"])
            and value["arrival_kwh"] < TARGET_KWH <= value["target_kwh"]
            and value["delivered_kwh"] >= value["target_kwh"] - value["arrival_kwh"] - 0.001
        ):
            problem("charging_effect_unconfirmed")
        conn.execute(
            "INSERT INTO commanding.charging_effects VALUES (%s,%s)",
            (value["command_id"], Jsonb(value)),
        )
    return {"state": "effect_observed"}


@router.get("/diversions/{command_id}", dependencies=[Depends(operator)])
def status(command_id: str):
    target = journal.scope()
    value = parse(
        json.dumps(
            {
                "command_id": command_id,
                "run_id": target.run_id,
                "asset_id": target.asset_id,
                "decision_sha256": "0" * 64,
            }
        ).encode(),
        Submit,
    )
    with journal.connection() as conn:
        row = command_row(conn, (target.run_id, target.asset_id), value["command_id"])
        if "authority" in row["payload"]:
            row["actuation_granted"] = has_grant(conn, value["command_id"])
    state = (
        "effect_observed"
        if row["effect"]
        else ("reserved_unknown" if row["reserved_at"] else "queued")
    )
    return {"state": state, **row}


app.include_router(router)


if __name__ == "__main__":
    initialize()
