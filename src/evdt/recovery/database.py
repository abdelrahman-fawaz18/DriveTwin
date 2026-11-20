"""Stable data/schema/privilege inventories and fresh-target restore preflight."""

import hashlib
import json
from pathlib import Path

from psycopg import errors, sql

from evdt.monitoring.repository import connection


def cluster(conn):
    return str(
        conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[
            "system_identifier"
        ]
    )


def preflight(conn, source_cluster):
    if cluster(conn) == source_cluster:
        raise ValueError("Refusing original source cluster")
    objects = conn.execute("""SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
        AND c.relkind IN ('r','p','v','m','S','f')""").fetchone()["n"]
    functions = conn.execute("""SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema')""").fetchone()["n"]
    types = conn.execute("""SELECT count(*) AS n FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname='public'""").fetchone()["n"]
    schemas = conn.execute("""SELECT count(*) AS n FROM pg_namespace
        WHERE nspname NOT IN ('public','pg_catalog','information_schema')
        AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%'""").fetchone()["n"]
    extensions = conn.execute(
        "SELECT count(*) AS n FROM pg_extension WHERE extname<>'plpgsql'"
    ).fetchone()["n"]
    if objects or functions or types or schemas or extensions:
        raise ValueError("Restore target must be empty")


def empty_guards(conn, source_cluster):
    preflight(conn, source_cluster)
    tested = []
    for statement in (
        "CREATE TYPE recovery_probe_type AS ENUM ('test')",
        "CREATE SCHEMA recovery_probe_schema",
    ):
        with conn.transaction(force_rollback=True):
            conn.execute(statement)
            try:
                preflight(conn, source_cluster)
            except ValueError as exc:
                if str(exc) != "Restore target must be empty":
                    raise
                tested.append(statement.split()[1].lower())
            else:
                raise RuntimeError("Nonempty restore target accepted")
    preflight(conn, source_cluster)
    return {"nonempty_refused": tested, "test_objects_rolled_back": True}


def bootstrap_roles(conn):
    conn.execute("CREATE ROLE evdt_forecast NOLOGIN")
    for role, file in [
        ("evdt_ingest", "ingest_db"),
        ("evdt_read", "read_db"),
        ("evdt_registry", "registry_db"),
        ("evdt_replay", "replay_db"),
    ]:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(Path("/run/secrets", file).read_text().strip())
            )
        )


def inventory(conn, schema="public"):
    """Called only against a sealed checkpoint or an idle restored database."""
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET DateStyle = 'ISO, YMD'")
    conn.execute("SET extra_float_digits = 3")
    tables = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (schema,)
    ).fetchall()
    result = {"tables": {}}
    for table in tables:
        name = table["tablename"]
        digest, count = hashlib.sha256(), 0
        with conn.cursor(name="backup_inventory") as rows:
            rows.execute(
                sql.SQL(
                    'SELECT to_jsonb(t)::text AS row FROM {} t ORDER BY to_jsonb(t)::text COLLATE "C"'
                ).format(sql.Identifier(schema, name))
            )
            for row in rows:
                digest.update(row["row"].encode() + b"\n")
                count += 1
        result["tables"][name] = {"rows": count, "sha256": digest.hexdigest()}
    queries = {
        "columns": "SELECT table_name,column_name,ordinal_position,data_type,udt_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position",
        "constraints": "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname",
        "indexes": "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname",
        "views": "SELECT viewname,definition FROM pg_views WHERE schemaname='public' ORDER BY viewname",
        "functions": "SELECT p.proname,pg_get_functiondef(p.oid) AS definition,pg_get_userbyid(p.proowner) AS owner,p.proacl::text AS acl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY p.proname,p.oid::regprocedure::text",
        "triggers": "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "acl": "SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner,c.relacl::text AS acl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname",
        "schema_acl": "SELECT nspname,pg_get_userbyid(nspowner) AS owner,nspacl::text AS acl FROM pg_namespace WHERE nspname='public'",
        "sequences": "SELECT sequencename,data_type,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
    }
    for key, query in queries.items():
        result[key] = conn.execute(query.replace("'public'", "%s"), (schema,)).fetchall()
    result["sequence_state"] = {}
    for entry in result["sequences"]:
        name = entry["sequencename"]
        result["sequence_state"][name] = conn.execute(
            sql.SQL("SELECT last_value,is_called FROM {}").format(sql.Identifier(schema, name))
        ).fetchone()
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "inventory",
            "cluster",
            "preflight",
            "bootstrap",
            "refusal",
            "empty-guards",
            "sealed",
        ],
    )
    parser.add_argument("--source-cluster")
    args = parser.parse_args()
    with connection() as conn:
        if args.action == "inventory":
            print(json.dumps(inventory(conn), sort_keys=True))
        elif args.action == "cluster":
            print(cluster(conn))
        elif args.action == "sealed":
            try:
                with conn.transaction(force_rollback=True):
                    conn.execute("CREATE TABLE recovery_must_not_exist(n int)")
            except errors.ReadOnlySqlTransaction:
                print(json.dumps({"readonly_write_denied": True}))
            else:
                raise RuntimeError("Source database is not sealed read-only")
        else:
            if not args.source_cluster:
                parser.error("--source-cluster required")
            if args.action == "empty-guards":
                print(json.dumps(empty_guards(conn, args.source_cluster)))
                return
            if args.action == "refusal":
                try:
                    preflight(conn, args.source_cluster)
                except ValueError as exc:
                    print(json.dumps({"refused": str(exc)}))
                    return
                raise RuntimeError("Unsafe target was accepted")
            preflight(conn, args.source_cluster)
            if args.action == "bootstrap":
                bootstrap_roles(conn)
            print("Fresh independent target verified")


if __name__ == "__main__":
    main()
