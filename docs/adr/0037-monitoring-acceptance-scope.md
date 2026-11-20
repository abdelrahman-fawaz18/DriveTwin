# ADR 0037. Monitoring acceptance scope

Date: November 4, 2026. Status: explicitly authorized; acceptance pending.

## Decision

Freeze features and architecture. Use the already qualified normal monitoring configuration: authenticated broker, durable legacy queues, receiver, PostgreSQL, read-only API and catalog-driven dashboard. Optional bounded-storage bindings stay unset; completed components and their evidence remain available without claiming deployment readiness.

Version workloads as 1.2.0 and the policy as `phase1-short-run/1.2.0`. W0: one bus, 20 signals, one event/second, 60 warmup + 120 measured seconds. W1: ten simultaneous buses, 20 signals each, one event/bus/second, 60 warmup + 300 measured seconds. A full declared interval is still mandatory; `full_duration` means complete declared short-run duration, not sustained-load proof. Reports explicitly mark sustained-load qualification false. No latency, resource, access, integrity, freshness, recovery, replay or accounting limit changes. A canonical protected-specification fingerprint normalizes only the authorized version/duration fields and still protects every other prior requirement.

## Evidence and limits

Reuse the complete 72,000-event historical fixture, queries, downloads and restore, plus latest successful real five-minute outage and compatible extension/replay/access/storage results. Compare affected source and configuration explicitly. Keep original image/source guards and old outcomes; do not relabel failures or regenerate history because unrelated files changed. Run only affected integration and deterministic failure-branch checks. Run final shared checks once after changes stabilize.

Phase 6 takes the former 30-minute qualifications, sustained-load/soak, comprehensive visual regression, broader reboot/deployment/storage lifecycle tests, automatic retention deletion and production hardening beyond active monitoring. Host-memory reserve has failed earlier fleet rehearsals; keep the unchanged threshold and report any repeated failure honestly. Silent corruption and unauthorized control are blocking defects.

## Qualification windows

The initial closeout window ran from 15:40:57 to 16:40:57 UTC. The scope used the normal monitoring configuration and allowed one targeted correction and rerun after a failed acceptance attempt. The reported outcomes retain their original source, configuration, and thresholds.

A separate verification session ran from 16:45:37 to 17:45:37 UTC. The complete W0 attempt reconciled 180 events and passed latency and Linux resource checks. It failed the Windows memory reserve. A cache cleanup preceded one rerun, which failed its launch precheck. The session stopped before W1 and further integration. [Verification record](../verification/monitoring-resumption.json)
