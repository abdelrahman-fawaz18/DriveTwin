"""Exercise three competing requesters and one sealed actuator decision."""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from qualify_connected_speed_limit import (
    ASSET,
    read,
    request,
    require,
    restart_commands,
    running,
    timestamp,
    wait_ready,
)

CASES = ("operator_wins", "thermal_fallback", "stale_fence", "local_protection")
ISSUERS = {"operator": "local-operator", "thermal": "thermal-service", "energy": "energy-service"}
LIMITS = {"operator": 4, "thermal": 3, "energy": 5}
SEAL = "/v1/authority/seal"
CLAIM = "/internal/v1/claim"
VERIFY = "/internal/v1/verify-fence"


def seal_competing_proposals(commands, tokens, run, case):
    def call(path, status, role, value=None):
        body = json.dumps(value).encode() if value is not None else None
        actual, response = request(commands, path, tokens[role], body)
        require(actual == status, f"{path} expected {status}, received {actual}")
        return response

    fenced_claim = {**running(4), "fence_protocol": "evdt-fence/1"}
    call(CLAIM, 204, "executor", fenced_claim)
    call(SEAL, 409, "operator", {})
    call(SEAL, 401, "thermal", {})
    call(SEAL, 401, "energy", {})
    call("/v1/authority", 401, "reader")
    call("/v1/commands", 409, "operator", {})

    print("Submitting three competing speed-cap proposals", flush=True)
    proposed = {}
    for role in ("energy", "thermal", "operator"):
        now = datetime.now(timezone.utc)
        expiry = 3 if role == "operator" and case == "thermal_fallback" else 30
        payload = {
            "schema_version": "evdt-command/1.0.0",
            "command_id": str(uuid.uuid4()),
            "run_id": run,
            "asset_id": ASSET,
            "issuer": ISSUERS[role],
            "mode": "simulation",
            "capability": "set_speed_limit",
            "parameters": {"value": LIMITS[role], "unit": "m/s"},
            "reason": "Local authority qualification",
            "issued_at": timestamp(now),
            "expires_at": timestamp(now + timedelta(seconds=expiry)),
            "not_before_sim_s": 4,
            "expires_sim_s": 6,
        }
        path = "/v1/proposals/" + role
        if role == "energy":
            call("/v1/proposals/operator", 401, role, payload)
            call(path, 403, role, {**payload, "issuer": ISSUERS["operator"]})
            call(path, 422, role, {**payload, "priority": 100})
        assert call(path, 201, role, payload) == {"command_id": payload["command_id"]}
        assert call(path, 200, role, payload) == {"command_id": payload["command_id"]}
        proposed[role] = payload
    call(CLAIM, 204, "executor", fenced_claim)
    if case == "thermal_fallback":
        time.sleep(3.1)

    print("Racing two seal requests and checking the single authority decision", flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda _: request(commands, SEAL, tokens["operator"], b"{}"), range(2))
        )
    require(
        sorted(code for code, _ in replies) == [200, 201],
        "Seal was not single-writer and idempotent",
    )
    decision = replies[0][1]
    require(decision == replies[1][1], "Concurrent seals returned different decisions")
    selected = "thermal" if case == "thermal_fallback" else "operator"
    identity = proposed[selected]["command_id"]
    require(
        decision["selected_proposer"] == selected and decision["command_id"] == identity,
        "Wrong command selected",
    )
    require(
        [item["proposer"] for item in decision["assessments"]] == list(ISSUERS),
        "Precedence order was lost",
    )
    expected_reasons = {
        role: ("selected" if role == selected else "lower_precedence") for role in ISSUERS
    }
    if case == "thermal_fallback":
        expected_reasons["operator"] = "elapsed_deadline"
    require(
        {item["proposer"]: item["reason"] for item in decision["assessments"]} == expected_reasons,
        "Unexpected rejection or selection reason",
    )
    call(
        "/v1/proposals/energy",
        409,
        "energy",
        {**proposed["energy"], "reason": "Replace sealed proposal"},
    )
    call(CLAIM, 204, "executor", running(4))
    proof = {
        "command_id": identity,
        "run_id": run,
        "asset_id": ASSET,
        "generation": decision["generation"],
    }
    call(VERIFY, 409, "executor", proof)
    call(VERIFY, 401, "operator", proof)
    call(VERIFY, 403, "executor", {**proof, "run_id": str(uuid.uuid4())})
    return decision, proposed, expected_reasons, proof


def verify_authority(compose, tokens, run, output, case):
    compose("up", "-d", "--pull", "never", "api", "command-api")

    def address(service):
        return "http://" + compose("port", service, "8000").decode().strip()

    commands = address("command-api")
    wait_ready(commands)

    def call(path, status, role, value=None):
        body = json.dumps(value).encode() if value is not None else None
        actual, response = request(commands, path, tokens[role], body)
        require(actual == status, f"{path} expected {status}, received {actual}")
        return response

    decision, proposed, expected_reasons, proof = seal_competing_proposals(
        commands, tokens, run, case
    )
    selected = decision["selected_proposer"]
    identity = decision["command_id"]
    fenced_claim = {**running(4), "fence_protocol": "evdt-fence/1"}

    print(
        "Running the selected command through the simulator fence and local protection", flush=True
    )
    denied = case in ("stale_fence", "local_protection")
    extra = (
        ["python", "scripts/commands/run_connected_speed_limit.py", "--authority-test", case]
        if denied
        else []
    )
    compose("run", "--rm", "--no-deps", "-T", "command-simulator", *extra, timeout=75)
    drive = read(output / "drive/qualification.json")
    delivered = read(output / "drive/commanded/command.json")
    require(
        drive["passed"] and drive["received_generation"] == decision["generation"], "Fence was lost"
    )
    require(
        delivered == {**proposed[selected], "issuer": "dispatch-coordinator"},
        "Dispatch changed the winning proposal or impersonated a proposer",
    )
    path = "/v1/commands/" + identity
    if not denied:
        compose("run", "--rm", "--no-deps", "-T", "command-observer", identity)
    state = call(path, 200, "operator")
    if denied:
        reason = "authority_fence_refused" if case == "stale_fence" else "not_a_speed_reduction"
        require(
            state["state"] == "rejected_before_actuation"
            and state["refusal_reason"] == reason
            and state["result"] is None
            and state["effect"] is None
            and drive["identical_speed_traces"],
            "Fence or local protection allowed actuation",
        )
        if case == "stale_fence":
            require(
                drive["stale_generation_tested"] < decision["generation"],
                "Stale generation was not used",
            )
        else:
            require(drive["maximum_commanded_m_s"] <= 3.05, "Local cap was raised")
    else:
        require(
            state["state"] == "acknowledged"
            and state["effect"] == drive["effect"]
            and state["effect"]["confirmed"],
            "Selected command had no independent effect",
        )

    commands = restart_commands(compose, address)
    require(
        call(path, 200, "operator") == state and call("/v1/authority", 200, "operator") == decision,
        "Authority or result changed on restart",
    )
    require(call(SEAL, 200, "operator", {}) == decision, "Reseal changed the decision")
    call(CLAIM, 204, "executor", fenced_claim)
    call(VERIFY, 409, "executor", proof)
    for role, payload in proposed.items():
        if role != selected:
            call("/v1/commands/" + payload["command_id"], 404, "operator")

    db = check_authority_journal(compose, denied)
    return {
        "authority_case": case,
        "selected_proposer": selected,
        "selected_cap_m_s": LIMITS[selected],
        "selection_reasons": expected_reasons,
        "generation": decision["generation"],
        "stale_generation_tested": drive.get("stale_generation_tested"),
        "concurrent_seal_returned_same_decision": True,
        "restart_preserved_authority": True,
        "journal_counts": db["counts"],
        "audit_reasons": sorted(db["reasons"]),
        "final_execution_state": state["state"],
        "refusal_reason": state["refusal_reason"],
        "measured_effect": state["effect"],
        "unchanged_speed_trace": drive.get("identical_speed_traces"),
        "maximum_commanded_m_s": drive.get("maximum_commanded_m_s"),
    }


def check_authority_journal(compose, denied):
    probe = """
import json
from evdt.command_journal import connection
from psycopg.errors import InsufficientPrivilege
with connection() as conn:
    counts = {t:conn.execute(f'SELECT count(*) AS n FROM commanding.{t}').fetchone()['n']
              for t in ('proposals','decisions','requests','deadlines','attempts','results','effects')}
    reasons = [r['reason'] for r in conn.execute('SELECT DISTINCT reason FROM commanding.rejections')]
    for statement in ('UPDATE commanding.decisions SET selected_proposer=selected_proposer',
                      'DELETE FROM commanding.proposals', 'DELETE FROM commanding.arbitration_targets'):
        try:
            with conn.transaction():
                conn.execute(statement)
        except InsufficientPrivilege:
            continue
        raise RuntimeError('Command role can overwrite authority')
print(json.dumps({'counts':counts,'reasons':reasons}))
"""
    db = json.loads(compose("run", "--rm", "--no-deps", "-T", "command-api", "python", "-c", probe))
    require(
        db["counts"]
        == {
            "proposals": 3,
            "decisions": 1,
            "requests": 1,
            "deadlines": 1,
            "attempts": 1,
            "results": int(not denied),
            "effects": int(not denied),
        },
        "More than one command obtained authority",
    )
    require(
        {
            "authority_required",
            "credential_required",
            "authority_protocol_required",
            "authority_fence_refused",
            "proposal_conflict",
        }
        <= set(db["reasons"]),
        "Missing authority audit reason",
    )
    return db
