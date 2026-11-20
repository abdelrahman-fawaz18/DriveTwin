# Command authority and execution acceptance

Phase 3 is complete for the local simulated command milestone. It proves a bounded speed-cap action and records operator intent for a station. For example, the tested live cap reduced a 7.80 m/s reference peak to 4.00 m/s, and the observer confirmed that change independently of the simulator's reply. [Live speed result](live-speed-command.json)

| Planned deliverable | Closeout record |
|---|---|
| Command state and sequence diagrams | Show admission, pending changes, durable reservation, timing and authority checks, and a separate effect assessment. [Architecture](../architecture.md#local-command-lifecycle) |
| Permission matrix | Shows each role's allowed reads and writes, the gateway's limited routes, and separate database access. [Permission matrix](../command-catalog.md#permission-matrix) |
| Command catalog | Links versioned requests, endpoints, units, deadlines, and effect conditions for the supported capabilities. [Command catalog](../command-catalog.md) |
| Operator runbook | Covers startup, timeline, retry, uncertain execution, a stopped source, and backup recovery. [Runbook](../setup/operator-runbook.md) |
| Qualification evidence | P3-01 through P3-06 passed for the local profile. The archived audit checks measured actuation, duplicate and restart behavior, interrupted handoff, expiry and scope, precedence, and actuator faults. It also reviews operator browser and backup results. [Evidence record](command-closeout.json) |

[ADR 0056](../adr/0056-close-local-command-scope.md) records the local transport and recovery decisions. The coordinator's durable reservation and the fresh-run recovery rule are the qualified boundary. A durable device-side ledger is required before enabling same-session recovery or physical control. Automatic policy and input lineage must be added before automatic dispatch. The original measured reports remain historical evidence for their tested sources.

| Phase 4 entry item | Status |
|---|---|
| Local command integrity | Passed under the Phase 3 simulation scope. |
| Phase 2 advisory release | Verified for its documented known-route forecast and conditional station assessments. |
| First checkpoint | **P4-A1, Charging requirements and station profiles.** Ready to start in advisory/offline mode. Specify charging objective, reserve and diversion rules. Define three configurable hypothetical stations with road approaches, power, compatibility, capacity, queue assumptions, and status freshness. Set the evidence and comparison rules before policy evaluation. |
| Runtime model and station arrival | Open. Qualify the model across the intended decision domain and verify station endpoints with independent drive evidence before relying on their estimates for dispatch. |
| Automatic simulated dispatch | Open. Requires qualification, decision lineage, runtime guard and override checks, and explicit approval. |

[Phase 4 plan](../charging-policy.md) · [Frozen advisory release](../models/stationary-advisory-release.json)
