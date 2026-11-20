# Sustained outage and queue capacity

## Run the current release recovery check

P6-01 Q-07 uses one paced bus and the normal disk queue. The runner starts with 60 confirmed messages, then stops the broker and ingestion for at least 300 measured seconds. The bus and sender stay active. The registry continues accepting clock updates. During the outage, the queue must grow while the stored observation count stays flat.

The runner starts both delivery services and waits for ingestion to confirm its new broker subscription. It checks that the queue empties within 120 seconds from the start of restoration while the source is still running. It also records the interval from confirmed receiver readiness, verifies a fresh reading from after the outage, and compares every stored packet with the source recording. A pass requires zero rejected or pending messages, current readings that never go backward, matching source fingerprints, passing Windows and Ubuntu safeguards, and cleanup.

Run from the DriveTwin root in Windows PowerShell with a fresh evidence name. The first command prepares the image for the exact source. The second runs the guarded check with the original 480-message drive. Allow about nine minutes after launch checks, plus cleanup.

```powershell
python scripts/linux.py --timeout 1000 -- python3 scripts/release/prepare_simulation.py --dashboard
python scripts/release/qualify_restarts.py --case outage-recovery --output evidence/p6/outage-my-session
```

The report `qualification.json` gives the overall result. `outage-result.json` records the timing and accounting. `worker.log` contains progress, and the `outage` subfolder holds the observation trace. The results apply to a one-bus delivery outage. [Earlier outage evidence](../verification/outage-delivery-compatibility.json)

## What we are testing

The broker carries observation packets and storage confirmations between the vehicle and receiver. The vehicle's outbox is a SQLite database that retains exact packets until a matching confirmation arrives. The recovery check interrupts that delivery path for five minutes. The vehicle keeps running and recording data during the interruption.

The registry, which accepts source recipes and clock updates over HTTP, remains available. This is a broker/receiver outage, not a complete vehicle network disconnection. One real SUMO instance drives bus 001; bus 002 is an inactive policy entry inherited from the isolated bootstrap. No ten-asset load or browser performance result follows from this exercise.

```mermaid
sequenceDiagram
  participant V as Actual paced bus
  participant Q as Durable vehicle queue
  participant B as Broker
  participant D as Receiver/database
  V->>Q: Initial observations
  Q->>B: Original packets
  B->>D: Persist and confirm
  Note over B,D: Both stopped for at least 300 wall seconds
  V->>Q: Continue recording 20 readings each second
  Note over Q: Pending packets accumulate
  Note over B,D: Broker restored; receiver restarted
  Q->>D: Controlled newest-original then older-original check through broker
  Note over D: Keep newest current values; retain older history
  Q->>B: Existing sender drains remaining original packets
  B->>D: Confirm durable storage
  V->>Q: Continue generating during and after recovery
```

## Concepts

| Concept | Meaning and project example |
|---|---|
| Backlog | Work waiting to be delivered, such as 300 queued observations after five minutes at one per second. |
| Backpressure | Refuse new work when a queue reaches its limit, preserving the work already stored. The simulated producer must stop explicitly. |
| Logical capacity | The configured limit on encoded packet bytes. It excludes SQLite indexes, row headers and temporary journal storage. |
| Filesystem capacity | The actual storage available for the database and all related files. It can run out before the logical limit. |
| Transaction rollback | Undo an unsuccessful operation so it cannot leave a partial packet or consume a sequence number. |
| `tmpfs` | A temporary Linux filesystem with a fixed size, backed by memory/swap. A private 4 MiB instance lets us force a storage-full error without filling the host drive. |
| `SQLITE_FULL` | SQLite's specific error code for insufficient storage; other failures must not be mistaken for the intended test fault. |
| Sequence number | A durable increasing position in the source's event stream. A failed enqueue must not silently allocate one. |
| MiB | Mebibyte, 1,048,576 bytes. A 32 MiB encoded limit holds exactly 4,096 packets of 8,192 bytes. |
| Sampled observation | A measurement taken periodically. A one-second monitor can confirm the states it samples; it cannot establish every intermediate browser rendering or a latency percentile. |

## Automated sequence and expected checks

1. Build the matching locked backend/simulator images using the existing Linux launcher. Create uniquely owned containers, secrets, volumes and private reports; leave the canonical source tree in Windows.
2. Enqueue schema-valid synthetic storage packets with all 20 initial signals and exactly 8,192 encoded bytes each. Bounded annotations fill the wire-size envelope; no new sensor or relaxed validator is needed. These are offline storage fixtures with no server registration/delivery or physical-model claim.
3. Check 1,800 packets, representing 30 minutes at one packet per second, then fill all 32 MiB. Refuse packet 4,097 and compare all original hashes and sequence state. This insertion is accelerated storage testing, not a 30-minute wall-clock benchmark. Record the database footprint against the 64 MiB planning budget; do not infer a hard quota or worst journal peak.
4. Exit that container, reopen the same Linux-local queue volume in another container, and compare every original packet. Apply four explicitly local test receipts and refill. Reconcile 4,100 generated durable events as four accepted plus 4,096 pending. This does not claim those receipt fixtures came from a server.
5. In a different container, fill a private 4 MiB temporary filesystem. Require a real `SQLITE_FULL`, unchanged earlier bytes/next sequence, intact SQLite structure and successful connection reopen. Remove only its 128 KiB test reserve file and verify another insertion succeeds. This file is not the planned 8 MiB production critical-status reserve.
6. Start the normal broker/database/registry/receiver/read API/sender and one actual paced bus. An application programming interface (API) lets the monitor request the same consistent readings that the dashboard uses. Observe at least 60 accepted initial events, then stop broker and receiver. Start the 300-second timer only after both are confirmed stopped; confirm no observation ingress while source/queue generation continues.
7. Restore the broker and restart ingestion. With the normal sender temporarily stopped, deliver the newest queued original and then an older queued original through the authorized publisher and actual durable receipts. Verify the old packet joins history while current values stay newer. Resume the unchanged production sender and drain within 120 seconds while source generation continues.
8. Finish 480 paced observations, including at least 30 generated after restoration. Reconcile every source packet/recipe byte, 9,600 scalar samples, zero quarantine/drop/pending and all 20 current values at sequence 479. Keep the one-second trace, reports and original source outputs private. Verify ownership before removing only the fixture's containers and volumes.

## Reproduce from Windows PowerShell

In `<checkout>`, run:

```powershell
python scripts/dev.py check
python scripts/linux.py --timeout 2400 -- python3 scripts/monitoring/qualify_outage.py --evidence-dir evidence/p1/CHOOSE_NEW_RUN_NAME
```

Use a new evidence name; previous runs are never overwritten. The second command takes several minutes: the actual source runs for about eight minutes, including the full five-minute outage. It prints progress every 30 outage seconds.

Success requires `qualification.json` with `passed: true`, matching before/after source fingerprints, all storage/service results passing and no owned containers/volumes remaining. Read measured values in that report; a script being present does not establish acceptance. Failure reports and raw traces remain separate and ignored. Matching source may be uncommitted during qualification; fingerprints identify tested bytes, and the later local Git commit saves the implementation and reviewed summary.

## Remaining gates

The existing producer explicitly stops on queue capacity/storage failure; it does not restore physical simulation memory or silently evict packets. An unpausable physical sensor needs a separately designed durable loss/overflow policy. Physical quotas, the critical-status reserve, transient journal peaks and power-loss/controller faults remain additional tests/design work.

The current release includes the one-bus 30-minute run, scoped display-latency check, five-minute recovery, and sealed history restoration. Fleet duration, burst, queue-capacity, and outage workloads remain deferred. [Current acceptance](../release/verification.md) See [ADR-0018](../adr/0018-sustained-outage-and-storage-qualification.md), [frozen workload policy](../workloads-and-metrics.md) and [Phase 1 status](../monitoring-status.md).

## Measured local result

Final-source qualification passed on November 3, 2026: 300.290 measured outage seconds; 480 original events / 9,600 samples; no quarantine/drop/pending; original packet/recipe equality and current ordering passed. Empty queue was first observed after 102.094 seconds using one-second samples, while generation continued; 102 observations were generated after receiver-ready state capture, not after the queue emptied. The single-asset drain target was 120 seconds. This does not qualify fleet/browser latency.

The 1,800-packet storage point used 14,745,600 encoded bytes / 15,814,656 database bytes. Full encoded capacity held 4,096 exact-size packets / 32 MiB, with a 35,942,400-byte database. Refused insertion, container reopen, four local receipt/refill accounting, and genuine SQLITE_FULL at 456 packets in the private 4 MiB filesystem all passed. Earlier packets survived, and a new insert succeeded after freeing test space. These are bounded storage checks; hard physical quotas and journal peaks remain open.

The final shared suite passed 103 Python / 12 frontend tests, strict TypeScript/scoped Python checks and 339+68 contract outcomes per language. All source fingerprints matched; owned cleanup was empty. [Reviewed evidence](../verification/sustained-outage-and-storage.json).

Control-file writers publish complete snapshots, and readers retry transient missing/locked/partial handoffs for a bounded interval on the Windows-mounted filesystem. Persistent faults fail the run. Monitor liveness is timed by progress in the controller's own clock, with other process timestamps retained only as audit markers. Forced termination can still prevent normal cleanup; the existing launcher is not a container service manager. Preserve the private command log/ownership labels and have the agent verify the exact owned project before cleanup; never use broad volume deletion.

After E2-A, current qualification commands select the explicitly reviewed compatible baseline, preserving the historical E1 result and strict D2 record. No original wire/queue/physics algorithm is relaxed. See [ADR-0019](../adr/0019-sustained-workload-sources-and-measurement.md) and [the source guide](sustained-workload-sources.md) for the exact profile/parser changes.
