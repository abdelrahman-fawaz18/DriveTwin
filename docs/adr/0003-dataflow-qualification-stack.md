# ADR-0003: bounded stack for the first telemetry fixture

Date: November 3, 2026 (America/Toronto)

Status: Qualified for the bounded P0 telemetry/store/query experiment. The [final report](../verification/README.md) passed; this does not close D-03 through D-06 or authorize Phase 1 entry.

## Decision and reason

Use Mosquitto MQTT 5, PostgreSQL 18, Python 3.12, and a modular FastAPI backend for the first isolated data-flow fixture. Use uv's committed dependency lock and digest-pinned official container images. This tests the plan's recommended transport/storage/query path directly, with few runtime components and without a second source tree.

The code has separate contract, repository, ingestion, and query modules. Ingestion and the API are separate processes built from one application image; this is not one microservice per sensor. A catalog controls supported scalar signal identities, units, and bounds. A migration controls the initial database layout and records its checksum; the migration runner refuses a changed applied migration. A future layout change requires a new explicit migration, not editing this applied file.

## What this does not decide

Eclipse Ditto is a framework providing twin state, interfaces, and policies. Its documented deployment includes cooperating services and a MongoDB backing store. Those requirements would add a second database alongside the proposed history database; actual local memory use has not been measured. This is an architectural observation, not a claim that Ditto failed a local benchmark. [Ditto runtime requirements](https://eclipse.dev/ditto/installation-running.html), [Ditto architecture](https://eclipse.dev/ditto/architecture-overview.html)

Do not adopt or reject Ditto solely because the small Python fixture works. D-03 still needs the bounded comparison of schema mapping, stale-state behavior, access isolation, command correlation, restart behavior, resource footprint, and implementation effort. If Ditto is selected, this fixture's accepted observation history remains separate; its twin state/policy integration belongs behind the planned repository boundary. The current concrete PostgreSQL module is a qualification implementation, not a completed general repository interface.

Mosquitto versus RabbitMQ (D-04) and PostgreSQL versus extensions/other databases (D-05) remain candidate selections pending their documented requirements, rights, workload, and recovery checks. A small successful fixture is not a W0/W1 benchmark. PostgreSQL 18 is within the project's supported-major candidate line; the precise image content is locked by digest. [PostgreSQL support policy](https://www.postgresql.org/support/versioning/)

Python 3.12 is selected for this fixture and frozen by the image plus `requires-python`. Compatibility with SUMO's selected client/library remains untested. D-06 also needs Node/TypeScript, SUMO, standards, and API/database compatibility records. No unsupported claim of a final project-wide version freeze is made.

## Evaluation and scope

The qualification checks actual MQTT publication, schema/catalog rejection, transactional observation/quarantine accounting, exact-duplicate handling, conflicting identities, out-of-order history, query isolation, two container startup cycles, persistence across container replacement, and owned-resource cleanup. The [explained reproduction guide](../setup/dataflow-fixture.md) defines the experiment and limitations. Measured results belong in the immutable evidence reports and [Phase 0 status](../foundation-status.md), not in planned targets.

Operational authentication and authorization, local durable spooling, an adapter-facing application acknowledgment, current-state/freshness projection, calibration, a driving/energy model, browser reachability, and matching TypeScript validation remain later work. No bidirectional-control or deployment-readiness claim follows from this fixture.
