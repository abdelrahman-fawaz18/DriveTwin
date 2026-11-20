# ADR-0017: Coordinated development checkpoints and isolated recovery

Status: selected for P1-D3; qualification results belong in the verification record.

## Decision and consistency boundary

A checkpoint saves one stopped application's PostgreSQL database, two durable SQLite queues, and the exact signal dictionaries, schemas, migration files, trusted run policy and map contexts. Stop producers/publishers/registry/receiver, then make new source database connections read-only. Database and queue snapshots must reconcile before and after copying. This is a coordinated development backup, not a concurrent online database/edge snapshot.

Use the pinned PostgreSQL 18 tools already in the database image: `pg_dump --format=custom --no-owner`, followed by `pg_restore --no-owner --single-transaction --exit-on-error` into a fresh instance. Preserve object permissions. Bootstrap the same application role names with new passwords; never back up cluster-wide accounts/password hashes. A transaction applies all restore statements together or rolls them back on failure. Archive bytes use binary file handles, never terminal/text redirection. [PostgreSQL dump](https://www.postgresql.org/docs/18/app-pgdump.html), [restore](https://www.postgresql.org/docs/18/app-pgrestore.html), [SQLite backup](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup).

## Bundle and target guards

Backup format `evdt-backup/1.0.0` is a private directory with manifest, file sizes/SHA-256 fingerprints, source cluster identity, source code fingerprints/commit, exact application image identity, pinned PostgreSQL image/version, checkpoint dispositions and creation time. Assemble under `.partial`, validate, then rename to the final directory. Refuse overwrites, links, special files, missing/extra files, unsafe paths, changed content, unsupported bundle versions and mismatched application images. These checks detect accidental corruption; they are not authentication against an attacker who can replace both contents and manifest. Only restore locally trusted backups.

Restore uses another Compose project, database cluster, volumes, networks and credentials. Refuse the source cluster and nonempty targets before role/schema changes. Do not use `--clean`, database creation from the archive, migrations that could rewrite recovered records, or cluster-global account restoration. Mount archived dictionaries read-only into the matching application image; preserve one canonical source tree. No original recording timestamp is refreshed by restoration. Dynamic displayed age/freshness can naturally differ.

## Acceptance and limits

Use independently stepped sources with 30 events each. Deliver exactly 25 original queued packets per bus through the real receiver and durable receipts; retain five pending in each queue. Compare all table row counts/content hashes, columns, constraints, functions, triggers, views, indexes, sequences including called-state, ownership and permissions before/after restore. Compare all scalar exports, current observations, catalogs, contexts and replay original bytes. Stop the original services before restored reads. Verify restricted roles and rejected old credentials, then resume only queue transport/receiver and reconcile 60 original events with zero pending. Queue transport retries do not imply restoring SUMO's in-memory physical state.

Record backup size/time and restore-to-query/backlog times. A zero unexplained-loss outcome in this stopped fixture is not a universal zero-second recovery point objective. The full W2-sized 15-minute Q-10 target, periodic backup schedule, retention, offsite encrypted storage, host/disk disaster recovery, online multi-edge coordination, rollback across new application versions and physical plant state resumption remain separate gates. Matching images must still be available locally; the bundle does not include an operating system or image archive. Source generation stops before the checkpoint; application history and clocks remain auditable.


## November 4 fixture ownership correction

The final normal-queue compatibility check found that a direct probe mount could create the restored second queue volume without Compose labels, escaping label-only cleanup. The corrected fixture creates the stopped sender through Compose first, verifies exact ownership, rejects preexisting target resources before its cleanup block, and checks exact queue-volume absence afterward. Application runtime, backup format, schemas and image guards are unchanged. An unsupported `create --no-deps` attempt remains failed; the separately authorized supported-command attempt and independent cleanup passed. See the [Phase 1 closeout](../verification/monitoring-acceptance.md).
