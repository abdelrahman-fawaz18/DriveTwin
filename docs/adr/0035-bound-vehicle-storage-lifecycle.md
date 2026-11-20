# ADR-0035: Explicit binding and temporary vehicle-storage lifecycle

Date: November 4, 2026. Status: physical fixture and shared checks passed. Default source/sender selection remains unchanged.

## Decision

A normal queue open must verify its storage before creating or updating anything. Introduce internal `vehicle-queue-storage/1.0.0` binding metadata: exact run/asset/source, selected wire/catalog pair, immutable encoded/audit limits, independently checked telemetry/critical device identities and filesystem capacities, physical image budgets, unique owner and Ubuntu boot identity. Keep the existing 32-MiB encoded, 64-MiB telemetry and additional 8-MiB critical budgets. A smaller explicitly requested capacity is labeled `reduced-test`, never nominal horizon acceptance.

Use fixed container mount locations `/spool` and `/critical` as deployment interfaces, with no sensor or asset hardcoding in the provider. Verify exact ext4 mount entries/root/restricted options, kernel device identity, measured filesystem capacity and current boot. Refuse fallback directories, missing mounts, overmounts, a shared device or changed binding. The critical filesystem retains the canonical binding and a fingerprint of the initialized SQLite schema. Normal opens also use read-only integrity/schema checks before the existing queue constructor; they cannot silently recreate missing storage or repair an empty/incomplete database.

Initialization is explicit and only for unused storage. Create the preallocated identity-bound fault latch and actual queue, synchronize the schema/binding records and their directory, then verify readiness. Refuse existing or partial initialization and preserve its state for inspection. This is not an atomic distributed transaction across filesystems; a partial attempt requires explicit recovery. Normal queue operations require both valid fault copies. A latched fault remains visible across reopening; no reset is introduced. Storage-class SQLite/capacity/I/O failures attempt to update the independent preallocated fault record after transaction failure, preserve the original exception and expose critical-status failure if that update also fails. Ordinary database lock contention does not itself latch a disk failure.

## Scoped local lifecycle

Extend the already qualified owned-volume provisioner with an explicit temporary serving mode. It creates only its new closed-namespace files/devices/volumes, verifies packaged source, initializes/reopens them in ordinary containers and publishes private readiness metadata. It accepts only an exact owner-bound release record and has a bounded lifetime. It does not expose an arbitrary administrator command interface or install a daemon. External application helpers must finish before release; the existing cleanup refuses referenced volumes or changed device/file/ownership associations.

This mechanism serves local qualification. Boot identity intentionally expires the binding after Ubuntu restarts. Automatic reattachment, deployment recovery, power-loss behavior and backup migration are not implemented by accepting a stale binding. The two bounded filesystems share the host's underlying drive; they isolate storage budgets, not hardware failures. Checksums detect changed bytes, not a malicious administrator who can rewrite metadata and disks.

## Actual candidate proof and remaining scope

Ordinary containers initialized/reopened the selected disks and refused missing telemetry/critical mounts, stale boot, changed device and changed durable binding. One admitted original survived fresh reopening, actual telemetry exhaustion, a failed normal insertion returning `SQLITE_FULL` (13), reopening while still full, telemetry recovery and another reopening. Original bytes, ID/hash, complete queue metadata/sequence and counts were unchanged. The normal bounded-queue wrapper retained a two-copy sticky `telemetry_sqlite_full` fault. Captured source matched and all owned resources were removed. Earlier failed controller attempts remain private and unaccepted.

The initial lifecycle gate did not select the provider in the normal source/sender factory; the separately recorded follow-up below adds that explicit selection. Broker overflow/reconnect diagnostics, original backup-format changes and full W0/W1/Q-12/E4/E5 remain open. See [the worked guide](../setup/bound-vehicle-storage.md) and [initial sanitized results](../verification/vehicle-storage.json).

Shared checks passed 446 Python / 22 frontend tests, seven strictly checked Python modules and unchanged 339+81 contract outcomes per language, with matching captured source. Privacy/secret checks are also required against staged bytes before the local commit.


Follow-up: explicit normal factory selection and actual authenticated internal HTTP storage diagnostics now passed in ordinary containers, including full telemetry failure/reopening. Missing/empty configured binding refuses legacy fallback. API/System fields are implemented but their new-provider proxy/browser, broker overflow/reconnect, fleet/backup/reboot gates remain open. Shared checks passed 468 Python / 22 frontend tests with seven strict modules and unchanged contracts. Historical lifecycle results remain scoped to their earlier exact image. See [the follow-up record](../verification/storage-factory-metrics.json).
