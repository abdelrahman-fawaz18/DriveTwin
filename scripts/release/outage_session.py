"""Measure recovery of the normal queue after a real five-minute delivery outage."""

import json
import time
from datetime import datetime, timezone

from outage_fixture import read_state
from qualify_connected_speed_limit import ASSET, read, request, require, wait_ready, write
from qualify_live_restart import wait_for
from qualify_live_speed_command import prepare_live

OUTAGE_CASE = "outage-recovery"
DELIVERY_SERVICES = ("broker", "ingest")
OUTAGE_SECONDS = 300
RECOVERY_SECONDS = 120
SOURCE_MESSAGES = 480


def audit_trace(rows, first, last):
    times = [row["monotonic_s"] for row in rows]
    require(
        len(times) > 1 and all(0 < b - a < 5 for a, b in zip(times, times[1:])),
        "Outage observer coverage is incomplete",
    )
    outage = [
        row for row in rows if first["monotonic_s"] <= row["monotonic_s"] <= last["monotonic_s"]
    ]
    require(outage and outage[0] == first and outage[-1] == last, "Outage boundaries are missing")
    require(
        all(row["database"]["events"] == first["database"]["events"] for row in outage),
        "Database ingress occurred during the measured outage",
    )
    require(all(row["rejected"] == 0 for row in rows), "The queue recorded a rejected observation")
    return len(outage)


def prepare_outage(compose, shell, runtime, override, env, tokens, run, evidence, image):
    env["MONITORING_IMAGE"] = image
    prepare_live(compose, shell, runtime, override, env, tokens, run, False)
    write(
        runtime / "secrets/readers.json",
        {"readers": [{"label": "all", "token": tokens["reader"], "assets": [ASSET]}]},
    )
    config = read(override)
    config["services"]["source"]["command"][-1] = str(SOURCE_MESSAGES)
    config["services"]["outage-monitor"] = {
        "image": image,
        "pull_policy": "never",
        "command": ["python", "-m", "evdt.workload.outage_probe", "monitor"],
        "environment": {
            "EVDT_RUN_ID": run,
            "ASSET_ID": ASSET,
            "SOURCE_ID": "sumo-adapter",
            "SPOOL_FILE": "/spool/queue.db",
            "DB_USER": "evdt_admin",
            "DB_PASSWORD_FILE": "/run/secrets/admin_db",
            "READERS_FILE": "/run/secrets/readers",
            "PGOPTIONS": "-c default_transaction_read_only=on",
        },
        "secrets": ["admin_db", "readers"],
        "networks": ["fixture"],
        "volumes": ["spool:/spool", "source-output:/source-output:ro", f"{evidence}:/control"],
        "read_only": True,
        "tmpfs": ["/tmp:size=16m,mode=1777"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "128m",
        "pids_limit": 32,
    }
    write(override, config)


def run_outage(compose, shell, runtime, override, env, tokens, run, output, image):
    evidence = output / "outage"
    evidence.mkdir()
    latest_path = evidence / "latest.json"
    result = {"passed": False, "scope": "One paced bus with the normal durable queue"}

    def latest(predicate):
        if latest_path.is_file():
            row = read_state(latest_path)
            if predicate(row):
                return row
        return None

    def container(service):
        identity = compose("ps", "-aq", service).decode().strip()
        require(bool(identity) and "\n" not in identity, f"Expected one owned {service} container")
        return identity

    def stopped_states(identities):
        states = shell(
            "docker", "inspect", "--format", "{{.State.Running}} {{.State.StartedAt}}", *identities
        )
        rows = states.decode().splitlines()
        require(
            len(rows) == len(identities) and all(row.startswith("false ") for row in rows),
            "A delivery service is running during the outage",
        )
        return rows

    try:
        prepare_outage(compose, shell, runtime, override, env, tokens, run, evidence, image)
        compose(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "45",
            "--pull",
            "never",
            "registry",
            "api",
            "ingest",
            "sender",
        )
        api = "http://" + compose("port", "api", "8000").decode().strip()
        wait_ready(api)
        compose("up", "-d", "--no-deps", "--pull", "never", "outage-monitor", "source")
        result["warmup"] = wait_for(
            lambda: latest(lambda row: row["accepted"] >= 60 and row["pending"] == 0),
            "The paced source did not reach 60 confirmed observations",
            timeout=100,
        )
        broker, receiver = ids = tuple(container(service) for service in DELIVERY_SERVICES)
        shell("docker", "stop", "--time", "1", *ids, timeout=15)
        stopped = stopped_states(ids)
        outage_start = time.monotonic()
        first = wait_for(
            lambda: latest(lambda row: row["monotonic_s"] > outage_start), "No outage sample"
        )
        result["outage_start"] = first
        print(
            "Broker and ingestion stopped. Measuring five minutes of queued observations.",
            flush=True,
        )
        for elapsed in range(30, OUTAGE_SECONDS + 1, 30):
            last = wait_for(
                lambda: latest(lambda row: row["monotonic_s"] >= outage_start + elapsed),
                "The outage observer stopped advancing",
                timeout=35,
            )
            require(
                stopped_states(ids) == stopped, "A delivery service restarted during the outage"
            )
            require(
                last["database"]["events"] == first["database"]["events"],
                "Database ingress resumed early",
            )
            print(
                f"Outage {elapsed}/{OUTAGE_SECONDS} seconds. Queued messages {last['pending']}.",
                flush=True,
            )
        result["outage_end"] = last
        require(last["pending"] >= 300, "Five-minute backlog did not reach 300 messages")

        restart_stamp = datetime.now(timezone.utc).isoformat()
        restore_start = time.monotonic()
        result["outage_seconds"] = restore_start - outage_start
        require(result["outage_seconds"] >= OUTAGE_SECONDS, "Outage was shorter than five minutes")
        shell("docker", "start", broker, timeout=15)
        shell("docker", "start", receiver, timeout=15)
        wait_for(
            lambda: (
                b"monitoring_ingest_ready"
                in shell("docker", "logs", "--since", restart_stamp, receiver)
            ),
            "Ingestion did not confirm a new subscription after restart",
            timeout=30,
        )
        ready = time.monotonic()
        result["restoration_to_receiver_ready_s"] = ready - restore_start
        print("Ingestion subscribed again. Measuring delivery and queue drain.", flush=True)
        drained = wait_for(
            lambda: latest(
                lambda row: (
                    row["monotonic_s"] >= ready
                    and row["pending"] == 0
                    and row["accepted"] >= last["generated"]
                )
            ),
            "The original backlog did not drain within the recovery budget",
            timeout=RECOVERY_SECONDS - (time.monotonic() - restore_start),
        )
        result["drain"] = drained
        result["restoration_to_observed_empty_s"] = time.monotonic() - restore_start
        result["receiver_ready_to_empty_sample_s"] = drained["monotonic_s"] - ready
        require(
            result["restoration_to_observed_empty_s"] <= RECOVERY_SECONDS,
            "Recovery exceeded 120 seconds",
        )
        require(drained["generated"] < SOURCE_MESSAGES, "Backlog drained after the bus stopped")

        def fresh_snapshot():
            status, snapshot = request(api, f"/v1/snapshot?asset_id={ASSET}", tokens["reader"])
            if (
                status == 200
                and snapshot["run_id"] == run
                and len(snapshot["items"]) == 20
                and all(
                    item["freshness"] == "fresh"
                    and item["source_state"] == "running"
                    and item["observation"]["stream_seq"] >= last["generated"]
                    for item in snapshot["items"]
                )
            ):
                return min(item["observation"]["stream_seq"] for item in snapshot["items"])
            return None

        result["fresh_post_outage_sequence"] = wait_for(
            fresh_snapshot, "No fresh post-outage snapshot", timeout=10
        )
        result["final_sample"] = wait_for(
            lambda: latest(
                lambda row: (
                    row["accepted"] == row["generated"] == SOURCE_MESSAGES and row["pending"] == 0
                )
            ),
            "Final source and queue accounting incomplete",
            timeout=180,
        )
        require(
            shell("docker", "wait", container("source"), timeout=20).strip() == b"0",
            "Paced source failed",
        )
        result["final"] = json.loads(
            compose(
                "exec",
                "-T",
                "outage-monitor",
                "python",
                "-m",
                "evdt.workload.outage_probe",
                "verify",
                "--events",
                str(SOURCE_MESSAGES),
            )
        )
        require(result["final"]["passed"], "Original packet verification failed")
        compose(
            "cp", "source:/source-output/source-report.json", str(evidence / "source-report.json")
        )
        (evidence / "finish").touch()
        result["observer"] = wait_for(
            lambda: (
                (evidence / "monitor-result.json").is_file()
                and read_state(evidence / "monitor-result.json")
            ),
            "The observer did not finish cleanly",
        )
        require(result["observer"]["passed"], "Current observations regressed")
        rows = [json.loads(line) for line in (evidence / "samples.jsonl").read_bytes().splitlines()]
        require(len(rows) == result["observer"]["samples"], "Observer trace sample count differs")
        result["outage_trace_samples"] = audit_trace(rows, first, last)
        result["passed"] = True
        return result
    finally:
        (evidence / "finish").touch()
        write(output / "outage-result.json", result)
