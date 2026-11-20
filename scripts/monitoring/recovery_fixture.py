"""Coordinated development backup and independent real-service restore qualification."""

import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.recovery.bundle import seal, verify  # noqa: E402


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait(predicate, seconds=90):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if predicate():
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    raise RuntimeError("Recovery readiness deadline exceeded")


def prepare_restore_queue(compose, execute, project):
    """Create the second queue through Compose before any probe can auto-create it."""
    name = project + "_spool2"
    existing = execute(["docker", "volume", "ls", "-q", "--filter", "name=" + name])
    require(name not in existing.splitlines(), "Restore queue volume already exists")
    # Create the stopped service only; do not run a sender before archive/queue restoration.
    compose("create", "sender2")
    volumes = json.loads(execute(["docker", "volume", "inspect", name]))
    require(isinstance(volumes, list) and len(volumes) == 1, "Invalid restore queue inventory")
    volume = volumes[0]
    require(isinstance(volume, dict) and volume.get("Name") == name, "Wrong restore queue volume")
    labels = volume.get("Labels") or {}
    require(
        isinstance(labels, dict)
        and labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.volume") == "spool2",
        "Restore queue volume must be owned by this Compose project",
    )
    return {"passed": True, "compose_owned": True, "service_started": False}


def qualify(compose, execute, prefix, runtime, output, env, readers, record):
    result = {
        "passed": False,
        "scope": "sealed development checkpoint; not W2 or physical plant resumption",
    }
    record["recovery"] = result
    prepare_start = time.monotonic()
    secret_dir = Path(env["SECRET_DIRECTORY"])
    policy = json.loads((secret_dir / "run_policy.json").read_bytes())
    for source in policy["sources"]:
        source["expected"]["paced"] = False
    (secret_dir / "run_policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n")
    source_project = prefix[prefix.index("-p") + 1]
    target_project = source_project + "-restore"
    target_runtime = runtime / "restore"
    target_secrets = target_runtime / "secrets"
    target_secrets.mkdir(parents=True)
    bundle_parent = ROOT / ".private/backups" / source_project
    bundle_parent.mkdir(parents=True)
    partial = bundle_parent / "checkpoint.partial"
    final = bundle_parent / "checkpoint"
    partial.mkdir()

    def probe(c, project, module, *args, bundle=None):
        mounts = ["-v", project + "_spool2:/spool-2"]
        if bundle:
            mounts += ["-v", str(bundle) + ":/recovery"]
        return c("run", "--rm", "--no-deps", *mounts, "probe", "python", "-m", module, *args)

    def inventory(c, project):
        return json.loads(probe(c, project, "evdt.recovery.database", "inventory"))

    def cluster(c, project):
        return probe(c, project, "evdt.recovery.database", "cluster").strip()

    def ready(port):
        with urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=3) as response:
            return json.load(response)["ready"]

    def binary(argv, *, source=None, destination=None):
        # Never route a PostgreSQL binary archive through text encoding or a PTY.
        with source.open("rb") if source else open("/dev/null", "rb") as stdin:
            if destination:
                with destination.open("xb") as stdout:
                    completed = subprocess.run(
                        argv,
                        cwd=ROOT,
                        stdin=stdin,
                        stdout=stdout,
                        stderr=subprocess.PIPE,
                        timeout=120,
                    )
            else:
                completed = subprocess.run(
                    argv, cwd=ROOT, stdin=stdin, capture_output=True, timeout=120
                )
        record["commands"].append({"argv": argv, "exit_code": completed.returncode})
        (output / ("dump.stderr" if destination else "restore.stderr")).write_bytes(
            completed.stderr
        )
        require(
            completed.returncode == 0, "Database archive operation failed; inspect private stderr"
        )

    startup_start = time.monotonic()
    compose(
        "up", "-d", "db", "broker", "migrate", "registry", "ingest", "api", "replay", timeout=120
    )
    port = int(env["API_PORT"])
    wait(lambda: ready(port))
    startup_elapsed = time.monotonic() - startup_start
    result["startup"] = {
        "elapsed_s": startup_elapsed,
        "limit_s": 120,
        "passed": startup_elapsed <= 120,
    }
    require(startup_elapsed <= 120, "Application startup exceeded 120 seconds")
    compose(
        "run",
        "--rm",
        "--no-deps",
        "probe",
        "python",
        "-c",
        "from pathlib import Path;from evdt.monitoring.repository import connection;from evdt.monitoring.map_context import provision;c=connection();provision(c,Path('/run/secrets/map_context2'));c.commit()",
    )
    print(
        "Generating two real source queues, then delivering exactly 25 events per bus", flush=True
    )
    for service in ("source", "source2"):
        compose(
            "run",
            "--rm",
            "--no-deps",
            service,
            "python",
            "-m",
            "evdt.monitoring.adapter",
            "--accelerated",
            "--maximum-samples",
            "30",
            timeout=180,
        )
    result["checkpoint_queues"] = json.loads(
        probe(compose, source_project, "evdt.recovery.probe", "partial")
    )
    compose("stop", "registry", "ingest")
    # All writers/producers are stopped. Seal new connections as a second guard.
    compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "evdt_admin",
        "-d",
        "evdt",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "ALTER DATABASE evdt SET default_transaction_read_only=on",
    )
    result["seal"] = json.loads(probe(compose, source_project, "evdt.recovery.database", "sealed"))
    require(result["seal"] == {"readonly_write_denied": True}, "Source seal failed")
    source_cluster = cluster(compose, source_project)
    result["preparation_seconds"] = round(time.monotonic() - prepare_start, 3)
    backup_start = time.monotonic()
    original = inventory(compose, source_project)
    expected_reads = json.loads(probe(compose, source_project, "evdt.recovery.probe", "reads"))
    (partial / "inventory.json").write_text(json.dumps(original, sort_keys=True) + "\n")
    (partial / "application-queries.json").write_text(
        json.dumps(expected_reads, sort_keys=True) + "\n"
    )
    result["queue_inventory"] = json.loads(
        probe(compose, source_project, "evdt.recovery.probe", "snapshot", bundle=partial)
    )
    for folder, pattern in [
        ("contracts", "**/*.json"),
        ("catalog", "*.json"),
        ("migrations", "monitoring/*.sql"),
    ]:
        for path in (ROOT / folder).glob(pattern):
            dest = partial / "artifacts" / folder / path.relative_to(ROOT / folder)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
    (partial / "run-config").mkdir()
    for name in ("run_policy.json", "map_context.json", "map_context2.json"):
        shutil.copyfile(secret_dir / name, partial / "run-config" / name)
    binary(
        [
            *prefix,
            "exec",
            "-T",
            "db",
            "pg_dump",
            "-U",
            "evdt_admin",
            "-d",
            "evdt",
            "--format=custom",
            "--no-owner",
        ],
        destination=partial / "database.dump",
    )
    require(inventory(compose, source_project) == original, "Source changed during backup")
    image_id = execute(
        ["docker", "image", "inspect", env["MONITORING_IMAGE"], "--format", "{{.Id}}"]
    )
    pg_version = compose("exec", "-T", "db", "postgres", "--version")
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_cluster": source_cluster,
        "run_id": env["EVDT_RUN_ID"],
        "application_image_id": image_id,
        "postgres_image": env["POSTGRES_IMAGE"],
        "postgres_version": pg_version,
        "source_commit": execute(["git", "rev-parse", "HEAD"]),
        "source_sha256": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ROOT.glob("src/**/*.py")
        },
        "checkpoint": result["checkpoint_queues"],
        "consistency": "all writers stopped; source database read-only",
    }
    manifest = seal(partial, metadata)
    partial.rename(final)
    verify(final, image_id)
    result["backup_seconds"] = round(time.monotonic() - backup_start, 3)
    result["backup_bytes"] = sum(entry["size"] for entry in manifest["files"].values())
    result["backup_files"] = len(manifest["files"])
    result["manifest_sha256"] = hashlib.sha256((final / "manifest.json").read_bytes()).hexdigest()
    result["bundle"] = final.relative_to(ROOT).as_posix()
    print("Checkpoint sealed; restoring into a new database with rotated credentials", flush=True)
    restore_start = time.monotonic()
    compose("stop", "api", "replay")
    # Required configuration is recovered, secrets are deliberately not copied.
    for name in ("run_policy.json", "map_context.json", "map_context2.json"):
        shutil.copyfile(final / "run-config" / name, target_secrets / name)
    secret_names = [
        p.name
        for p in secret_dir.iterdir()
        if p.is_file()
        and p.suffix != ".json"
        and p.name not in ("passwords", "mosquitto.conf", "acl")
    ]
    for name in secret_names:
        (target_secrets / name).write_text(secrets.token_urlsafe(32) + "\n")
    for name in ("readers.json", "replay_readers.json"):
        old = json.loads((secret_dir / name).read_bytes())
        for entry in old["readers"]:
            entry["token"] = secrets.token_urlsafe(32)
        (target_secrets / name).write_text(json.dumps(old))
    writers = json.loads((secret_dir / "writers.json").read_bytes())
    for index, entry in enumerate(writers["writers"], 1):
        entry["token"] = (target_secrets / f"producer{index}").read_text().strip()
    (target_secrets / "writers.json").write_text(json.dumps(writers))
    for name in ("mosquitto.conf", "acl"):
        shutil.copyfile(secret_dir / name, target_secrets / name)
    broker_users = {
        "ingest": "ingest_mqtt",
        "adapter-bus-001": "adapter_mqtt",
        "adapter-bus-002": "adapter2_mqtt",
        "observer": "observer_mqtt",
        "replay-observer": "replay_mqtt",
    }
    (target_secrets / "passwords").write_text(
        "".join(
            user + ":" + (target_secrets / name).read_text().strip() + "\n"
            for user, name in broker_users.items()
        )
    )
    execute(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "mosquitto_passwd",
            "-v",
            str(target_secrets) + ":/config",
            env["MOSQUITTO_IMAGE"],
            "-U",
            "/config/passwords",
        ]
    )
    target_env = {
        **env,
        "SECRET_DIRECTORY": str(target_secrets),
        "API_PORT": str(port + 2),
        "REPLAY_PORT": str(port + 3),
        "BACKUP_DIRECTORY": str(final),
        "BROKER_CONFIG": str(target_secrets / "mosquitto.conf"),
        "BROKER_PASSWORDS": str(target_secrets / "passwords"),
        "BROKER_ACL": str(target_secrets / "acl"),
    }
    env_file = target_runtime / "compose.env"
    env_file.write_text("".join(k + "=" + v + "\n" for k, v in target_env.items()))
    target_prefix = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "--env-file",
        str(env_file),
        "-f",
        str(ROOT / "ops/compose/compose.monitoring.yaml"),
        "-f",
        str(ROOT / "ops/compose/compose.extension.yaml"),
        "-f",
        str(ROOT / "ops/compose/compose.recovery.yaml"),
        "-p",
        target_project,
        "--profile",
        "*",
    ]

    def target(*args, **kwargs):
        return execute([*target_prefix, *args], **kwargs)

    # Refuse an occupied target before entering the cleanup-owning block.
    # Its finally must never remove a preexisting project's containers or volumes.
    require(
        not execute(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + target_project,
            ]
        ),
        "Target project already exists",
    )
    existing_target_volumes = execute(
        ["docker", "volume", "ls", "-q", "--filter", "name=" + target_project + "_"]
    ).splitlines()
    require(
        not any(name.startswith(target_project + "_") for name in existing_target_volumes),
        "Restore target volumes already exist",
    )
    try:
        result["restore_queue_ownership"] = prepare_restore_queue(target, execute, target_project)
        target("up", "--no-deps", "-d", "db", "broker")
        wait(
            lambda: target(
                "exec", "-T", "db", "pg_isready", "-U", "evdt_admin", "-d", "evdt", check=False
            ).endswith("accepting connections")
        )
        target_cluster = cluster(target, target_project)
        require(target_cluster != source_cluster, "Target cluster must be independent")
        result["different_cluster"] = True
        result["empty_target_guards"] = json.loads(
            probe(
                target,
                target_project,
                "evdt.recovery.database",
                "empty-guards",
                "--source-cluster",
                source_cluster,
            )
        )
        # Positive preflight and fresh roles happen before pg_restore can write schema/data.
        probe(
            target,
            target_project,
            "evdt.recovery.database",
            "bootstrap",
            "--source-cluster",
            source_cluster,
        )
        verify(final, image_id)
        binary(
            [
                *target_prefix,
                "exec",
                "-T",
                "db",
                "pg_restore",
                "-U",
                "evdt_admin",
                "-d",
                "evdt",
                "--no-owner",
                "--single-transaction",
                "--exit-on-error",
            ],
            source=final / "database.dump",
        )
        restored = inventory(target, target_project)
        (output / "restored-inventory.json").write_text(json.dumps(restored, indent=2) + "\n")
        require(restored == original, "Restored table/schema/privilege inventory differs")
        result["inventory_equal"] = True
        for c, project in [(compose, source_project), (target, target_project)]:
            negative = json.loads(
                probe(
                    c,
                    project,
                    "evdt.recovery.database",
                    "refusal",
                    "--source-cluster",
                    source_cluster,
                )
            )
            expected = (
                "Refusing original source cluster"
                if project == source_project
                else "Restore target must be empty"
            )
            require(negative == {"refused": expected}, "Unsafe restore preflight outcome differs")
        result["source_and_nonempty_refused"] = True
        require(
            inventory(compose, source_project) == original,
            "Original database changed during restore",
        )
        compose("stop", "api", "replay", "db", "broker")
        result["source_stopped_before_reads"] = True
        for service in ("sender", "sender2"):
            target("run", "--rm", "--no-deps", service, "python", "-c", "pass")
        recovered_queues = json.loads(
            probe(target, target_project, "evdt.recovery.probe", "load", bundle=final)
        )
        require(recovered_queues == result["queue_inventory"], "Restored queue inventories differ")
        target("up", "--no-deps", "-d", "api", "replay")
        wait(lambda: ready(port + 2) and ready(port + 3))
        before_resume = json.loads(probe(target, target_project, "evdt.recovery.probe", "reads"))
        require(before_resume == expected_reads, "Representative API reads changed after restore")
        result["application_queries_equal"] = True
        result["roles"] = json.loads(
            target(
                "run",
                "--rm",
                "--no-deps",
                "-v",
                str(secret_dir / "read_db") + ":/old-read-password:ro",
                "probe",
                "python",
                "-m",
                "evdt.recovery.probe",
                "permissions",
            )
        )
        result["query_ready_seconds"] = round(time.monotonic() - restore_start, 3)
        result["checkpoint_queries"] = {
            a: {k: v for k, v in d.items() if k in ("events", "scalars")}
            for a, d in before_resume.items()
        }
        # Old reader token is invalid at the new deployment.
        try:
            urlopen(
                Request(
                    f"http://127.0.0.1:{port + 2}/v1/snapshot?asset_id=bus-001",
                    headers={"Authorization": "Bearer " + readers[0]["token"]},
                ),
                timeout=5,
            )
            raise RuntimeError("Old reader credential was accepted")
        except HTTPError as exc:
            require(exc.code == 401, "Unexpected old-credential outcome")
        result["old_reader_denied"] = True
        target("up", "--no-deps", "-d", "ingest", "sender", "sender2")

        def drained():
            counts = json.loads(probe(target, target_project, "evdt.recovery.probe", "queues"))
            return all(v == {"pending": 0, "accepted": 30} for v in counts.values())

        wait(drained)
        final_reads = json.loads(
            probe(target, target_project, "evdt.recovery.probe", "reads", "--events", "30")
        )
        for asset, data in final_reads.items():
            require(
                set(before_resume[asset]["packet_hashes"]) < set(data["packet_hashes"]),
                "Recovered backlog replaced saved packets",
            )
            generated = json.loads((final / "recordings" / (asset + ".json")).read_bytes())
            require(
                data["packet_hashes"] == generated["wire_sha256"],
                "Recovered bytes differ from actual generated recording",
            )
        before_retry = inventory(target, target_project)
        result["duplicate_retry"] = json.loads(
            probe(target, target_project, "evdt.recovery.probe", "retry")
        )
        require(
            inventory(target, target_project) == before_retry,
            "Accepted retry changed durable records",
        )
        result["final_queries"] = {
            a: {k: v for k, v in d.items() if k in ("events", "scalars")}
            for a, d in final_reads.items()
        }
        result["final_queues"] = json.loads(
            probe(target, target_project, "evdt.recovery.probe", "queues")
        )
        result["tables"] = original["tables"]
        result["recovery_seconds_through_backlog"] = round(time.monotonic() - restore_start, 3)
        result["passed"] = True
        return result
    finally:
        target("logs", "--no-color", check=False)
        target("down", "--volumes", "--remove-orphans", timeout=90)
        result["cleanup"] = {
            kind: execute(
                ["docker", *argv, "--filter", "label=com.docker.compose.project=" + target_project]
            )
            for kind, argv in [("containers", ["ps", "-aq"]), ("volumes", ["volume", "ls", "-q"])]
        }
        queue_name = target_project + "_spool2"
        remaining = execute(
            ["docker", "volume", "ls", "-q", "--filter", "name=" + queue_name]
        ).splitlines()
        result["cleanup"]["queue_volume"] = queue_name if queue_name in remaining else ""
        require(not any(result["cleanup"].values()), "Restore fixture resources remain")
