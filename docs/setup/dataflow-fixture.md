# First data-flow qualification

This is the first small working path through the proposed backend. It uses artificial test values, not a simulated driving mission. It qualifies part of P0-02; the road simulator, browser checks, shared TypeScript validation, workload tests, and other Phase 0 deliverables have separate acceptance checks.

## Understand the path

```text
Synthetic publisher -> Mosquitto -> Python ingestion -> PostgreSQL -> FastAPI query
                            MQTT 5                         HTTP
```

**Telemetry** means measurements sent from an asset, such as a bus's battery state of charge. The publisher stands in for the future vehicle adapter. The fixture uses fixed values to verify the delivered payload exactly.

**MQTT** is a messaging protocol designed for devices exchanging small messages. A publisher sends a message to a named **topic**; a subscriber asks the broker to deliver messages on matching topics. Mosquitto is the broker implementation. Here the topic is `evdt/qualification/<run-id>/bus-001/observations`. The run identifier separates this test's records from other tests. There are no command topics or actuator processes in this fixture. MQTT's name is used as the protocol's proper name; it does not require a messaging-queue product.

**Ingestion** is the code that receives, checks, and records messages. It checks a **data contract**, the rules defining an acceptable message. Our contract has two layers:

1. [JSON Schema](../../contracts/telemetry/1.0.0.schema.json) defines the envelope: required fields, types, version, and timestamp format. JSON means JavaScript Object Notation, a text format of named values and lists. We use the JSON Schema 2020-12 dialect and the Python `jsonschema` validator with format checks enabled.
2. The [signal catalog](../../catalog/reference-signals.json) defines registered sources and signals, units, ranges, physical locations, and display hints. The shared receiver does not contain a list of particular sensor names. A test registers a new temperature signal through a catalog addition alone.

For example, the battery's canonical value `0.72` has unit `1`, meaning a dimensionless fraction. Its display hint multiplies by 100 to show 72%. A message containing `72` with unit `%` is rejected by the unit checks. Motor temperature uses kelvin (`K`); the display hint converts it to degrees Celsius. Calibration and source-unit conversion are later adapter work.

**PostgreSQL** is a relational database: it stores structured records in tables and supports queries in Structured Query Language (SQL). It stores accepted envelopes, rejection evidence, and delivery counters. Its files live in a Docker **named volume**, persistent storage outside a container's replaceable filesystem.

The fixture exposes a read-only FastAPI query service with an OpenAPI schema. The [demo guide](../release/demo.md) covers the dashboard.

## What happens when delivery repeats or fails

MQTT **Quality of Service 1 (QoS 1)** requests at-least-once delivery on a connection: a message can be delivered more than once. A broker acknowledgment confirms one delivery step, not that our database has saved the reading. The Python MQTT client uses manual acknowledgment: ingestion acknowledges only after its database transaction commits. A **transaction** groups database changes so the accepted reading or rejection and its counter are saved together. A failed transaction rolls back its changes. [Paho acknowledgment API](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html), [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)

Each event has a stable identifier. Ingestion computes a **SHA-256 hash**, a fingerprint of the normalized message content. The same identifier and content count as a duplicate, leaving one history record. The same identifier with changed content, or a conflicting source-session sequence number, enters **quarantine**: a separate table preserving the original message and a rejection reason. SHA-256 detects content differences here; it does not authenticate the sender.

Database uniqueness is scoped by mode and run. A second constraint protects the vehicle/source/session/sequence identity. Query results are ordered by observation time, then event identifier, even when readings arrive out of order. There is no current-state projection yet, so this test does not establish a latest-value or freshness policy.

The timestamps have distinct meanings: `observed_at` is a fixed artificial UTC observation time; `sim_time_s` is synthetic elapsed time; `received_at` is the ingestion clock; `persisted_at` is the database insertion clock **inside** the transaction, not a separately measured commit time. UTC is Coordinated Universal Time, a common reference independent of local time zones. The trailing `Z` in a timestamp denotes UTC. These test times are not network-latency measurements.

This fixture does not implement an adapter's durable local spool or an application acknowledgment returned to the adapter. Those are Phase 1 requirements. Broker persistence and manual acknowledgments alone do not prove zero data loss under a power failure, client crash, or prolonged outage.

## Reproduce the automated check

Run the fixture from Windows PowerShell.

1. In a PowerShell terminal, open `<checkout>`. Ubuntu and its authorized Docker Engine must already be installed. The existing [launcher guide](linux-launcher.md) explains how Windows invokes Ubuntu without switching the whole app.
2. Run the following from that folder, choosing a **new** evidence filename each time:

   ```powershell
   python scripts/linux.py --timeout 900 -- python3 scripts/platform/dataflow.py qualify --report evidence/p0/my-dataflow-check.json
   ```

   `--timeout 900` allows up to 900 seconds for the Linux command. `qualify` builds and tests this fixture. `--report` saves the measured result; the script refuses to overwrite existing evidence. If local port 18760 is occupied, add `--port 18761` to select another loopback port.

3. Expect a tools build, locked application build, code checks, and two application startup cycles. A cycle starts the database, broker, migration, receiver, and API. The receiver's successful subscription is checked before the publisher sends test messages. A started process alone is not sufficient readiness. Compose's health/dependency conditions coordinate these steps. [Compose readiness documentation](https://docs.docker.com/compose/how-tos/startup-order/)
4. Open the report in the project. Success requires `passed: true`, passing unit/lint/format checks, matching accounting, successful second-cycle persistence verification, and empty owned-container/volume lists after cleanup. On failure inspect the report's error and retained logs under `.runtime/p0-dataflow/<test-id>/commands.log`; do not delete evidence or reset Docker.

The two cycles each send nine deliveries: three unique valid observations (including one late arrival), one exact duplicate, and five invalid/conflicting deliveries. At the end the expected totals are six accepted observations, two duplicates, and ten quarantined deliveries. The second cycle first queries the first cycle's three readings **before publishing anything else**. It then sends its own batch. This distinguishes persistence from simply generating replacement data.

The script removes only its uniquely named Compose project's containers, network, and label-verified volumes. Removing those test volumes happens **after** the persistence checks. It never prunes the engine or stops unrelated containers. Locally built images and downloaded image/build caches remain for subsequent work. Do not manually run a global prune as part of these instructions.

## Dependency and build controls

An **image** is a packaged container filesystem and startup configuration. A tag is a human-readable label that can move. A **digest** identifies immutable image content. The [image manifest](../../environments/dataflow-images.json) records the one-time discovery tags and resolved digests; builds use the digests. Later image upgrades require an intentional manifest update and requalification.

**uv** manages Python dependencies. A dependency is another library our code uses. [pyproject.toml](../../pyproject.toml) declares compatible version ranges; [uv.lock](../../uv.lock) records the exact resolved versions and package hashes. `uv sync --locked` installs from that lock and fails if the project declaration and lock disagree. This prevents a normal rebuild from silently choosing new libraries. [uv locking](https://docs.astral.sh/uv/concepts/projects/sync/), [uv in Docker](https://docs.astral.sh/uv/guides/integration/docker/)

One-time lock generation or an intentional dependency update uses:

```powershell
python scripts/linux.py --timeout 600 -- python3 scripts/platform/dataflow.py lock
```

Then rerun qualification and review the lock changes. The `format` action runs Ruff, an automatic Python code-style checker/formatter. The qualification action checks style without rewriting code. **pytest** executes the small Python tests, including invalid types/units/versions and the catalog extension check. These tests run in the same pinned Python base as the application. Type checking and continuous integration are still separate foundation work.

Source remains in the one canonical Windows project folder. Application dependencies are installed in the container's Linux filesystem, not in a second source checkout or a Windows virtual environment.

## Boundaries and next checks

This deployment is restricted to synthetic local qualification. The broker and database publish no host ports; the network is internal; only the read-only API maps to host loopback. The broker permits anonymous clients within that isolated network. Per-device authentication, topic access rules, encrypted transport, production API access controls, and command authorization have **not** been qualified. A generated test database password is kept in ignored `.runtime` storage and passed by a file mount; this is not a production secret-management design.

The application processes run as an unprivileged container user with read-only filesystems and small temporary areas. The broker explicitly runs as its image's unprivileged user 1883. The database bootstrap account is a fixture administrator; separate ingestion and read-only API database roles remain to be implemented. Container memory limits bound this fixture; they are not a measured resource-performance claim. The short test is not workload W0/W1 or quality-target Q-12 qualification. The automated run cleans up its API before returning. The separate [P0-03 browser check](browser-verification.md) now qualifies Windows/browser access using an API-only additional network and diagnostic-page override; the base internal-only fixture's queries remain container-internal.

Normal-completion cleanup is tested. Cancellation while Docker is running background containers is not yet qualified by this harness; launcher process cleanup alone cannot remove engine-managed containers. If interrupted, inspect the unique project recorded in the report or `.runtime/p0-dataflow` command log and perform only scoped, ownership-checked cleanup before rerunning. Never use an engine-wide reset or prune to recover this test.

Measured final result: [20 passing unit tests, both startup cycles, preserved data, reconciled deliveries, and clean owned-resource removal](../verification/README.md). The retained first failed report and intermediate result are explained in [Phase 0 status](../foundation-status.md).

The local release uses this FastAPI, Mosquitto, and PostgreSQL stack. The [architecture](../architecture.md) and [release evidence](../release/verification.md) document its later integration checks.
