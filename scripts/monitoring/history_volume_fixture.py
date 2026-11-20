"""Coordinate original source-backed history; never claim live transport from seeding."""

import json
import secrets
import shutil
import time
from pathlib import Path
from urllib.request import urlopen

from history_source_audit import audit
from workload_source_fixture import ASSETS, require, wait

ROOT = Path(__file__).resolve().parents[2]


def marked(raw, marker):
    rows = [line.split("=", 1)[1] for line in raw.splitlines() if line.startswith(marker + "=")]
    require(len(rows) == 1, "Missing or ambiguous history result: " + marker)
    return json.loads(rows[0])


def qualify(compose, execute, prefix, runtime, output, env, record, *, samples, asset_count):
    require(samples in (60, 600, 7200) and 1 <= asset_count <= 10, "Unsupported history scope")
    protocol = json.loads((ROOT / "benchmarks/history.json").read_bytes())
    require(protocol["version"] == "history-volume/1.0.0", "Unknown history protocol")
    assets = ASSETS[:asset_count]
    full = samples == protocol["samples_per_asset"] and assets == protocol["assets"]
    result = {
        "passed": False,
        "W2_database_passed": False,
        "Q05_passed": False,
        "Q10_passed": False,
        "scope": "Complete W2 history database" if full else "Bounded history database pilot",
        "assets": assets,
        "samples_per_asset": samples,
        "source_mode": "actual sequential accelerated plants; original file to normal repository.store",
        "live_delivery_claim": False,
        "sources": {},
        "loads": {},
    }
    record["history_volume"] = result
    project = prefix[prefix.index("-p") + 1]

    def owned_run(kind, index, *argv, timeout):
        name = project + "-history-" + kind + "-" + str(index)
        try:
            raw = compose("run", "--name", name, "--no-deps", *argv, timeout=timeout)
            state = json.loads(execute(["docker", "inspect", "--format", "{{json .State}}", name]))
            require(
                state["Running"] is False
                and state["ExitCode"] == 0
                and state["OOMKilled"] is False,
                "History container exit/OOM check failed",
            )
            return raw, state
        finally:
            execute(["docker", "rm", "-f", name], check=False)

    reserve = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())["limits"][
        "host_disk_reserve_bytes"
    ]
    result["initial_host_disk_free_bytes"] = shutil.disk_usage(ROOT).free
    require(
        result["initial_host_disk_free_bytes"] >= reserve + 8 * 1024**3,
        "History fixture needs host reserve plus 8 GiB temporary headroom",
    )
    secret_dir = Path(env["SECRET_DIRECTORY"])
    for index in range(3, 11):
        (secret_dir / f"producer{index}").write_text(secrets.token_urlsafe(32) + "\n")
    readers = json.loads((secret_dir / "readers.json").read_bytes())
    readers["readers"][0]["assets"] = ASSETS
    (secret_dir / "readers.json").write_text(json.dumps(readers))
    writers = {
        "writers": [
            {
                "token": (secret_dir / f"producer{index}").read_text().strip(),
                "run_id": env["EVDT_RUN_ID"],
                "asset_id": asset,
                "source_id": "sumo-adapter",
            }
            for index, asset in enumerate(ASSETS, 1)
        ]
    }
    (secret_dir / "writers.json").write_text(json.dumps(writers))
    setup = execute(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "512m",
            "-v",
            str(secret_dir) + ":/policy",
            "-v",
            str(ROOT / "simulation/montreal-history") + ":/history-scenario:ro",
            "-v",
            str(ROOT / "data/public/montreal-core-v1/roads.osm.xml") + ":/input/roads.osm.xml:ro",
            "--entrypoint",
            "python",
            env["SIMULATOR_IMAGE"],
            "-m",
            "evdt.workload.source_setup",
            "--scenario",
            "/history-scenario",
            "--run-id",
            env["EVDT_RUN_ID"],
            "--output",
            "/policy",
        ],
        timeout=120,
    )
    result["preparation"] = marked(setup, "SOURCE_SETUP_RESULT")
    policy = json.loads((secret_dir / "run_policy.json").read_bytes())
    for source in policy["sources"]:
        source["expected"]["paced"] = False
    (secret_dir / "run_policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
    override = runtime / "history-volume.compose.json"
    override.write_text(
        json.dumps(
            {
                "services": {
                    "source": {
                        "volumes": [str(ROOT / "simulation/montreal-history") + ":/scenario:ro"]
                    },
                    "probe": {"volumes": [str(output) + ":/history-evidence:ro"]},
                }
            }
        )
        + "\n"
    )
    prefix.extend(["-f", str(override)])
    compose("config", "--quiet")
    compose("up", "-d", "db", "migrate", "registry", "api", timeout=120)

    def ready():
        with urlopen(f"http://127.0.0.1:{env['API_PORT']}/health/ready", timeout=3) as response:
            return json.load(response)["ready"]

    wait(ready)
    # Metadata maps are provisioned through the ordinary map validator before reads.
    for index in range(2, 11):
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-v",
            str(secret_dir / f"map_context{index}.json") + ":/history-map:ro",
            "probe",
            "python",
            "-c",
            "from pathlib import Path;from evdt.monitoring.repository import connection;from evdt.monitoring.map_context import provision;c=connection();provision(c,Path('/history-map'));c.commit();c.close()",
        )
    for index, asset in enumerate(assets, 1):
        require(
            shutil.disk_usage(ROOT).free >= reserve + 2 * 1024**3,
            "History fixture reached its host disk reserve",
        )
        print(f"History {asset}: stepping {samples} actual simulated seconds", flush=True)
        source_started = time.monotonic()
        generated, source_state = owned_run(
            "source",
            index,
            "-v",
            str(output) + ":/history-evidence",
            "-v",
            str(secret_dir / f"producer{index}") + ":/history-producer:ro",
            "-e",
            "PRODUCER_TOKEN_FILE=/history-producer",
            "source",
            "python",
            "-m",
            "evdt.workload.history_source",
            "--register",
            "--output",
            "/history-evidence/" + asset,
            "--run-id",
            env["EVDT_RUN_ID"],
            "--asset-id",
            asset,
            "--maximum-samples",
            str(samples),
            timeout=300,
        )
        summary = marked(generated, "HISTORY_SOURCE_RESULT")
        require(summary["passed"], "Actual history source failed")
        result["sources"][asset] = {
            "generation": summary,
            "container_state": source_state,
            "independent_audit": audit(output / asset, ROOT / "simulation/montreal-history"),
            "source_and_audit_wall_s": time.monotonic() - source_started,
        }
        print(
            f"History {asset}: loading original bytes through normal validation/storage", flush=True
        )
        loaded, loader_state = owned_run(
            "loader",
            index,
            "-e",
            "DB_USER=evdt_ingest",
            "-e",
            "DB_PASSWORD_FILE=/run/secrets/ingest_db",
            "probe",
            "python",
            "-m",
            "evdt.workload.history_load",
            "load",
            "--directory",
            "/history-evidence/" + asset,
            timeout=1800,
        )
        result["loads"][asset] = marked(loaded, "HISTORY_LOAD_RESULT")
        result["loads"][asset]["container_state"] = loader_state
        require(result["loads"][asset]["passed"], "History load failed")
    print(
        "Reconciling every stored original and scalar, metadata, current values and retries",
        flush=True,
    )
    checked = compose(
        "run",
        "--rm",
        "--no-deps",
        "probe",
        "python",
        "-m",
        "evdt.workload.history_load",
        "verify",
        "--directory",
        "/history-evidence",
        "--assets",
        *assets,
        "--samples",
        str(samples),
        timeout=1200,
    )
    result["verification"] = marked(checked, "HISTORY_LOAD_RESULT")
    require(result["verification"]["passed"], "History database reconciliation failed")
    from history_checkpoint import save

    print("Saving a sealed private history checkpoint before owned database cleanup", flush=True)
    result["checkpoint"] = save(
        compose, execute, prefix, runtime, output, env, record, assets=assets, samples=samples
    )
    result["passed"] = True
    result["W2_database_passed"] = full
    result["final_host_disk_free_bytes"] = shutil.disk_usage(ROOT).free
    require(result["final_host_disk_free_bytes"] >= reserve, "Host disk reserve failed")
    return result
