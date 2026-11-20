# ADR-0038: Local monitoring acceptance and deferred host capacity

Date: November 4, 2026. Status: explicitly approved by the user; qualification pending.

## Decision

Phase 1 qualifies local monitoring, not deployment capacity. Policy `phase1-local-monitoring/1.3.0` keeps W0 60 warmup + 120 measured seconds and W1 ten simultaneous buses, 20 signals each, one event per second, 60 warmup + 300 measured seconds. All integrity, access, replay, freshness, recovery, accounting, latency, core-container limits, zero out-of-memory failures and Linux swap limits remain unchanged.

The previous Windows 2 GiB reserve / 30-second condition becomes a recorded warning in this local profile. Full host-capacity qualification remains unpassed and moves to Phase 6. Do not report the original complete Q-12 capacity target as passed. Keep RAM, browser/WSL, paging and sample-coverage records; add system commit totals and remaining allocation capacity. Commit is Windows' promise to back memory with RAM or its paging files; its remaining capacity differs from free physical RAM.

Version the explicit local guard as `phase1-local-host/1.0.0` in `benchmarks/monitoring-host.json`. Abort a temporary test when available physical RAM is below 512 MiB, or system commit headroom below 1 GiB, for 10 observed consecutive seconds. Missing/invalid counters or observation gaps also fail closed. Final assessment conservatively includes sampling boundaries. These provisional operational guardrails are not universal Windows minimums or proof of production safety. Deterministic transient/sustained/corrupt-counter and owned-child cancellation tests must pass before live qualification. The controller stops only its fresh browser-runner tree and owned Linux worker, preserves incomplete outputs where available, and uses its existing exact-project cleanup.

## Evidence reuse and completion

Reuse the prior W0 run's valid complete data/latency/Linux-resource components with explicit runtime-source/configuration comparison. Its original overall failed result remains unchanged; no retrospective original-policy pass is created. Reuse compatible full history/query/export/restore and the latest real five-minute outage. Do not regenerate historical data because test-only instrumentation changed.

Run fresh W1 and the smallest remaining active-configuration readings/history/map/reconnect/stale/access/replay/startup/backup checks. Combine checks where practical. Product architecture and features stay frozen; unfinished optional bounded-storage integration stays disabled. Phase 6 retains former 30-minute qualification, sustained/soak, comprehensive visual regression, broader reboot/deployment/storage lifecycle, automatic retention deletion, and production hardening. Corruption and unauthorized control are never deferred.

## Bounded session

The verification window was one hour with a single targeted correction permitted. Earlier failed runs retain their original outcomes.

## Single corrective attempt: setup cache and partial-copy paths

The first 1.3.0 attempt hit the physical emergency floor after ten sources started (core peak about 1.53 GiB; Linux clean file cache about 0.96 GiB). It remains failed/incomplete. A Windows-style destination caused its fallback Docker source/trace copies to fail; the report explicitly retains those copy failures. Available host/Linux resource records and browser artifacts remain private; complete source originals from that interrupted temporary run were not recovered.

Before the one permitted rerun, 1.3.1 / host policy 1.0.1 fixes the destination to a canonical Linux-relative path and adds explicit clean-cache preparation after core readiness, before the browser and source warmup start. The Windows controller invokes only sync/drop-clean-cache in the existing WSL distribution. Monitoring remains active; no counters, warning history or emergency state are reset. Record cache bytes before/after. All buses, rates, durations, latency/integrity/Linux limits and emergency floors remain unchanged. This prepares the test environment; it is not product optimization or deployment capacity proof. Deterministic tests verify the scoped destination and exact preparation operation before the rerun. If the rerun still fails, stop without another redesign/retry.
