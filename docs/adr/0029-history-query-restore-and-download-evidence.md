# ADR-0029: Independent historical query, restore and browser-download evidence

Date: November 4, 2026. Status: selected; small method pilots and complete W2 query/browser-export/restore acceptance passed. See docs/verification/p1-history-acceptance.json.

## Fixed query method

Freeze `benchmarks/history-query-cases.json` before collecting results. Its twenty cases cover both historical hours of all ten assets in a fixed order. Each request selects the same five initial signals and a 3,600-second interval, with at most 5,000 total original plot points. Inclusive endpoints follow the existing API contract: the second hour includes its boundary observation at second 3,600. All original workload and quality targets remain unchanged.

Cold means PostgreSQL is restarted and ready before each measured request. The API process and operating-system/storage caches are uncontrolled; do not call this a physically cold disk test. Warm means one identical, uniformly applied priming request immediately precedes each measured request without another PostgreSQL restart. Priming must succeed, its response is retained and independently checked, and its elapsed time is reported separately. Never select a favorable warm result or omit a slow/failed measured case.

Measure one client monotonic interval around the HTTP request, bounded response read and JSON parsing. Independent reference construction/validation is outside that interval. Require all twenty cold and twenty warm cases at W2 volume and nearest-rank p95 <=2 seconds in each series. Preserve response bytes, fingerprints, actual durations, priming results and failures. A smaller checkpoint can qualify the method only; its flags cannot pass Q-05.

## Independent numerical checks

Stream the original source packets and verify bytes, identities, recipe binding, sequence and count. Use an independently written Python grouping algorithm to reconstruct each selected bucket/continuous segment and choose original first/minimum/maximum/last points. Compare every returned value, unit, identity, source/epoch, order, segment, original observation time, extrema and quality count. Separately verify bucket summaries, including empty intervals and missing/invalid values. Boolean false is not numeric zero. Fragmentation beyond the point budget cannot be hidden by truncation or interpolation.

Window/time statistics are normalized to the API/database's declared double type in the reference, without changing original sample values. Original values remain exact JSON numeric values. Only aggregate mean fields allow relative/absolute 1e-12 summation roundoff; counts, endpoints, extrema, original samples, scope and metadata remain exact. This is explicit before timing. Unit negatives cover zero, peaks, bad/missing data, recovery, cadence/epoch gaps, wrong units/values/scopes/order, omitted points, corrupted extrema/means and unchanged percentile denominators.

## Independent history restore

Use `pg_restore` on the sealed custom archive, with `--single-transaction --exit-on-error --no-owner`, into a newly created, uniquely owned empty cluster. These flags apply one transaction, stop on SQL failure and assign restored ownership to the selected existing target administrator. The pinned PostgreSQL image and exact recorded application image are required. No original target is overwritten and no existing development-backup guard is weakened. See PostgreSQL's [archive export](https://www.postgresql.org/docs/18/app-pgdump.html) and [restore interface](https://www.postgresql.org/docs/18/app-pgrestore.html).

Create fresh role/reader credentials outside the checkpoint. Verify different cluster identity, actual same-cluster equality refusal, rolled-back nonempty schema/type probes, restored nonempty-target refusal and complete data/schema/ACL/sequence inventory equality. Check actual restored table counts against declared assets/events/scalars/receipts/current/registrations/clocks and the sealed count evidence. Run saved plot/summary requests, real restricted-role mutation denials, old database-password denial, old/absent HTTP-token denial and scoped-reader denial.

The 900-second timer starts before checkpoint verification and ends after archive restore, complete inventory, representative API reads and credential/permission checks. Cleanup is outside that timer but is required for an accepted attempt. Optional additional query/browser work starts after the timed restore boundary. A failed additional check fails the overall controller; a deliberately failing real callback was exercised to prove this behavior. Earlier prototype failures remain preserved and unaccepted. A pilot restore is not Q-10 at W2 volume.

## Actual browser downloads

Keep a verified restored read API alive for visible Windows Edge and pinned automated Chromium. Use the existing Windows-native agent/WSL workflow. Each browser opens the first and last included asset, selects five signals, checks actual charts/cursor/summary, uses the product's JSON/CSV download buttons and records the page-request chain. Require frozen scope and a complete download, then compare every downloaded scalar value, unit, timestamp, source/session ordering, quality and recipe/schema/catalog reference with original packets. Python's standard CSV parser handles quoted fields and embedded newlines; JSON/CSV metadata must agree. At full volume each first-hour five-signal download must contain 18,000 rows from eighteen 1,000-row pages.

Keep credentials in memory/headers, browser storage empty, web requests on the fixture origin, and private downloads/screenshots/traces/control files ignored. Preserve all failed attempts and partial diagnostics; do not publish. Desktop/mobile controls and document overflow are checked; formal visual baselines and mobile chart-axis readability remain E4 work. No new dependency, runtime route, wire schema, catalog or migration is introduced by these test tools.
