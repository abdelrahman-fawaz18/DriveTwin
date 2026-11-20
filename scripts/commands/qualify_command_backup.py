"""Restore a stopped command checkpoint into a separate, empty PostgreSQL cluster."""

import json
import secrets

from qualify_connected_speed_limit import digest, prepare, read, require, running, wait_ready, write
from qualify_pending_commands import CLAIM, address, expect, verify_mission, verify_pending

PROBE = """
import json
import sys
from pathlib import Path
from psycopg import sql
from evdt.monitoring.database import connection
from evdt.recovery.database import bootstrap_roles, cluster, inventory, preflight

action = sys.argv[1]
with connection() as conn:
    if action == 'capture':
        print(json.dumps({'cluster':cluster(conn),
                          'inventory':{s:inventory(conn,s) for s in ('public','commanding')}}, sort_keys=True))
    elif action == 'bootstrap':
        preflight(conn, sys.argv[2])
        bootstrap_roles(conn)
        secret = Path('/run/secrets/command_db').read_text().strip()
        conn.execute(sql.SQL('CREATE ROLE evdt_command LOGIN CONNECTION LIMIT 1 PASSWORD {}').format(sql.Literal(secret)))
        print(cluster(conn))
    else:
        try:
            preflight(conn, sys.argv[2])
        except ValueError as error:
            print(str(error))
        else:
            raise RuntimeError('Unsafe restore preflight was accepted')
"""


def probe(compose, *args):
    return compose(
        "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "migrate", "-c", PROBE, *args
    )


def verify_backup(compose, shell, runtime, output, run, tokens, project, profile):
    arbitrated = profile == "arbitration"
    seed, reads = verify_pending(compose, tokens, run, "sealed-cancel" if arbitrated else "replace")
    if not arbitrated:
        reads.update(verify_mission(address(compose), tokens, run, empty_queue=True))
    write(output / "original-reads.json", reads)

    print("Stopping command writers and saving the database checkpoint", flush=True)
    compose("stop", "command-api", "api")
    compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "evdt_admin",
        "-d",
        "evdt",
        "-c",
        "ALTER DATABASE evdt SET default_transaction_read_only=on",
    )
    saved = json.loads(probe(compose, "capture"))
    require(
        probe(compose, "refuse", saved["cluster"]).strip() == b"Refusing original source cluster",
        "Original database was not rejected as a restore target",
    )
    write(output / "original-inventory.json", saved)
    archive = output / "commands.dump"
    archive.write_bytes(
        compose(
            "exec",
            "-T",
            "db",
            "pg_dump",
            "-U",
            "evdt_admin",
            "-d",
            "evdt",
            "--format=custom",
            binary_output=True,
        )
    )
    fingerprint = digest(archive)
    require(archive.read_bytes().startswith(b"PGDMP"), "Backup is not a PostgreSQL custom archive")
    compose("stop", "db")

    target = runtime / "restore"
    target.mkdir()
    restored_tokens, env, override = prepare(target, output, run)
    configuration = read(override)
    configuration["services"]["migrate"]["secrets"] = ["command_db"]
    write(override, configuration)
    prefix = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "-p",
        project + "-restore",
        "-f",
        "ops/compose/compose.monitoring.yaml",
        "-f",
        "ops/compose/compose.commands.yaml",
        "-f",
        str(override),
    ]
    if arbitrated:
        env["COMMAND_ISSUER"] = "dispatch-coordinator"
        prefix += ["-f", "ops/compose/compose.arbitration.yaml"]
        for role in ("thermal", "energy"):
            (target / "secrets" / ("command_" + role)).write_text(secrets.token_urlsafe(36) + "\n")

    def restore(*argv, **kwargs):
        return shell(*prefix, *argv, environment=env, **kwargs)

    print("Restoring into a separate database and comparing the saved records", flush=True)
    try:
        restore("up", "-d", "--wait", "--wait-timeout", "45", "--pull", "never", "db")
        restored_cluster = probe(restore, "bootstrap", saved["cluster"]).decode().strip()
        require(
            bool(restored_cluster) and restored_cluster != saved["cluster"],
            "Target cluster is not independent",
        )
        require(digest(archive) == fingerprint, "Checkpoint changed before restore")
        restore(
            "exec",
            "-T",
            "db",
            "pg_restore",
            "-U",
            "evdt_admin",
            "-d",
            "evdt",
            "--single-transaction",
            "--exit-on-error",
            input_data=archive.read_bytes(),
        )
        recovered = json.loads(probe(restore, "capture"))
        write(output / "restored-inventory.json", recovered)
        require(
            recovered["cluster"] == restored_cluster
            and recovered["inventory"] == saved["inventory"],
            "Restored data, privileges, or sequence state differs from the checkpoint",
        )
        require(
            probe(restore, "refuse", saved["cluster"]).strip() == b"Restore target must be empty",
            "Populated restore target accepted a second restore",
        )
        restore("up", "-d", "--no-deps", "--pull", "never", "command-api")
        base = address(restore)
        wait_ready(base)
        for path, state in reads.items():
            expect(base, tokens, path, 401)
            require(
                expect(base, restored_tokens, path, 200) == state,
                "Restored API changed a saved record",
            )
        claim = {**running(4), "fence_protocol": "evdt-fence/1"} if arbitrated else running(4)
        expect(base, restored_tokens, CLAIM, 204, "executor", claim)
        # A normal command-role connection can read restored records but cannot rewrite them.
        acl_probe = """
from psycopg.errors import InsufficientPrivilege
from evdt.command_journal import connection
with connection() as conn:
    for statement in ('DELETE FROM commanding.requests', 'DELETE FROM commanding.mission_intents',
                      'UPDATE commanding.pending_changes SET recorded_at=recorded_at',
                      'SELECT * FROM public.m_observations'):
        try:
            with conn.transaction():
                conn.execute(statement)
        except InsufficientPrivilege:
            continue
        raise RuntimeError('Restored command role had excess permissions')
"""
        restore("run", "--rm", "--no-deps", "-T", "command-api", "python", "-c", acl_probe)
    finally:
        restore("down", "--volumes", "--remove-orphans")
        label = "label=com.docker.compose.project=" + project + "-restore"
        require(
            not any(
                shell("docker", *args, "--filter", label).strip()
                for args in (("ps", "-aq"), ("volume", "ls", "-q"), ("network", "ls", "-q"))
            ),
            "Restore target left owned resources",
        )
    return {
        "backup_profile": profile,
        "seed_checks": seed,
        "archive_sha256": fingerprint,
        "archive_bytes": archive.stat().st_size,
        "separate_cluster": True,
        "inventory_exact": True,
        "source_and_occupied_restore_refused": True,
        "restored_views": len(reads),
        "old_credentials_denied": True,
        "handoff_refused_after_restore": True,
        "command_role_denials": 4,
        "restore_cleanup_passed": True,
        "command_tables": {
            k: v["rows"] for k, v in saved["inventory"]["commanding"]["tables"].items()
        },
    }
