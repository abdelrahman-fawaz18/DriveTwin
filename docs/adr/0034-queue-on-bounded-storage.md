# ADR-0034: Actual queue transactions on bounded persistent storage

Date: November 4, 2026. Status: accelerated actual queue/storage qualification passed with matching source and empty owned cleanup. Default vehicle sender remains unchanged.

## Explicit storage workload

Freeze `queue-storage-integration/1.0.0` in `benchmarks/storage-queue.json`. Use normal production `Spool` validation/transactions and its opt-in oldest-first strategy on the qualified 64-MiB telemetry filesystem, alongside the independent 8-MiB critical filesystem and preallocated fault status. Keep 32 MiB encoded capacity, exact 8,192-byte packets, twenty initial signals and all existing limits. The first 1,800 originals represent the nominal thirty-minute storage horizon at 1 Hz. Then fill 4,096 packets and add ten overflow originals. These are accelerated insertion bursts with explicit synthetic planning values and valid bounded size-padding annotations, not a wall-time workload, calibrated plant, broker delivery or server-persistence claim. Full W0/W1 remain sixty warmup plus 1,800 measured wall seconds.

The controller and ordinary application container retain each committed original's ID/hash/session/epoch/sequence/time witness independently from the later database read. Check every reopened original byte fingerprint, all sample values/units, metadata, contract validity, order and counts against those witnesses. Check every eviction ID/hash/time and exact oldest ordering. Initially evicted originals stay unconfirmed/pending. The fixture has no sender/server and its containers have no network, so its explicitly trusted reconciliation can confirm that those evicted originals were never published; retain the audit reference that establishes their disposition.

## Actual filesystem failure and fault visibility

After encoded capacity/overflow checks, fill the remaining telemetry filesystem with only an owned filler. A further normal queue insertion must encounter the actual SQLite full/I/O failure class, not merely the encoded limit. Its transaction must preserve all earlier data/schema/counters/sequence/eviction evidence. Record a sticky critical fault outside telemetry. Remove only the filler, reopen the queue in a fresh ordinary container and compare the complete logical SQLite inventory and every original witness. Fill critical independently, update/reopen its preallocated status and preserve the first queue fault while recording the later critical fault.

Compare an independently streamed SQLite content/schema inventory before/after failure and reopen. Physical database pages/free pages are diagnostics, not a claim that a database file is identical byte for byte. Preserve original packet bytes exactly. Do not infer transient journal headroom solely from packet arithmetic: the kernel-enforced filesystem bound applies throughout every successful transaction, and all required committed originals must actually survive.

## Source and deployment scope

Use an exact local locked application image with current source/contract/catalog contents independently compared before any queue work. Current source is mounted read-only at the same package path and compiled without reusing host bytecode caches. Check image/source fingerprints inside each queue helper as well. Normal application UID/capability/network/root-filesystem restrictions and exact volume/device ownership cleanup remain unchanged. A larger bounded helper memory/time allowance is a storage-fixture setting, not a changed live application resource target. Preserve failures and partial evidence.

This gate proves queue policy and physical storage coupling. Selecting the provider in running vehicle/source/sender configurations, authenticated overflow/fault/reconnect diagnostics, startup/reattachment, backup compatibility, retention, full live resources and final release review remain separate. Positive command execution/result reservation is still a P3 capability; Phase 1 continues to reject operational commands.

The public Windows controller builds the locked image and validates child acceptance, required source fingerprints and empty cleanup. Shared checks passed 404 Python / 22 frontend tests, six strict Python modules and unchanged contract outcomes. The actual failed insert returned SQLite error 13; complete logical inventory and every surviving original matched after reopening. See [measured results](../verification/queue-storage.json) and [reproduction and explanation](../setup/queue-on-bounded-storage.md).
