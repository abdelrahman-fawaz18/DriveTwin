"""Capture the approved browser story through the ordinary guarded live services.

Raw responses and credentials remain in ignored evidence. No SUMO actuation is
performed here: the charge actor uses the existing scoped approval endpoint.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / ".private/reviews/moving-charge-operator-profile-01.json"
FORK = 30


def read(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def capture(output, outcome, errors):
    deadline = time.monotonic() + 1600
    while not (output / "ready.json").exists():
        if (output / "operator-result.json").exists() or time.monotonic() > deadline:
            errors.append("Services did not become ready")
            return
        time.sleep(0.5)
    keys = read(output / "ready.json")
    token = keys["charging_operator_token"]
    opener = build_opener(ProxyHandler({}))
    base = f"/v2/operator/{keys['run_id']}/bus-001/charging"
    prefix_stop = threading.Event()

    def retain_checkpoint_context():
        # Keep the boundary independent of slower map and forecast requests.
        client = build_opener(ProxyHandler({}))
        seen = set()
        with (output / "demo-prefix-context.jsonl").open("x", encoding="utf-8") as handle:
            end = time.monotonic() + 90
            while not prefix_stop.is_set() and time.monotonic() < end:
                try:
                    request = Request(
                        "http://127.0.0.1:18765/v2/energy-input?asset_id=bus-001",
                        headers={"Authorization": "Bearer " + keys["monitoring_token"]},
                    )
                    with client.open(request, timeout=3) as response:
                        body = json.load(response)
                    context = body.get("context")
                    if context:
                        working = context["working_s"]
                        if working not in seen:
                            seen.add(working)
                            handle.write(
                                json.dumps(
                                    {
                                        "kind": "context",
                                        "available_working_s": working,
                                        "http": 200,
                                        "body": {"context": context},
                                    }
                                )
                                + "\n"
                            )
                            handle.flush()
                        if working > FORK:
                            break
                except (OSError, ValueError, URLError):
                    pass
                prefix_stop.wait(0.1)
        if FORK not in seen:
            errors.append("The exact checkpoint context was not captured")

    prefix_reader = threading.Thread(target=retain_checkpoint_context, daemon=True)
    prefix_reader.start()

    def call(path, body=None, *, reader=False):
        request = Request(
            "http://127.0.0.1:18765" + path,
            headers={
                "Authorization": "Bearer " + (keys["monitoring_token"] if reader else token),
                "Content-Type": "application/json",
            },
            data=None if body is None else json.dumps(body).encode(),
        )
        try:
            with opener.open(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    approved = False
    pending = None
    last = {}
    next_slow = 0
    with (output / "demo-capture.jsonl").open("x", encoding="utf-8") as log:

        def emit(kind, working, status, body):
            if kind in ("forecasts", "advisory") and status == 200:
                code, after = call("/v1/snapshot?asset_id=bus-001")
                if code != 200:
                    raise ValueError("Cannot establish result presentation time")
                working = max(
                    working,
                    max(
                        (
                            i["observation"]["sim_time_s"]
                            for i in after["items"]
                            if i.get("observation")
                        ),
                        default=0,
                    ),
                )
            key = json.dumps(body, sort_keys=True)
            if last.get(kind) != key:
                log.write(
                    json.dumps(
                        {"kind": kind, "available_working_s": working, "http": status, "body": body}
                    )
                    + "\n"
                )
                log.flush()
                last[kind] = key

        while not (output / "operator-result.json").exists() and time.monotonic() < deadline:
            if (output / "arrived.json").exists():
                break
            try:
                status, snapshot = call("/v1/snapshot?asset_id=bus-001")
                if status != 200:
                    time.sleep(0.5)
                    continue
                working = max(
                    (
                        i["observation"]["sim_time_s"]
                        for i in snapshot["items"]
                        if i.get("observation")
                    ),
                    default=0,
                )
                emit("snapshot", working, status, snapshot)
                code, context = call("/v2/energy-input?asset_id=bus-001", reader=True)
                emit("context", working, code, context)
                status, advisory = call("/v2/charging-advisory?asset_id=bus-001")
                emit("advisory", working, status, advisory)
                offer = advisory.get("estimate") if status == 200 else None
                if (
                    outcome == "charge"
                    and working >= FORK
                    and not approved
                    and offer
                    and advisory.get("can_respond")
                ):
                    waiting = any(
                        offer.get(k)
                        for k in (
                            "approval_waiting_for_road",
                            "approval_waiting_for_motion",
                            "past_approval_point",
                        )
                    )
                    if not waiting:
                        if offer["bay_id"] != "dev-bay-2":
                            errors.append("The reviewed demonstration bay changed")
                            break
                        # Keep the same request after a lost response; never create a
                        # second command while the first outcome is ambiguous.
                        pending = pending or {
                            "run_id": keys["run_id"],
                            "asset_id": "bus-001",
                            "command_id": str(uuid4()),
                            "job_fence": offer["job_fence"],
                            "candidate_sha256": offer["candidate_sha256"],
                        }
                        code, receipt = call(base + "/approve", pending)
                        emit("approval", working, code, receipt)
                        if code == 200:
                            approved = True
                        elif code == 409 and receipt.get("detail") in (
                            "moving_charge_commit_point_passed",
                            "moving_charge_stale_progress",
                        ):
                            pending = None  # Explicit refusal, not an ambiguous dispatch.
                        else:
                            errors.append("Approval refused: " + str(code))
                            break
                if time.monotonic() >= next_slow:
                    for kind, path in (
                        ("forecasts", "/v2/energy-forecasts?asset_id=bus-001"),
                        ("map", "/v1/map-context?asset_id=bus-001"),
                        ("catalog", "/v1/catalog"),
                        ("operator", base + "/status"),
                    ):
                        code, body = call(path)
                        emit(kind, working, code, body)
                    next_slow = time.monotonic() + 1
            except (OSError, URLError, TimeoutError, ValueError):
                # The runner remains responsible for failure/cleanup. A transient
                # collection gap is retained and checked by the exporter.
                pass
            time.sleep(0.25)
    if outcome == "charge" and not approved:
        errors.append("No successful approval after the checkpoint")
    prefix_stop.set()
    prefix_reader.join(timeout=4)
    if prefix_reader.is_alive():
        errors.append("Checkpoint reader did not finish")
    save(
        output / "demo-actor.json",
        {"outcome": outcome, "fork_working_s": FORK, "approved": approved, "errors": errors},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Start the two explicitly authorized sequential runs"
    )
    parser.add_argument(
        "--receipt", type=Path, help="Write the private pair location for the preparation tool"
    )
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "scripts/geographic"))
    from live_inputs import check

    profile = read(PROFILE)
    check(profile)
    if profile.get("time_scale") != 1 or profile.get("starting_charge_percent") != 11.6:
        raise ValueError("The reviewed demonstration configuration changed")
    protocol = {
        "version": "browser-demo-capture/1.0.0",
        "fork_working_s": FORK,
        "selected_bay": "dev-bay-2",
        "seed": 32017,
        "profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
        "profile": profile,
        "outcomes": {},
    }
    if not args.run:
        print("BR-01 inputs verified. No services started.")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    pair = ROOT / ("evidence/energy-v2/browser-demo-pair-" + stamp)
    pair.mkdir()
    save(pair / "protocol.json", protocol)
    if args.receipt:
        receipt = args.receipt.resolve()
        if not any(
            receipt.is_relative_to(ROOT / name) for name in (".private", "evidence", ".runtime")
        ):
            raise ValueError("Pair locator must remain in private project storage")
        save(receipt, {"protocol": (pair / "protocol.json").relative_to(ROOT).as_posix()})
    print("PAIR:", pair, flush=True)
    for outcome in ("continue", "charge"):
        output = ROOT / (f"evidence/energy-v2/browser-demo-{outcome}-" + stamp)
        errors = []
        actor = threading.Thread(target=capture, args=(output, outcome, errors), daemon=True)
        actor.start()
        result = subprocess.run(
            [
                sys.executable,
                "scripts/geographic/live.py",
                "--profile",
                PROFILE.relative_to(ROOT).as_posix(),
                "--no-browser",
                "--output",
                output.relative_to(ROOT).as_posix(),
            ],
            cwd=ROOT,
        )
        actor.join(timeout=10)
        protocol["outcomes"][outcome] = {
            "directory": output.relative_to(ROOT).as_posix(),
            "exit_code": result.returncode,
            "actor_errors": errors,
        }
        save(pair / "protocol.json", protocol)
        if result.returncode or errors or actor.is_alive():
            print("Stopped after the failed or blocked attempt. Inspect", output, flush=True)
            return 1
    print("Both guarded recordings saved. Next: exact prefix and effect verification.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
