# ADR-0014: map context, historical exports and operational views

Date: November 3, 2026. Status: implementation decision; acceptance is recorded separately.

## C2 scope

Add an offline vector map, coherent observed location/trajectory, known planned route/stops and explicitly hypothetical charging stations. Extend history to bounded arbitrary simulation-time ranges, up to four synchronized signal plots, explicit bucket summaries and metadata-bearing JSON/CSV export. Add a recorded event timeline and authenticated operational-health view. Replay remains P1-D; workload, quota/retention/restore and performance gates remain separate. Complete functional coverage is not a claim that all P1 release gates have passed.

Leaflet 1.9.4 is the selected stable map library. GeoJSON is a standard JSON representation of geographic features; RFC 7946 coordinates are longitude then latitude in decimal degrees. Leaflet's native point arrays use latitude then longitude. Convert only at the explicit boundary. The existing SUMO conversion translates its local meter coordinates to geographic coordinates. No new Python projection library is needed. [Leaflet stable documentation](https://leafletjs.com/reference-1.9.4.html), [GeoJSON standard](https://www.rfc-editor.org/rfc/rfc7946).

## Trusted static context and observed data

Build the map context from the reviewed road input and independently authored mission/station configuration before observations start. Use the same reviewed network conversion as the source. Query coordinate conversion without stepping the simulated vehicle. Only publish whitelisted roads, assigned route geometry, stop locations, station assumptions, attribution and input hashes. No sensor seed, future trajectory or plant truth enters map context. Store immutable bytes/hash under the assigned run/asset through trusted deployment provisioning. Reader scope applies; an unassigned vehicle gets no borrowed mission context. Include station configuration in source input fingerprints.

Planned lane shapes illustrate the assigned route; actual lane choice and legal road access are not inferred from this layer. Stations have hypothetical locations/power limits and unvalidated road access, not verified availability. Preserve OpenStreetMap attribution/ODbL terms. No external tiles, fonts or images are fetched. Leaflet needs element-position styles: permit inline style attributes while retaining same-origin stylesheets/scripts and blocking inline scripts.

Pair navigation coordinates from the same accepted event/source/epoch/time, never independently updated scalar values. Missing/invalid/incomplete coordinates, source/epoch changes and timing gaps break observed trajectory segments. A bad current location may show a clearly labeled historical last valid position; never call it current. Source pause, stale readings and browser disconnection remain distinct. Trajectory/history pages are anchored to one snapshot revision and preserve raw accepted data. Queries that exceed point caps return an explicit error.

## Historical analysis and exports

Historical query controls freeze the selected snapshot, range and signals until the user applies another query. Charts use a common time axis and cursor but retain separate stored-unit vertical axes. A cursor without an eligible nearby sample shows a gap. Raw data, min/max display reduction and explicit statistical aggregation are different operations. Summaries count valid/invalid/missing readings and compute statistics from good values only; an empty bucket has no numeric value. Bound the number of signals, time span and raw rows before computing or exporting.

JSON exports carry query, mode/run/asset, revision, policy hash, catalog/schema/model and recipe provenance with the data. CSV uses a record-type column: one metadata row and then data rows, retaining metadata even for an empty result. Escape spreadsheet formula-like text while preserving numeric values; document that text escaping. Export is an authenticated read, not a command. No hidden model state is queried.

## Events and health

Append safe, asset-bound registration, declared source-state, quality and rejection summaries in the same transaction as their original records. Migration 005 extends quality auditing to forward recovery while preserving applied migration 004. Historical unbound rejected bytes stay private and are not guessed into an asset scope. Read events through bounded revision-anchored pages. Browser connection events are page-local and labeled accordingly. Heartbeat expiry is calculated in current status; it is not retrospectively inserted as a declared source event.

Expose queue/broker/disk observations through a small authenticated internal metrics endpoint on the sender and receiver. This endpoint is on the isolated service network, not a host-published port; its credential stays backend-only. The reader API checks run/asset identity and marks failed/expired metrics unavailable. Database counts/rate windows remain asset-scoped. Report uninstrumented drops and unenforced physical quotas as unknown/pending, not fabricated zero/success. This is local operational visibility, not remote TLS, full disk-pressure recovery or workload qualification.

Required evidence includes map coordinate/order/scope/provenance checks, actual accepted navigation history, gap/invalid/zero/pause/reconnect behavior, export metadata/query bounds, summary correctness, pagination stability, role negatives, real internal metrics plus unavailable cases, event provenance, both browsers, responsive layouts, historical-byte preservation, source fingerprints and owned cleanup.
