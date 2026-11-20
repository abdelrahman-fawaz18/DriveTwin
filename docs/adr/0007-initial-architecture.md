# ADR-0007: initial modular backend and engineering workflow

Date: November 3, 2026. Status: selected for initial implementation; workload acceptance remains future evidence.

## Context and method

D-03 originally proposed a bounded runtime comparison with a general twin framework. The method is narrowed to an architecture/requirements comparison before P1: current needs do not require another registry/policy cluster, and deploying one would not validate this project's freshness, history, energy or control semantics. This is an explicit method revision, not a claim that an unrun benchmark passed or failed.

Eclipse Ditto is a twin-management framework with five cooperating services and MongoDB persistence. That adds another store alongside the intended PostgreSQL history. Additional integration/operations work is an architectural inference, not a measured memory/speed disadvantage. [Official architecture](https://eclipse.dev/ditto/architecture-overview.html)

## Comparison

| Requirement/tradeoff | Modular Python backend | General twin framework |
|---|---|---|
| Admission/history | Qualified catalog/profile and transactional history | Entity mapping plus history integration needed |
| Current state/freshness | Project must implement explicit ordering/freshness | Entity state available; project policy still needed |
| Identity/policy | Operational authorization must be built before use; fixture insufficient | Built-in policy service can be valuable when justified |
| Verified commands | Project ledger/executor/effect tests required | Messaging does not itself establish physical effect |
| Energy/thermal/coupling | Isolated domain modules/adapters | Domain models and integration still required |
| Dependencies/evidence | Broker, PostgreSQL, Python codebase; fixture restart/persistence tested | Additional services/store documented; no local runtime benchmark performed |

## Selections

- D-03: modular domain-specific Python backend; separate ingress/API processes from one codebase. Keep repository, contract, adapter, model and command boundaries explicit. No per-sensor microservices.
- D-04: Mosquitto MQTT 5. It satisfies tested initial publication/subscription needs. No RabbitMQ benchmark or general superiority claim is made.
- D-05: vanilla PostgreSQL 18 for history/audit/later projections. Defer extensions/alternate stores until retention/query measurements justify them. Workload/restore objectives remain unpassed.
- D-06: application Python 3.12, Node 24 tools, TypeScript 6.0.3, SUMO/TraCI 1.28.0, locked dependencies and digest-pinned Linux amd64 images. Exact observations belong in [compatibility](../compatibility.md). Ubuntu system Python is not the implicit application runtime.
- D-10: React-based generic scalar UI, Leaflet maps and Server-Sent Events (SSE) are the P1 direction. SSE is a one-way HTTP update stream from server to browser; recovery requires snapshots/cursors/history beyond transport delivery. Pin/test actual UI package versions at P1 integration. Current browser qualification is not a product UI.

React builds interfaces from reusable components; Leaflet draws maps. Domain freshness remains a backend responsibility. [React](https://react.dev/learn), [Leaflet](https://leafletjs.com/reference.html), [SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)

## Engineering workflow and limits

`python scripts/dev.py check` is the Windows/native-Linux entry point. It checks candidate privacy/secrets, builds from public locks/images, then runs offline read-only unit/lint/format/strict TypeScript and shared-contract checks. Original fixtures are checked without private reports. Project-wide strict Python typing is not claimed; add checks at P1 typed interfaces and expand coverage.

The minimal GitHub Actions definition uses the same command, a commit-pinned checkout action, read-only repository permission and no retained checkout credentials. Hosted execution remains unverified until authorized publication. Service/browser qualification is separate. Build inputs exclude private settings/records/secrets. Local commits do not authorize upload or settle license rights.

The project owns operational authorization, freshness, durable acknowledgment, command safety and model validity. Reconsider framework/storage when measured W1/W2 objectives fail after bounded diagnosis or real multi-tenant policy/search needs arise. Record migration costs and new evidence with references to the earlier decision.
