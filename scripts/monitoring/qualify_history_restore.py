"""Restore a sealed history checkpoint into an independent, temporary cluster."""

import argparse
import hashlib
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.recovery.history_bundle import verify  # noqa: E402


def require(condition, message):
    if not condition:
        raise ValueError(message)


def equal_query(actual, expected, path=()):
    """Original points are exact; only aggregate means allow floating summation error."""
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            equal_query(actual[k], expected[k], (*path, k)) for k in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            equal_query(a, b, (*path, i)) for i, (a, b) in enumerate(zip(actual, expected))
        )
    if (
        len(path) == 3
        and path[0] in ("items", "buckets")
        and type(path[1]) is int
        and path[2] == "mean"
        and isinstance(actual, float)
    ):
        return (
            math.isfinite(actual)
            and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
        )
    return actual == expected


def fingerprints():
    paths = [
        Path(__file__),
        ROOT / "src/evdt/recovery/history_bundle.py",
        ROOT / "src/evdt/recovery/database.py",
        ROOT / "src/evdt/recovery/probe.py",
        ROOT / "src/evdt/monitoring/history_queries.py",
        ROOT / "src/evdt/monitoring/api.py",
        ROOT / "benchmarks/history.json",
        ROOT / "benchmarks/workloads.json",
        ROOT / "scripts/monitoring/history_browser_bridge.py",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }


def restore(checkpoint, output, *, port=18785, after_restore=None):
    before = fingerprints()
    record = {
        "passed": False,
        "Q10_W2_passed": False,
        "scope": "History-only independent restore; no queues or plant resumption",
        "source_sha256": before,
        "commands": [],
        "mean_comparison": "aggregate means only: rel/abs 1e-12; original values/identities/counts/metadata exact",
    }
    identifier = uuid.uuid4().hex
    project = "evdt-history-restore-" + identifier[:12]
    runtime = ROOT / ".runtime/history-restore" / identifier
    secret_dir = runtime / "secrets"
    secret_dir.mkdir(parents=True)
    (output / "restore-control.json").write_text(
        json.dumps({"project": project, "runtime_id": identifier}) + "\n"
    )
    created = False
    log = (output / "commands.log").open("w", encoding="utf-8")

    def run(argv, *, timeout=180, check=True):
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        log.write(json.dumps(argv) + "\n" + completed.stdout + completed.stderr + "\n")
        log.flush()
        record["commands"].append({"argv": argv, "exit_code": completed.returncode})
        if check and completed.returncode:
            raise RuntimeError("History restore command failed; inspect private commands.log")
        return completed.stdout.strip()

    def wait(predicate, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        raise RuntimeError("History restore readiness deadline exceeded")

    started = time.monotonic()
    try:
        reserve = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())["limits"][
            "host_disk_reserve_bytes"
        ]
        record["initial_host_disk_free_bytes"] = shutil.disk_usage(ROOT).free
        require(
            record["initial_host_disk_free_bytes"] >= reserve + 8 * 1024**3,
            "Restore lacks host disk reserve plus 8 GiB temporary headroom",
        )
        print("Verifying the history checkpoint before creating a fresh target", flush=True)
        # The bounded strict verifier validates this metadata before any service starts.
        raw = (checkpoint / "manifest.json").read_bytes()
        require(len(raw) <= 1024 * 1024, "Unbounded checkpoint manifest")
        metadata = json.loads(raw)["metadata"]
        manifest = verify(checkpoint, metadata["application_image_id"])
        metadata = manifest["metadata"]
        image = metadata["application_image_id"]
        require(
            run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]) == image,
            "Matching checkpoint image is unavailable",
        )
        record["checkpoint_manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        record["restored_application_image_id"] = image
        record["recorded_runtime_source_sha256"] = metadata["source_sha256"]
        record["assets"] = metadata["assets"]
        record["samples_per_asset"] = metadata["samples_per_asset"]
        # New credentials are never copied from the checkpoint or logged in argv.
        for name in ("admin_db", "read_db", "ingest_db", "registry_db", "replay_db", "metrics"):
            (secret_dir / name).write_text(secrets.token_urlsafe(32) + "\n")
        readers = {
            "readers": [
                {"label": "all", "token": secrets.token_urlsafe(32), "assets": metadata["assets"]},
                {"label": "bus-001", "token": secrets.token_urlsafe(32), "assets": ["bus-001"]},
            ]
        }
        (secret_dir / "readers").write_text(json.dumps(readers))
        original_secrets = (
            ROOT / ".runtime/p1-uplink" / uuid.UUID(metadata["run_id"]).hex / "secrets"
        )
        require(
            (original_secrets / "read_db").is_file()
            and (original_secrets / "readers.json").is_file(),
            "This qualification requires its original local credentials for explicit rotation-denial tests",
        )
        require(
            (secret_dir / "read_db").read_bytes() != (original_secrets / "read_db").read_bytes(),
            "Database credential did not rotate",
        )
        common = {
            "image": image,
            "pull_policy": "never",
            "read_only": True,
            "tmpfs": ["/tmp:size=32m,mode=1777"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": "256m",
            "pids_limit": 64,
            "networks": ["data"],
            "environment": {
                "EVDT_RUN_ID": metadata["run_id"],
                "DB_USER": "evdt_admin",
                "DB_PASSWORD_FILE": "/run/secrets/admin_db",
                "READERS_FILE": "/run/secrets/readers",
            },
            "secrets": ["admin_db", "read_db", "ingest_db", "registry_db", "replay_db", "readers"],
        }
        config = {
            "services": {
                "db": {
                    "image": metadata["postgres_image"],
                    "pull_policy": "never",
                    "mem_limit": "256m",
                    "pids_limit": 64,
                    "environment": {
                        "POSTGRES_USER": "evdt_admin",
                        "POSTGRES_DB": "evdt",
                        "POSTGRES_PASSWORD_FILE": "/run/secrets/admin_db",
                    },
                    "secrets": ["admin_db"],
                    "volumes": ["database:/var/lib/postgresql"],
                    "networks": ["data"],
                    "command": [
                        "postgres",
                        "-c",
                        "shared_buffers=32MB",
                        "-c",
                        "max_connections=20",
                    ],
                },
                "tool": {
                    **common,
                    "volumes": [
                        str(checkpoint) + ":/history:ro",
                        str(original_secrets / "read_db") + ":/old-read-password:ro",
                    ],
                },
                "api": {
                    **common,
                    "mem_limit": "192m",
                    "secrets": ["read_db", "readers", "metrics"],
                    "environment": {
                        **common["environment"],
                        "DB_USER": "evdt_read",
                        "DB_PASSWORD_FILE": "/run/secrets/read_db",
                        "METRICS_TOKEN_FILE": "/run/secrets/metrics",
                    },
                    "command": [
                        "uvicorn",
                        "evdt.monitoring.api:app",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        "8000",
                    ],
                    "networks": ["data", "host"],
                    "ports": [f"127.0.0.1:{port}:8000"],
                },
            },
            "networks": {"data": {"internal": True}, "host": {}},
            "volumes": {"database": {}},
            "secrets": {
                name: {"file": str(secret_dir / name)}
                for name in (
                    "admin_db",
                    "read_db",
                    "ingest_db",
                    "registry_db",
                    "replay_db",
                    "metrics",
                    "readers",
                )
            },
        }
        compose_file = runtime / "compose.json"
        compose_file.write_text(json.dumps(config) + "\n")
        prefix = ["docker", "compose", "-f", str(compose_file), "-p", project]

        def compose(*argv, **kwargs):
            return run([*prefix, *argv], **kwargs)

        def tool(action, *args, timeout=600):
            return compose(
                "run",
                "--rm",
                "--no-deps",
                "tool",
                "python",
                "-m",
                "evdt.recovery.database",
                action,
                *args,
                timeout=timeout,
            )

        require(
            not run(
                ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]
            )
            and not run(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=" + project,
                ]
            ),
            "Unique restore target already exists",
        )
        compose("config", "--quiet")
        created = True
        print("Starting an independent empty PostgreSQL cluster with fresh credentials", flush=True)
        compose("up", "-d", "db")
        wait(
            lambda: compose(
                "exec", "-T", "db", "pg_isready", "-U", "evdt_admin", "-d", "evdt", check=False
            ).endswith("accepting connections")
        )
        target_cluster = tool("cluster")
        require(
            target_cluster != metadata["source_cluster"],
            "Original cluster cannot be a restore target",
        )
        record["different_cluster"] = True
        record["same_cluster_guard"] = json.loads(
            tool("refusal", "--source-cluster", target_cluster)
        )
        require(
            record["same_cluster_guard"] == {"refused": "Refusing original source cluster"},
            "Same-cluster refusal failed",
        )
        record["empty_target_guards"] = json.loads(
            tool("empty-guards", "--source-cluster", metadata["source_cluster"])
        )
        tool("bootstrap", "--source-cluster", metadata["source_cluster"])
        print(
            "Restoring the binary archive and checking the complete database inventory", flush=True
        )
        verify(checkpoint, image)
        argv = [
            *prefix,
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
        ]
        with (checkpoint / "database.dump").open("rb") as source:
            completed = subprocess.run(
                argv, cwd=ROOT, stdin=source, capture_output=True, timeout=600
            )
        record["commands"].append({"argv": argv, "exit_code": completed.returncode})
        (output / "restore.stderr").write_bytes(completed.stderr)
        require(completed.returncode == 0, "History archive restore failed")
        restored = json.loads(tool("inventory"))
        (output / "restored-inventory.json").write_text(json.dumps(restored, sort_keys=True) + "\n")
        require(
            restored == json.loads((checkpoint / "inventory.json").read_bytes()),
            "Complete restored inventory differs",
        )
        events = len(metadata["assets"]) * metadata["samples_per_asset"]
        required_counts = {
            "m_observations": events,
            "m_sample_history": 20 * events,
            "m_receipts": events,
            "m_current": 20 * len(metadata["assets"]),
            "m_quarantine": 0,
            "m_registrations": len(metadata["assets"]),
            "m_source_clocks": len(metadata["assets"]),
        }
        require(
            all(restored["tables"][name]["rows"] == n for name, n in required_counts.items()),
            "Restored database volume differs from declared history scope",
        )
        counts = json.loads((checkpoint / "counts.json").read_bytes())["totals"]
        require(
            counts
            == {"events": events, "scalars": 20 * events, "receipts": events, "quarantine": 0},
            "Sealed count evidence differs from restored volume",
        )
        record["restored_table_counts"] = required_counts
        require(tool("cluster") == target_cluster, "Restore changed target cluster identity")
        record["complete_inventory_equal"] = True
        refusal = json.loads(tool("refusal", "--source-cluster", metadata["source_cluster"]))
        require(
            refusal == {"refused": "Restore target must be empty"}, "Nonempty target refusal failed"
        )
        record["nonempty_target_guard"] = refusal
        compose("up", "--no-deps", "-d", "api")
        base = f"http://127.0.0.1:{port}"

        def read(path, token=None):
            headers = {"Authorization": "Bearer " + token} if token else {}
            with urlopen(Request(base + path, headers=headers), timeout=10) as response:
                return json.load(response)

        wait(lambda: read("/health/ready")["ready"])
        token = readers["readers"][0]["token"]
        queries = json.loads((checkpoint / "queries.json").read_bytes())
        require(queries, "Missing representative history queries")
        for query in queries:
            require(
                equal_query(read(query["path"], token), query["response"]),
                "Representative restored history query differs",
            )
        record["representative_queries_equal"] = len(queries)
        record["restricted_roles"] = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "tool",
                "python",
                "-m",
                "evdt.recovery.probe",
                "permissions",
            )
        )
        old_token = json.loads((original_secrets / "readers.json").read_bytes())["readers"][0][
            "token"
        ]
        for path, credential, expected in [
            ("/v1/snapshot?asset_id=bus-001", old_token, 401),
            ("/v1/snapshot?asset_id=bus-001", None, 401),
        ]:
            try:
                read(path, credential)
            except HTTPError as exc:
                code = exc.code
                exc.close()
                require(code == expected, "Rotated or absent token failed for an unexpected reason")
            else:
                raise ValueError("Old/absent token was accepted")
        if len(metadata["assets"]) > 1:
            try:
                read(
                    "/v1/snapshot?asset_id=" + metadata["assets"][-1],
                    readers["readers"][1]["token"],
                )
            except HTTPError as exc:
                code = exc.code
                exc.close()
                require(code == 403, "Scoped restored reader failed for an unexpected reason")
            else:
                raise ValueError("Scoped restored reader leaked another asset")
        record["rotated_http_credentials_and_scope_checked"] = True
        elapsed = time.monotonic() - started
        record["restore_and_verification_s"] = elapsed
        record["within_900s"] = elapsed <= 900
        require(record["within_900s"], "History restore exceeded the unchanged 900-second deadline")
        full = (
            metadata["assets"] == [f"bus-{i:03}" for i in range(1, 11)]
            and metadata["samples_per_asset"] == 7200
        )
        record["Q10_W2_passed"] = full
        if after_restore:
            record["additional_checks"] = after_restore(compose, base, token, manifest)
            require(record["additional_checks"]["passed"], "Additional historical checks failed")
        record["passed"] = True
    except Exception as exc:
        record.update(passed=False, error=str(exc))
    finally:
        if created:
            try:
                compose("logs", "--no-color", check=False)
                compose("down", "--volumes", "--remove-orphans", timeout=120)
                record["cleanup"] = {
                    "containers": run(
                        [
                            "docker",
                            "ps",
                            "-aq",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ]
                    ),
                    "volumes": run(
                        [
                            "docker",
                            "volume",
                            "ls",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ]
                    ),
                }
                require(not any(record["cleanup"].values()), "Owned restore cleanup incomplete")
            except Exception as exc:
                record.update(passed=False, Q10_W2_passed=False, cleanup_error=str(exc))
        if fingerprints() != before:
            record.update(
                passed=False,
                Q10_W2_passed=False,
                error="Source changed during restore qualification",
            )
        record["phase_1_complete"] = False
        log.close()
        (output / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
        print(
            f"History restore passed: {record['passed']}; evidence: {output.relative_to(ROOT)}",
            flush=True,
        )
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--browser-control", type=Path)
    args = parser.parse_args()
    checkpoint, output = args.checkpoint.resolve(), args.evidence_dir.resolve()
    if (
        os.name == "nt"
        or not checkpoint.is_relative_to(ROOT / ".private/backups")
        or not output.is_relative_to(ROOT / "evidence/p1")
        or output.exists()
    ):
        parser.error(
            "Use the Linux launcher, a private canonical checkpoint and new evidence directory"
        )
    output.mkdir(parents=True)
    callback = None
    if args.browser_control:
        control = args.browser_control.resolve()
        if not control.is_relative_to(ROOT / ".runtime/dashboard") or not control.is_dir():
            parser.error("Use an existing private history-browser control directory")
        from history_browser_bridge import serve

        def callback(compose, base, token, manifest):
            return serve(control, checkpoint, base, token, manifest)

    return 0 if restore(checkpoint, output, after_restore=callback)["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
