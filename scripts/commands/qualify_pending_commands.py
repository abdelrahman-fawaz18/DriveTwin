"""Verify pending command edits, mission intent admission, and persisted reads."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from qualify_command_authority import seal_competing_proposals
from qualify_connected_speed_limit import (
    ASSET,
    ISSUER,
    ROOT,
    digest,
    request,
    require,
    running,
    timestamp,
    wait_ready,
)

CASES = ("cancel", "replace", "cancel-race", "replace-race", "sealed-cancel", "mission")
COMMANDS = "/v1/commands"
CLAIM = "/internal/v1/claim"
MISSIONS = "/v1/mission-intents"


def address(compose, service="command-api"):
    return "http://" + compose("port", service, "8000").decode().strip()


def expect(base, tokens, path, status, role="operator", value=None):
    body = None if value is None else json.dumps(value).encode()
    code, response = request(base, path, tokens[role], body)
    require(code == status, f"{path} expected {status}, received {code}")
    return response


def speed_request(run, speed):
    now = datetime.now(timezone.utc)
    return {
        "schema_version": "evdt-command/1.0.0",
        "command_id": str(uuid.uuid4()),
        "run_id": run,
        "asset_id": ASSET,
        "issuer": ISSUER,
        "mode": "simulation",
        "capability": "set_speed_limit",
        "parameters": {"value": speed, "unit": "m/s"},
        "reason": "Pending command qualification",
        "issued_at": timestamp(now),
        "expires_at": timestamp(now + timedelta(seconds=30)),
        "not_before_sim_s": 4,
        "expires_sim_s": 6,
    }


def verify_mission(base, tokens, run, *, empty_queue):
    now = datetime.now(timezone.utc)
    intent = {
        "schema_version": "evdt-mission-intent/1.0.0",
        "intent_id": str(uuid.uuid4()),
        "run_id": run,
        "asset_id": ASSET,
        "issuer": ISSUER,
        "mode": "simulation",
        "station_id": "depot-a",
        "reason": "Operator mission qualification",
        "issued_at": timestamp(now),
        "expires_at": timestamp(now + timedelta(seconds=20)),
    }
    path = MISSIONS + "/" + intent["intent_id"]
    for role in (
        "reader",
        "replay",
        "executor",
        "observer",
        *(["thermal", "energy"] if "thermal" in tokens else []),
    ):
        expect(base, tokens, MISSIONS, 401, role, intent)
        expect(base, tokens, path, 401, role)
    for patch, status in (
        ({"run_id": str(uuid.uuid4())}, 403),
        ({"asset_id": "bus-002"}, 403),
        ({"station_id": "unlisted-stop"}, 422),
        ({"mode": "replay"}, 422),
        ({"issued_at": timestamp(now - timedelta(seconds=30)), "expires_at": timestamp(now)}, 422),
        ({"execute": True}, 422),
    ):
        expect(base, tokens, MISSIONS, status, value={**intent, **patch})
    expect(base, tokens, path, 404)
    accepted = expect(base, tokens, MISSIONS, 201, value=intent)
    require(
        accepted["state"] == "intent_recorded"
        and accepted["intent"] == intent
        and accepted["station_catalog_sha256"]
        == digest(ROOT / "simulation/montreal/stations.json"),
        "Intent was not bound to the reviewed station catalog",
    )
    require(
        expect(base, tokens, MISSIONS, 200, value=intent) == accepted
        and expect(base, tokens, path, 200) == accepted,
        "Intent retry changed its record",
    )
    expect(base, tokens, MISSIONS, 409, value={**intent, "station_id": "opportunity-b"})
    expect(base, tokens, COMMANDS, 409 if "thermal" in tokens else 422, value=intent)
    if empty_queue:
        expect(base, tokens, CLAIM, 204, "executor", running(4))
    return {path: accepted}


def verify_pending(compose, tokens, run, case):
    compose("up", "-d", "--pull", "never", "api", "command-api")
    base = address(compose)
    wait_ready(base)
    if case == "mission":
        return {"mission_case": True, "intent_and_read_verified": True}, verify_mission(
            base, tokens, run, empty_queue=True
        )
    if case == "sealed-cancel":
        decision, _, _, proof = seal_competing_proposals(base, tokens, run, "operator_wins")
        old_path = COMMANDS + "/" + decision["command_id"]
        expect(base, tokens, old_path + "/replace", 409, value=speed_request(run, 3))
        expect(base, tokens, old_path + "/cancel", 401, "thermal", {})
        canceled = expect(base, tokens, old_path + "/cancel", 200, value={})
        require(
            canceled["state"] == "canceled" and canceled["attempt_sim_s"] is None,
            "Sealed decision was not canceled before handoff",
        )
        expect(
            base, tokens, CLAIM, 204, "executor", {**running(4), "fence_protocol": "evdt-fence/1"}
        )
        expect(base, tokens, "/internal/v1/verify-fence", 409, "executor", proof)
        require(
            expect(base, tokens, "/v1/authority", 200) == decision,
            "Cancellation rewrote sealed decision",
        )
        reads = {old_path: canceled, "/v1/authority": decision}
        reads.update(verify_mission(base, tokens, run, empty_queue=False))
        return {"pending_case": case, "canceled": True, "decision_preserved": True}, reads

    first, successor = speed_request(run, 5), speed_request(run, 4)
    old_path, new_path = (COMMANDS + "/" + obj["command_id"] for obj in (first, successor))
    expect(base, tokens, COMMANDS, 201, value=first)
    expect(base, tokens, COMMANDS, 409, value=successor)
    replacing = case.startswith("replace")
    edit_path, edit = (
        (old_path + "/replace", successor) if replacing else (old_path + "/cancel", {})
    )
    for role in ("executor", "reader", "replay", "observer"):
        expect(base, tokens, edit_path, 401, role, edit)
    if case.endswith("race"):
        barrier = Barrier(2, timeout=5)

        def compete(path, role, payload):
            barrier.wait()
            return request(base, path, tokens[role], json.dumps(payload).encode())

        with ThreadPoolExecutor(max_workers=2) as pool:
            change = pool.submit(compete, edit_path, "operator", edit)
            handoff = pool.submit(compete, CLAIM, "executor", running(4))
            (edit_code, edited), (claim_code, claimed) = change.result(), handoff.result()
        if edit_code == 409:
            require(claim_code == 200 and claimed == first, "Handoff winner was not the original")
            expect(base, tokens, new_path, 404)
            winner = "handoff"
        else:
            require(
                edit_code == 200 and edited["state"] == ("superseded" if replacing else "canceled"),
                "Concurrent edit was not committed",
            )
            require(
                (claim_code, claimed) == ((200, successor) if replacing else (204, None)),
                "Concurrent claim handed out an obsolete command",
            )
            winner = "replacement" if replacing else "cancellation"
        expect(base, tokens, CLAIM, 204, "executor", running(4))
        return {"pending_case": case, "race_winner": winner, "second_handoff_refused": True}, {}

    if replacing:
        expect(base, tokens, edit_path, 403, value={**successor, "run_id": str(uuid.uuid4())})
        expect(base, tokens, edit_path, 409, value=first)
    else:
        expect(base, tokens, edit_path, 422, value={"reason": "unsupported field"})
    edited = expect(base, tokens, edit_path, 200, value=edit)
    require(
        edited["state"] == ("superseded" if replacing else "canceled")
        and edited["attempt_sim_s"] is None,
        "Pending edit did not close the original",
    )
    require(
        expect(base, tokens, edit_path, 200, value=edit) == edited
        and expect(base, tokens, COMMANDS, 200, value=first) == edited,
        "Exact retry changed the old command",
    )
    expect(base, tokens, COMMANDS, 409, value=speed_request(run, 3))
    reads = {old_path: edited}
    result = {"pending_case": case, "original_state": edited["state"], "idempotent": True}
    if replacing:
        expect(base, tokens, old_path + "/cancel", 409, value={})
        expect(base, tokens, edit_path, 409, value={**successor, "reason": "different payload"})
        require(
            edited["replacement_command_id"] == successor["command_id"], "Successor link was lost"
        )
        compose("run", "--rm", "--no-deps", "-T", "command-simulator", timeout=75)
        compose("run", "--rm", "--no-deps", "-T", "command-observer", successor["command_id"])
        assessed = expect(base, tokens, new_path, 200)
        require(
            assessed["result"]["status"] == "acknowledged"
            and assessed["effect"]["confirmed"]
            and assessed["effect"]["maximum_commanded_m_s"] == 4.0,
            "Replacement did not produce the measured cap",
        )
        expect(base, tokens, new_path + "/cancel", 409, value={})
        expect(base, tokens, new_path + "/replace", 409, value=speed_request(run, 3))
        reads[new_path] = assessed
        result["confirmed_replacement_cap_m_s"] = 4.0
    expect(base, tokens, CLAIM, 204, "executor", running(4))
    require(expect(base, tokens, old_path, 200) == edited, "Closed original command changed")
    result["second_handoff_refused"] = True
    return result, reads
