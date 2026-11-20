# Engineering foundation

Qualification recorded November 3, 2026. Current acceptance is summarized in the [release evidence](release/verification.md).

Status: **P0-01 through P0-06 complete within their recorded scopes.** Phase 0 qualifies the development tools and freezes the initial engineering foundation. Passing an installation check does not establish vehicle-model accuracy or application readiness.

| Gate | Status | Evidence and remaining boundary |
|---|---|---|
| P0-01: Windows-to-Ubuntu command execution | Passed | 18 integration tests, real terminal interruption, shared-file editing, and recovered chat access. Managed foreground process groups only; suspension and supervisor crashes are outside this scope. |
| P0-02: containers, persistence, and traffic simulator | Passed | Engine service stop/start tests, two real messaging/store/query starts with persistent data, 20 application unit tests, and two deterministic SUMO movement runs. Full host reboot and calibrated EV physics are untested. |
| P0-03: browser access | Passed | Visible Windows Edge and pinned automated Chromium displayed actual saved observations, refresh, identity, and empty results. Product dashboard and Linux browser CI remain future work. |
| P0-04: shared data contracts | Passed | Python and compiled TypeScript agreed with 339 expected outcomes: 116 valid and 223 rejected. Explicit adaptation was consumed by the frozen original reader. Data-flow/browser regressions passed. |
| P0-05: architecture and engineering foundation | Passed locally | ConOps, requirement trace, ownership/sequence/state diagrams, initial component/time/energy decisions, Git safeguards, shared Windows/Linux checks, and minimal CI definition completed. Hosted CI is explicitly unrun; P2 validates the selected energy design. |
| P0-06: workloads, data rights, and model assumptions | Passed locally | Frozen workloads/metrics/budgets, reviewed attributed map, explicit hypothetical bus/station parameters, dependency inventory, 35 source/foundation tests, and two actual repeatable Montreal journeys. New dependency set passed 339 contract cases and real service/browser regressions. Performance/model/operational and release-license gates remain future work. |

See the [verification summary](verification/README.md) and [full plan](../SCOPE.md). Raw diagnostics are retained locally and excluded from Git; summaries distinguish original measured results from later changes.

## Operating arrangement

- Run Linux commands from the Windows checkout with `python scripts/linux.py -- COMMAND`.
- Use the selected Ubuntu-native Docker Engine and its project-local CLI settings; keep Desktop out of the active engine path.
- Preserve one canonical source tree. Routine commands do not require access to other projects.
- Read the relevant architecture decision and setup guide before changing a qualified component. Do not bypass source-fingerprint checks or relabel old measurements as current.
- Measure available host memory before heavy browser/container work. Previous memory snapshots are observations, not a sustained workload result. No memory-allocation adjustment has been needed for completed checks.

## Historical checkpoint boundary

At this foundation checkpoint the complete monitoring product, command path and charging work were still future gates. Their later results are recorded in [monitoring](monitoring-status.md) and the [current capability matrix](release/capabilities.md). Foundation checks do not inherit those later claims.

## Latest engineering-foundation verification

[Sanitized P0-05 record](verification/engineering-foundation.json): the shared command passed through both the Windows launcher and native Linux entry point, including 339 contract cases (116 valid, 223 rejected), strict TypeScript compilation, Python style checks, and unit tests. The updated real data-flow fixture passed 24 unit tests and two startup/persistence cycles (8.615 and 8.745 seconds with Engine running). Two SUMO runs and both Windows browsers passed again after private build-input exclusions. The privacy guard rejected staged secret-shaped bytes and forced-staged private paths in an isolated synthetic test. Local Git checkpoints exist; no remote/upload was configured.

The alternate backend was compared architecturally, not runtime-benchmarked. Python strict typing is not project-wide; P1 interfaces introduce appropriate checks. A full host reboot, sustained workload/resource objectives, complete source/data rights, geographic/bus assumptions, product monitoring UI, energy accuracy, and control remain outside this completed gate.

## Reference movement verification

[Sanitized P0-06 record](verification/montreal-reference-movement.json). Final source checks passed 35 tests, strict TypeScript and all 339 expected contract outcomes. Actual data-flow/persistence, both Windows browsers and original synthetic SUMO were requalified against the changed dependency lock. Two final Montreal runs matched exactly: 4,323 records, 864.6 simulated-second arrival, 10 s stop, no collision/relocation. Geographic round trips and bounded continuous state passed. Every reviewed warning message/count remains checked, independent of emission order.

P0-06 freezes specifications and documents outcomes; W0/W1 performance, full soak/recovery/resource guarantees, calibrated battery/thermal physics, route legality/safety, production security, control, hosted CI and publication remain unclaimed. Original DriveTwin work now uses the [MIT license](../LICENSE). Component notices apply to distributed native and binary artifacts. See [reproduction](setup/foundation-data.md), [workloads](workloads-and-metrics.md), [data provenance](data-sources.md), [assumptions](model-assumptions.md) and [ADR-0009](adr/0009-data-workload-and-assumptions.md).
