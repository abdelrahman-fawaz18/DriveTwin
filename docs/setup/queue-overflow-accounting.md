# Understanding telemetry eviction and loss accounting

The explicit queue-policy component passed shared and focused tests. Later integration is recorded in the [bounded source/Sender check](bounded-outage-and-flow-control.md). The local release uses the normal queue with this optional policy disabled. See [current Phase 1 status](../monitoring-status.md) and [ADR-0031](../adr/0031-durable-queue-overflow-accounting.md).

## What changes when a queue fills

A queue keeps an original event until a matching server receipt confirms its outcome. Its **encoded limit** counts packet bytes. A **physical disk budget** also has to include database pages, indexes, audit records and temporary transaction files. These are different limits. The planned per-bus budgets remain 32 MiB encoded telemetry inside 64 MiB physical telemetry storage, with another 8 MiB for critical status/results. MiB means 1,048,576 bytes.

The existing default queue refuses an extra event when full and applies backpressure: its caller must stop or pause explicitly. That tested behavior is preserved. The new opt-in `oldest-first/1.0.0` policy can instead remove the oldest unacknowledged telemetry when space is needed. It first records exactly which event was removed and then inserts the new event in the same database transaction. A transaction either commits all of these changes or recovers the previous state. The ledger is a durable table of those eviction records.

For example, suppose a small test queue contains events A and B. Adding C requires space. The policy records A's ID, original-byte hash, source epoch/sequence, observation/simulated time and eviction time, removes A's queued bytes and inserts C. If the insert fails or the process crashes before commit, reopening recovers A and B. Tests exercise an actual process exit at that point.

## Eviction is not proof of loss

The sender might already hold A when its queue row is removed. A can still reach the server. Its ledger entry starts **unconfirmed**. That means its final outcome is unknown, not silently assumed lost.

| Count | Meaning |
|---|---|
| `queued` | Original packets still available for sending |
| `evicted_unconfirmed` | Evicted originals whose terminal outcome is not settled |
| `pending` | Queued plus evicted-unconfirmed events |
| `accepted` / `rejected` | Outcomes established by matching validated server receipts |
| `dropped` | Loss explicitly confirmed by a trusted reconciliation with an audit reference |
| `evicted_lifetime` | All historical evictions, including events later accepted; a separate diagnostic |
| `generated` | Committed unique events across producer sessions |

The disjoint accounting rule is `generated = accepted + rejected + dropped + pending`. A retry does not create another unique event. The vehicle's `rejected` receipt outcome corresponds to a terminal application rejection; receiver-side quarantine classifications remain separately recorded.

A late matching receipt settles an evicted event exactly once. A wrong asset, run, source, event ID or original-byte hash cannot settle it. A trusted local loss confirmation must first establish stopped publication, settled in-flight work and an authoritative inventory excluding the event. The queue method does not prove those external facts. The stored audit reference makes that assertion reviewable. If a later valid receipt contradicts it, both the old assertion and receipt remain while current counts are corrected.

The audit has a finite, durably bound capacity. Once another eviction cannot be recorded, it refuses that operation and preserves old entries. It never silently deletes loss evidence to make space. Actual critical-storage exhaustion and command-refusal integration are separate pending tests; Phase 1 has no operational command acceptance.

## Where the implementation lives

`src/evdt/monitoring/spool.py` owns original-byte encoding, queue identity, sequence and receipt transactions. Its optional `capacity_policy` selects the strategy. `spool_capacity.py` implements `DropOldestPolicy`, durable policy binding, eviction records and reconciliation. Default construction preserves the legacy refusal policy. Only a fresh unused queue can adopt the new strategy; changing settings on reopen or reopening without its bound policy fails explicitly. No schema/catalog/wire format was changed.

`eviction_audit(limit=1000, after=0)` returns a bounded ordered local audit page. The last `audit_sequence` is the next page's `after`. `confirm_evicted_loss(event_id, digest, audit_reference)` is a trusted local audit operation, not an HTTP route or remote vehicle command. `counts()` checks the accounting invariant before returning opted-in statistics. Admitted numeric times retain their JSON value and numeric kind, including fractional values and signed zero; the existing portable safe-integer restrictions remain enforced.

## Reproduce the component checks

Run the queue checks in Windows PowerShell from `<checkout>`.

```powershell
python scripts/dev.py check
```

The shared command builds from the existing dependency locks and runs all tests, formatting, types, contract comparisons, build and privacy/secret checks. It now strictly checks the new policy module as well. The queue tests include oldest order across sessions, original hashes/values, reopen/configuration/legacy guards, malformed and individually oversized input without eviction, finite audit exhaustion, transaction failure, real process-crash recovery, wrong/duplicate/late receipts, explicit loss confirmation/correction, bounded audit paging and admitted numeric times. Raw reports stay ignored under `.runtime/dev-checks/`.

The current recorded run passed 362 Python / 22 frontend tests and unchanged 339+81 contract outcomes per language. This is a component result. It does not claim a persistent filesystem quota, an eight-MiB critical reserve, a live overflow/reconnect test, full W0/W1 or complete Phase 1 acceptance.


## Consistent counts during simultaneous delivery

A producer can add an original while the sender applies a receipt and the dashboard requests counts. A database snapshot is a view of one committed moment. The queue now explicitly keeps all count queries in one SQLite read transaction, including the legacy mode. Without that boundary, one response could combine an earlier pending count with a later accepted count, or falsely report broken accounting.

Two deterministic tests interleave a real matching receipt transaction between the count queries. With an immediate writer lock deadline, the concurrent commit is refused while the reader retains the coherent earlier counts; retrying after that read succeeds. Both legacy and oldest-first modes pass. Temporarily removing only the snapshot boundary in memory makes both tests fail with the original mixed-count behavior. No stored schema, packet, policy or numeric limit changes. Current shared checks passed 406 Python / 22 frontend tests and unchanged contracts. Historical review manifests remain unchanged; queue-count-core.json records the exact successor. This closes a concurrent read defect; it does not qualify the normal bounded-disk sender integration.
