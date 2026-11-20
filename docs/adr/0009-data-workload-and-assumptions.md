# ADR-0009: initial data, workloads, and assumptions

Date: November 3, 2026. Status: initial P0-06 design selections, subject to recorded qualification scope.

## Decisions

- D-07: generic synthetic 12 m bus with explicit assumed parameters; fixed project-owned mission on a reviewed local Montreal OSM subset. No manufacturer/operator model or official service route is claimed. Station positions/capacities are hypothetical. Calibration/operational suitability is later evidence.
- D-11: versioned W0–W5 rates/clocks/counts, per-signal freshness, clock-error admission, queue/retention/disk/memory/swap policies in [workloads and metrics](../workloads-and-metrics.md). Limits define future tests, not achieved performance. Keep all initial supported scalar IDs and the held-back extension explicit.
- D-16: incorporate the attributed ODbL geographic subset and independently authored scenario/parameters. Inventory declared locked software terms/notices. Select jsonschema's documented `format-nongpl` extra to remove an unused optional GPL parser; preserve behavior through qualification. Original DriveTwin work now uses the [MIT license](../../LICENSE). Distribution of compiled artifacts requires the applicable component notices.
- D-18: retain model-accuracy thresholds as a P2/P5 decision before held-out evaluation; no unsupported accuracy percentage is invented during platform setup.

## Important tradeoffs

The imported graph is a reproducible development fixture with known turn/junction/signal/stop warnings. Freeze/review these messages; unexpected changes fail qualification. This accepts a fictional movement experiment, not legal or safe real-world bus routing. Geographic round-trip precision is different from GPS accuracy. SUMO owns movement/initial energy under ADR-0008; energy/thermal physics are not validated here.

Use a fixed local snapshot after acquisition; no live service dependency for tests. Keep original downloaded account metadata ignored and only the reviewed road subset in Git. Code and geographic-data licenses have separate scopes. New data or map revisions require provenance/rights/quality review and a new qualification.

The earlier 30-day development raw-retention proposal is replaced by at most 48 hours or 32 GiB, with a separate 90-day/8 GiB trend quota. The first reached quota controls; expose the achieved retained interval. A 100 GiB project disk planning budget and 20 GiB host reserve bound generation. Byte estimates are encoding arithmetic; actual database/WAL/backup/spool overhead is a P1/P6 test.

W1 carries 200 readings/s in 10 packets/s. At the declared 8 KiB event cap, 30 minutes requires at most 14,745,600 encoded bytes per asset. Allocate byte/disk/critical reserves, then test actual durability and full-disk behavior. Rate, field-count or payload changes need an explicit workload/profile revision.

## Acceptance and later gates

P0-06 requires data source/rights records, explicit assumptions, checked workload arithmetic/metric failure behavior, two actual repeatable Montreal movement runs, reviewed warnings, coordinate/continuity checks, and affected dependency regressions. Preserve failed attempts and raw results privately; publish sanitized summaries.

Passing this gate permits P1 monitoring implementation. It does not pass W0/W1 performance, the two-hour soak, data-loss/recovery guarantees, source-clock accuracy, database restoration, calibrated battery/thermal models, command safety, public deployment, hosted CI, or final redistribution clearance.
