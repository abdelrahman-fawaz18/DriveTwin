# Testing the real queue against its disk boundaries

Status: the accelerated queue/storage fixture passed. Normal vehicle source/sender selection, live overflow/reconnect and deployment/backup lifecycle remain separate gates. See [ADR-0034](../adr/0034-queue-on-bounded-storage.md) and [the measured result](../verification/queue-storage.json).

## What this adds

An encoded limit counts the bytes in observation packets. A physical filesystem limit also contains the database's tables, indexes and temporary transaction journal. Passing packet arithmetic alone cannot establish that a real database fits. This fixture places the actual production SQLite queue on the previously qualified 64-MiB telemetry filesystem and places its fault status on the separate 8-MiB critical filesystem.

An original is one immutable observation packet. A witness is an independently retained description of that original: its identifier, byte fingerprint, session, sequence and time. After reopening the queue in a new container, the verifier compares every surviving original with its witness and the expected twenty signal values/units. It also compares the complete logical database contents and schema. This detects changed values even if an incorrect implementation recomputes a packet fingerprint.

## Stages and actual results

| Stage | Generated originals | Physically queued | Unconfirmed evictions | Confirmed drops | Database file bytes |
|---|---:|---:|---:|---:|---:|
| Nominal storage horizon | 1,800 | 1,800 | 0 | 0 | 15,831,040 |
| Encoded capacity | 4,096 | 4,096 | 0 | 0 | 35,954,688 |
| Ten additional originals | 4,106 | 4,096 | 10 | 0 | 35,971,072 |
| Reopened after disk-full transaction failure | 4,106 | 4,096 | 10 | 0 | 35,971,072 |
| Explicit never-published loss reconciliation | 4,106 | 4,096 | 0 | 10 | 35,971,072 |

Each packet is exactly 8,192 bytes. Values and size-padding annotations are synthetic planning inputs, inserted in accelerated bursts. The first row represents the storage needed for thirty minutes at one packet per second; it is not a thirty-minute wall-time benchmark or a calibrated physical simulation. No broker or server is involved.

The fixture fills remaining telemetry space with its own filler and attempts a normal queue insertion. SQLite returned error 13, `SQLITE_FULL`. After removing only that filler, a fresh container verified that all earlier originals, loss records, counters, sequence and complete logical schema/data inventory were unchanged. This checks transaction rollback: a failed insertion must not partly evict an original or advance its sequence.

The critical fault record retained first reason `telemetry_sqlite_full`, latest reason `critical_full` and count two. Two valid preallocated copies occupied 8,192 allocated bytes and reopened with zero available critical space. This is fault visibility, not a command-result reservation or permission to execute a command.

Eviction does not automatically mean confirmed loss: an original might already be traveling to the server. Here the helpers have no network, sender or server, so the fixture can independently establish that its ten evicted originals were never published. Only then does it record a trusted local loss assertion. Production reconciliation must separately establish those external facts. Accepted and rejected counts remain zero throughout this fixture.

## Reproduce from Windows PowerShell

Start in `<checkout>` with the qualified Ubuntu Docker Engine running and the ignored local development configuration present.

```powershell
python scripts/dev.py check
python scripts/monitoring/qualify_queue_storage.py --evidence-dir evidence/p1/CHOOSE-A-NEW-QUEUE-STORAGE-ATTEMPT
```

1. The first command checks source, contracts, formatting, types, dashboard build, privacy and secrets. The recorded source passed 404 Python and 22 frontend tests, six strictly checked Python modules and unchanged 339+81 contract outcomes per language.
2. The second builds the locked application image and checks its packaged source/contracts/catalog against the current tree. It invokes the scoped Ubuntu administrator provisioner only to create and later remove its new owned disk devices/volumes. Application helpers run as ordinary user ID 10001, without a network or elevated capabilities, with a read-only container root.
3. Require the parent `qualification.json` to pass. Its child `services/qualification.json` must also pass queue integration, packaged-source and critical-fault checks, retain matching captured source fingerprints and report empty owned container/volume/device cleanup. The controller checks these conditions before accepting the result. A partial directory or a positive log message alone is not acceptance.

Use a new evidence directory for each attempt; preserve earlier failures. Raw packets, identities, configuration, logs and reports remain ignored/private. See [disk ownership and cleanup](persistent-disk-boundaries.md), [overflow accounting](queue-overflow-accounting.md) and [fault-status behavior](critical-fault-status.md).

The tests do not establish concurrent normal sender behavior, reboot/power-loss recovery, automatic attachment, command-result storage, backup compatibility for a selected new provider, full live W0/W1 resources, retention or complete Phase 1 acceptance.
