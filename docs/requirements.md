# Requirements and verification trace

The local simulation release has passing evidence for its twelve acceptance gates. This trace connects functional requirements to implementation and measured checks. The approved release profile defines the one-bus scope and the deferred fleet workloads. [Current capability matrix](release/capabilities.md) · [Original release acceptance](release/verification.md) · [Release profile](../qualification/release/simulation.json)

## Functional requirements

| ID | Required behavior | Implementation | Verification |
|---|---|---|---|
| F-01 | Reject invalid types, units, versions, identities, or ambiguous JSON | Portable contracts and scalar catalogs | [Contract verification](setup/contract-verification.md) and [upgrade checks](verification/schema-compatibility.json) |
| F-02 | Preserve asset, source, session, sequence, units, quality, and time | Registered observations and run metadata | [Registry and quality](setup/run-registry-and-quality.md) |
| F-03 | Account for accepted, invalid, conflicting, and duplicate events | Transactional ingestion and stable event identity | Q-02 in [release evidence](release/verification.md) |
| F-04 | Retain and retry original packets until a matching storage receipt | SQLite queue, Sender, and post-commit receipt | Q-03 and Q-07 in [release evidence](release/verification.md) |
| F-05 | Keep current state ordered and expose stale or unavailable inputs | Current-state projection and source clocks | Q-06 in [release evidence](release/verification.md) |
| F-06 | Display all supported scalars with status, units, and history | Catalog-driven dashboard and read API | Q-01 and Q-04 in [release evidence](release/verification.md) |
| F-07 | Show observed position and trajectory with freshness | Static map context and recorded position | [Browser reproduction](verification/source-reproduction.json) |
| F-08 | Extend a supported scalar and asset without changing the shared data path | Catalog, adapter mapping, and configuration | [Extension guide](setup/sensor-and-asset-extension.md) |
| F-09 | Record energy estimates with assumptions, input lineage, uncertainty method and domain | Route-aware Energy Forecast, isolated worker and immutable records; historical known-route compatibility | [Route-aware qualification](verification/energy-route-aware.md), [geographic evidence](verification/energy-geographic.md), [original advisory closeout](verification/energy-advisory-acceptance.md) |
| F-10 | Authorize and expire commands, preserve a unique handoff, and verify effects | Command API, PostgreSQL journal, executor, and observer | [Command closeout](verification/command-acceptance.md) and [restart checks](verification/service-restart-status.json) |
| F-11 | Recommend eligible charging stations and compare decision policies | Moving-bus route-aware advisor and separately configured stationary approach model | [Moving-bus scope](verification/moving-bus-charging.md#operating-scope), [stationary closeout](verification/stationary-charging-acceptance.md) |
| F-12 | Evaluate thermal estimation, fault rules, and intervention effects | Bench model and synthetic cooling studies | [Thermal closeout](verification/thermal-acceptance.md) |
| F-13 | Keep replay and experiment data isolated from live decisions and controls | Separate run identities, APIs, credentials, and database roles | Q-09 in [release evidence](release/verification.md) |
| F-14 | Preserve supported formats, model identities, and recoverable records | Contract registry, migration guards, and verified restoration | [Compatibility](compatibility.md), Q-05 and Q-10 in [release evidence](release/verification.md) |
| F-15 | Enforce roles and keep credentials out of shared source | Scoped API, broker, and database credentials with publication checks | [Command permissions](command-catalog.md) and [privacy policy](privacy.md) |
| F-16 | Reproduce engineering checks from source and locked dependencies | Shared Docker check command and pinned workflow | [Engineering checks](setup/engineering-checks.md) |

## Evaluation boundaries

One-bus monitoring, recovery, restart behavior, restored history, and scoped command safety are accepted for the local release. The two-asset extension exercises identity and permission isolation. Ten-bus endurance, burst, outage, and queue-capacity workloads remain deferred after the recorded host-memory stops. Physical integration, automatic retention deletion, adaptive model promotion, and production deployment have separate acceptance work. [Workload definitions](workloads-and-metrics.md) · [Release scope decision](adr/0070-scope-the-first-release-to-simulation.md)

Forecast accuracy applies to each declared simulated domain. Geographic route-aware forecasts and moving-bus charging have separate evidence from the original known-route model and stationary-departure charging study. The thermal results distinguish laboratory prediction from synthetic bus fault and intervention evidence. [Current energy model](models/route-aware-energy.md) · [Historical advisory model](models/energy-advisory-model-card.md) · [Thermal model card](models/drivetrain-thermal-model-card.md)

Each verification report records its inputs, source fingerprints, expected outcomes, measured outcomes, and cleanup. Earlier failed and incomplete attempts remain in the evidence record. Changes to code, contracts, or model scope require the relevant compatibility and behavioral checks.
