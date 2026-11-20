# ADR-0024: Bounded five-signal history and W2-volume qualification

Date: November 4, 2026. Status: selected for E3 implementation; acceptance pending.

## Read interfaces

Keep existing raw history/export/summary responses compatible. Add three read-only routes: `/v1/plot`, `/v1/history-summary` and `/v1/export-page`. Each selects one authorized asset, one frozen snapshot revision, up to five distinct registered signals and at most one simulation day. The new plot format is `history-plot/1.0.0`; new summary/export metadata are separately versioned. No observation, command or stored raw value is changed by a query.

Perform reduction in PostgreSQL, not after loading the whole raw window into the application. SQL, Structured Query Language, is how the application asks the database for rows and calculations. Window functions compare neighboring rows and rank values within groups. Partition by signal/source/epoch; break continuity at invalid/missing/non-numeric data or a missing expected cadence interval. Retain first, minimum, maximum and last original good rows within each bucket and continuous segment, in original time order. Segment identifiers explicitly authorize chart continuity across omitted good readings; they never authorize a line across missing data. Return quality/count/extrema summaries separately and label the reduction.

The plot contains at most 5,000 original scalar points total across all selected signals. Buckets and summaries are also bounded. If many short segments exceed the point budget, refuse the query clearly; never silently truncate or join segments. Raw export is a separate revision/query-bound keyset-paginated path, up to 1,000 scalar rows per page. Its continuation binds the exact signal set, window, page size, asset/run snapshot and final row identity. The browser must verify response scope and apply a separate bounded download size, preserving all exported original values and metadata. An export is not limited to the number of plotted points.

Use existing repeatable-read transactions, role permissions, catalog definitions, registered sampling cadence, immutable raw packets and source identifiers. Existing API/CSV behavior remains available. No new runtime library is required. Add only indexes/migrations justified by measured query plans; never edit an applied migration. Record exact core changes in a successor review layer and requalify affected UI/history/export/replay behavior.

## W2 data and performance protocol

Provision 72,000 events / 1,440,000 scalar rows for ten assets, explicitly as a history-volume fixture. Prefer actual accelerated 7,200-sample SUMO/plant output for each independently stepped source on a separately versioned longer scenario, with real recipes declaring `paced: false`. Direct database seeding is allowed only for this fixture; it does not pass live transport, clock/browser latency or the later real-time soak. Preserve original bytes, deterministic numeric results, quality/provenance and consistent registries/heads/history/current/receipts. Keep source generation and database insertion bounded in memory. Existing source scenarios and contract/catalog versions remain immutable.

Freeze the Q-05 query mix before timing: one asset, five initial signals, a one-hour simulation window at full W2 storage volume and at most 5,000 plotted points. Record cold PostgreSQL-process-buffer cases and warm cases separately, at least 20 requests per case; OS/storage caches are uncontrolled and must be labeled accordingly. Restart readiness time is outside query latency and recorded separately. Include request/response parsing in the primary client elapsed interval, use one clock per interval, retain every measurement, and apply the unchanged nearest-rank p95 <=2 seconds requirement. Verify every returned point against originals, every bucket's extrema/counts and continuity breaks; use independent hand-checkable spike/gap cases as well as the volume fixture.

## Recovery and phase boundaries

Perform an isolated W2-sized database backup/restore with complete schema/data/permission/sequence and representative query checks within 15 minutes. Preserve existing bundle guards; version any format extension for a different recording/queue layout explicitly. Distinguish historical database recovery from physical plant resumption. Current small-queue recovery guarantees remain separately subject to affected-code regression.

Phase 1 live W0/W1 remain exactly 60 warmup +1,800 measured wall seconds under ADR-0022. Neither accelerated generation nor a one-hour historical query launches a one-hour live benchmark. E3 is not complete until actual volume, query, UI/export and restore evidence passes. E4 storage/retention/startup/resource/visual gates and E5 final audit remain separate.
