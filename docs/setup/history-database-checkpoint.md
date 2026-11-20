# Source-backed history loading and private checkpoints

Status: a two-bus, 60-event-per-bus source/loading/checkpoint pilot passed on matching source. The complete ten-asset W2 database, sealed checkpoint, independent restore, full-volume queries and actual multipage browser downloads also passed. Consult [Phase 1 status](../monitoring-status.md) before treating an implemented feature as accepted.

## From simulation to stored history

The history fixture executes an actual motion/battery/thermal plant for each bus. It registers that plant's recipe before the first observed packet. Registration is a recorded binding between the source identity, sensor definitions, model inputs and code fingerprints. Observed wall timestamps must follow registration; the loader does not bypass this rule to admit old unregistered packets.

Generation runs faster than real time and writes originals to files. The registry records an initial zero simulated clock and the actual completed simulated time; this is an offline history fixture, not a live heartbeat stream. The loader passes original bytes through the existing `repository.store` operation as the normal restricted ingest database role. The ordinary validator, ownership/clock rules, atomic transaction, scalar-history trigger, current-value ordering and persistence receipt still apply. A trigger is a database function automatically run for an insert: here it derives individual history rows from an accepted original packet. No direct scalar-row seeding, extra migration or replacement validation path is used.

The independent verifier streams all stored originals back and checks exact bytes, fingerprints, JSON values, identity, sequence and simulated time. It compares every scalar and its copied provenance with the corresponding original sample. It also checks registered recipe bytes, final current readings, complete accepted receipts and reader scope. It inventories all durable table contents, schema, permissions and sequence state before and after retrying one original per asset. Equal inventories establish that duplicate handling left durable state unchanged. Source and loader containers have explicit memory bounds; successful exits and out-of-memory flags are checked before owned removal.

The first loading pilot produced 120 events / 2,400 scalar readings across two independent source executions. Both normal loaders, complete-value audits, original retries and empty owned cleanup passed. It is not complete W2 volume. W2 requires ten independent 7,200-event executions: 72,000 original packets and 1,440,000 scalar rows. Identical plant parameters remain explicitly identical; separate execution does not establish fleet diversity or physical calibration.

## Reproduce from Windows

Use PowerShell in `<checkout>`.

1. Run the shared checks:

   ```powershell
   python scripts/dev.py check
   ```

2. Exercise the small source/loading/checkpoint path with a new private output directory:

   ```powershell
   python scripts/linux.py --timeout 1800 -- python3 scripts/monitoring/extension_fixture.py --history-volume --history-samples 60 --history-assets 2 --evidence-dir evidence/p1/CHOOSE-A-NEW-HISTORY-PILOT
   ```

3. Only after that path is qualified, select the complete history fixture:

   ```powershell
   python scripts/linux.py --timeout 7200 -- python3 scripts/monitoring/extension_fixture.py --history-volume --history-samples 7200 --history-assets 10 --evidence-dir evidence/p1/CHOOSE-A-NEW-W2-ATTEMPT
   ```

   `--history-samples` is the number of simulated one-second observations per asset, not the measured wall-time duration. `--history-assets` selects the count of separate source executions. The launcher timeout bounds the entire process; it is not a benchmark measurement interval. All sources belong to one recording identity. Sources load sequentially to bound memory. The full fixture can take tens of minutes because each original uses normal admission and storage.

4. Read `qualification.json`. A pilot may pass its declared scope while `W2_database_passed` remains false. For complete W2 require the top-level pass, ten passing source/physical audits and loaders, all expected original/scalar/receipt counts, no quarantine, full inventory equality across retries, a sealed checkpoint, matching captured source fingerprints and empty owned container/volume cleanup. Keep all failed/incomplete attempts; existing output directories are refused.

## What the checkpoint contains

After all sources/loaders finish, the controller captures frozen representative plot/summary requests and responses. It stops the registry, seals new database connections read-only and proves an attempted write is denied. PostgreSQL's `pg_dump` tool then saves a binary database archive. Binary bytes go directly to a file without shell text conversion. Inventories before and after the dump must match.

The private `evdt-history-backup/1.0.0` directory contains the database archive, complete integrity inventory, counts, frozen query results, run policy, all ten map contexts, reviewed contract/catalog/migration artifacts, and each included source's original packets, summary, actual recipe and physical-step trace. It excludes configured credentials, account data and vehicle queues. Source/image fingerprints and the base Git commit are recorded; a dirty working-tree flag makes uncommitted qualification code explicit. A manifest lists every file's size and SHA-256 fingerprint, and final verification rejects changed/missing/unexpected files, links, unsafe paths, duplicate manifest keys, malformed scope and a wrong application image. SHA-256 detects changed content; an unsigned local manifest is not proof of an untrusted publisher's identity.

The checkpoint appears under ignored `.private/backups/UNIQUE-OWNED-PROJECT/history`. An unfinished directory ends in `.partial`. The report records its actual path, manifest fingerprint, bytes and file count. Raw sources, diagnostics and backups remain private and ignored; Git stores source code and sanitized evidence summaries. Same-disk private checkpoints do not establish offsite recovery.

This new format is explicitly history-only. The existing `evdt-backup/1.0.0` development format and its two pending vehicle queues stay unchanged. Saving a history checkpoint does not pass restoration, resume vehicle queues, restore an in-memory physical model or prove Q-10. The separate restore qualifier now passed these requirements at W2 volume: independent empty cluster, fresh credentials, complete inventories and representative reads, original/nonempty-target refusals and the unchanged 900-second deadline. See the [query/restore guide](history-query-and-restore.md).

Query timing, independent five-signal reductions/summaries and actual multipage browser JSON/CSV downloads passed as separately measured gates; see the [sanitized acceptance record](../verification/history-acceptance.json). Phase 1 live W0/W1 remain exactly 60 seconds of warmup plus 1,800 measured wall seconds. No one-hour live Phase 1 benchmark is part of this history procedure. See [ADR-0028](../adr/0028-coordinated-history-seeding.md).
