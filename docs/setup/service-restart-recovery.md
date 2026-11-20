# Individual service restart recovery

The message broker carries readings from the bus to ingestion. Ingestion saves them in the database. The command coordinator records whether an authorized request can be handed off, and the executor applies the request to the simulated bus. These checks interrupt one component at a time to verify what survives and how the system recovers.

| Case | Required outcome |
|---|---|
| Message broker | A new reading waits in the disk queue while delivery is down. All original readings arrive after the broker starts. |
| Ingestion | A new reading waits for the database receipt. Restarting ingestion restores delivery without changing any original reading. |
| Command coordinator | Telemetry continues during the stop. After restart, the command service returns the same unresolved reservation and denies a second handoff. |
| Database | After restart, earlier readings and the unresolved reservation are intact. New readings can be saved and the old request cannot be handed off again. The simulated source waits at a checkpoint while registration storage is unavailable. |
| Executor before and after actuation | Interrupt the actual simulator at a known boundary. Use the normal speed readings to check what happened. Preserve the original reservation, reject a same-run restart, and start a fresh simulation that cannot inherit the old command. |

A reservation is a durable record that an executor has claimed a request. An unresolved reservation means the service lacks enough evidence to say whether the action happened. It must keep that uncertainty across restart and refuse to hand off the same action again.

## Run one case

From PowerShell in the project folder, run the following command. It checks available host resources, then starts a temporary project in the existing Ubuntu Docker Engine.

```powershell
python scripts/release/qualify_restarts.py --case broker --output evidence/p6/restart-broker-local
```

Use a fresh folder for each attempt. The other case names are `ingest`, `command-api`, `db`, `executor-before`, and `executor-after`. Wait for a case to finish before starting the next. For a pass, the console prints `passed: True` and the saved `qualification.json` records the guarded launch, worker result, and cleanup. The guard stops a run on a resource or clock violation. An incomplete report preserves its failure and any collected evidence.

The coordinator and database cases use an unresolved test handoff without authorizing a simulator speed change. The executor cases use the existing simulator interruption fixture and independent speed readings. The test tools and recordings stay separate from the normal runtime and public demo.

The broker, ingestion, coordinator, and database cases use 16 deterministic source messages with explicit stop and resume points. The simulator steps between those points without waiting for real time. The controller still measures service readiness and recovery in elapsed time, and the memory checks apply throughout. The executor cases retain their paced scenario because their speed command depends on it.

The broker and ingestion cases each verified that one reading waited on disk during the stop and all 16 reached storage unchanged. The database case preserved the first two saved messages, then resumed delivery and reconciled all 16 originals. The existing reservations were unchanged, repeat claims were refused, and cleanup passed. The current harness targets the owned container by its exact identifier and checks that completed migrations retain their original start times. The report preserves the incomplete attempts and the source fingerprints for each pass. [Restart verification record](../verification/service-restart-status.json)

The coordinator restart preserved the original reservation and reconciled all 16 readings. Both executor interruption cases refused a restart of the old run and preserved exactly one unresolved attempt. The last four observed speeds in the after-actuation case were 4 m/s, matching the requested limit. Both fresh runs produced 16 readings, reached 7.8 m/s, and had an empty command journal for the new run. The after-actuation run also verified one fresh resource sample during container replacement under the existing limits. [Restart verification record](../verification/service-restart-status.json)

## Check a supported schema upgrade

This separate test starts an isolated PostgreSQL database. It creates the first schema, inserts an old telemetry message, and applies the current migration files. It then checks the original bytes and history backfill. A message with an unknown version must be refused by the current wire validator.

```powershell
python scripts/release/qualify_restarts.py --case schema-upgrade --output evidence/p6/compatibility-schema-local
```

Expect `P6-04 schema-upgrade passed: True`, a passing report, and verified cleanup. [Compatibility verification](../verification/schema-compatibility.json)
