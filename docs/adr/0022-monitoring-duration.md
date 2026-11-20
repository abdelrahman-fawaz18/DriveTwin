# ADR 0022. Monitoring workload duration

Date: November 4, 2026. Status: explicitly requested by the user; qualification pending.

## Decision and exact scope

Change the measured duration of the ten-asset Phase 1 W1 benchmark from 3,600 to 1,800 wall seconds. W0 already has 1,800 measured wall seconds and remains unchanged. Both retain 60 additional seconds of warmup. This is a user-directed change to the project requirement, not an inference from a failed test or a passing result.

Version the executable workload specification as `1.1.0` and its Phase 1 policy as `phase1-wall-time/1.1.0`. Every full Phase 1 attempt must use 60 + 1,800 = 1,860 timed wall seconds, excluding setup/cleanup. The runner rejects stale one-hour configurations, accelerated runs, changed rates, shortened warmup and unapproved durations. Reports must match the current selected profile before evidence can qualify it.

| Workload | Concurrent assets | Measured events | Measured scalar readings | Warmup events | Warmup scalar readings |
|---|---:|---:|---:|---:|---:|
| W0 | 1 | 1,800 | 36,000 | 60 | 1,200 |
| W1 | 10 | 18,000 | 360,000 | 600 | 12,000 |

Each asset still generates one event per second containing all 20 original signals, through its ordinary adapter, durable queue, broker and receiver. Keep real clock duration and separate event identity sets; do not run faster or reclassify warmup to reach these totals.

## Requirements retained

Keep all other Q-01 through Q-12 requirements and phase gates unchanged: latency percentiles/deadlines, exact event/byte accounting, concurrency and signal coverage, freshness, isolation and command authority, spool capacity/physical budgets, restore/startup limits, retention, memory/headroom/swap/OOM limits, clocks and measurement completeness. Tests bind the protected configuration to the preceding version's canonical fingerprint after removing only the authorized version/duration fields.

W2 remains 72,000 seeded events / 1,440,000 scalar rows for Phase 1 history/query/restore qualification. Q-05 still queries five signals over one historical hour with at most 5,000 plotted points; that query does not require an hour-long live benchmark. The later P6 two-hour soak, burst and full fleet recovery definitions stay unchanged. Later long-running workloads are outside the current Phase 1 goal.

## Existing attempts and cleanup

Never relabel a failed, interrupted, shorter or old-policy run as a passing revised benchmark. Preserve its original configuration, source fingerprints, raw records and outcome. If an old one-hour attempt is running, stop generation cleanly, retain partial collectors/source/browser evidence, mark the attempt incomplete and remove only its owned temporary services after preservation.

At the revision inventory, no benchmark controller, source, resource collector or Docker application container was running. There was no one-hour run to interrupt or additional service cleanup to perform. Existing failed/preflight attempts remain intact. The temporary bounded Windows sleep helper is separate from application services and remains subject to renewal/stop/expiry.

The main plan, operating instructions, metric specification, implementation guide, runner and tests must be revised and verified before the next 30-minute attempt. Historical verification records and original immutable schema/catalog/migration bytes remain unchanged. The current goal follows this revised plan and retains the remaining E2-C through E5 work; the app's paused state and saved text must not be falsely reported as updated or active without verification.

## Verified revision checkpoint

Before a new workload attempt, all shared checks passed: 202 Python tests, 12 frontend logic tests, strict TypeScript, four scoped strict Python modules, formatting/build/privacy/secrets and unchanged 339 original plus 81 operational outcomes per language. Final source fingerprints matched. The new negatives reject legacy one-hour profiles, changed warmup/rates, unapproved durations and a legacy report profile before reading its evidence. The protected configuration fingerprint proves that the other frozen requirements match the preceding version. This checkpoint passes the requested revision, not W0/W1 or Phase 1. See the [sanitized record](../verification/monitoring-duration-1.1.json).
