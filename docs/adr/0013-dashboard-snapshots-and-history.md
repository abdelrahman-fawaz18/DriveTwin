# ADR-0013: consistent dashboard snapshots and bounded raw history

Date: November 3, 2026. Status: implemented design; acceptance is recorded separately in P1 status.

## Scope and tools

P1-C1 is the first product interface: overview, generic measurements, selected live/history trend, inspection of recorded readings, and connection recovery. P1-C2 will add the map/trajectory, planned route/stops/stations, arbitrary historical query/export and remaining event/system-health coverage. Replay retains P1-D. These checkpoints do not reduce the main plan's release requirements.

Use React 19.3.1 for reusable interface components, TypeScript 6.0.3 for checked data types, and Vite 8.3.5 to produce static browser files. Exact dependency versions and integrity hashes are locked in `apps/dashboard/package-lock.json`. Prettier 3.6.2 formats source consistently; formatting is not behavior verification. React's generated production notices and all exact dependency terms require release review. No third-party visual assets or external fonts/tiles are requested in C1.

The existing read API serves the compiled dashboard and its APIs at the same origin. An origin is the protocol, host and port: keeping these together avoids a separate cross-origin credential policy and another production service. Node/Vite are build tools, not additional running application services. The simulator image still excludes browser build artifacts. Static files contain no reader credentials.

## Transaction and cursor boundary

A transaction is a group of database changes that commits together or rolls back together. Migration 003 adds a per-run/asset revision row and a derived per-signal history table. A database trigger is a function automatically invoked by a specific table change. Here triggers allocate a revision for observations, registrations and clock events; accepted observations also derive sample-history rows in the same transaction as the original bytes/current state/receipt. The original observation remains authoritative. There is no second independent history writer.

Updating the revision row holds its PostgreSQL row lock until the writer ends. Another writer for the same asset waits, avoiding a cursor allocated before an earlier writer commits. Revisions are scoped `run:asset:number`, bounded to JavaScript's safe integer range; gaps are allowed (for example a conflicting insert whose transaction records a rejection). Do not interpret revision difference as an observation count. Migration backfill numbers existing observations deterministically by persistence time/event ID; it does not reconstruct their historical commit order. Original packet bytes and old migration files remain unchanged.

Snapshots use a read-only **repeatable-read** transaction: all queries see one committed database view, even if a producer commits between queries. Read the revision, policy, source clocks and current readings from that view. Evaluate freshness against one database UTC timestamp. PostgreSQL explains the guarantee in its [transaction isolation documentation](https://www.postgresql.org/docs/current/transaction-iso.html). This does not qualify clock agreement across machines.

## Live delivery and recovery

Server-Sent Events (SSE) is a one-way HTTP stream of text events. `/v1/stream` sends a full replacement snapshot once per second, including during source pauses and after source completion so age-based stale transitions remain visible. This intentionally coalesces interface redraws; it does not discard accepted history. One event contains run/asset/cursor identity. There is no misleading claim of replaying every intermediate snapshot.

The browser uses `fetch` with an Authorization header to read SSE; credentials never enter a URL. Cursor reuse is scoped and validated. On connection failure or a 4.5-second update watchdog, mark the display stale and retry with bounded exponential backoff plus a small random delay. On reconnection, replace state with an explicitly labeled resynchronization snapshot. A scope/obsolete-cursor conflict requires a new snapshot. Asset changes abort old work and clear old display state. Runtime identity/shape/finite-number checks reject malformed updates; TypeScript alone does not validate incoming network data.

The source's health is separate from the browser's connection. A connected dashboard can show an ended or disconnected source. A fresh source pause must remain distinct from stale data. Neither connection recovery nor a good historical reading makes a newer missing/invalid reading good.

## Historical queries and plots

`/v1/history` requires reader scope, signal ID, simulation-time window and a snapshot cursor. It returns raw readings, up to 1,000 per page, in `(simulation time,event ID)` order. A continuation records the same asset/run revision, signal, bounds and page size. Every page filters observations to the original revision: a late-arriving older-time reading cannot move into an already paginated historical result. Query windows are limited to one simulated day; this is a query bound, not a measured performance guarantee.

The first UI offers 60-second, five-minute and thirty-minute windows, follows continuation pages and caps a window at 5,000 readings. It explicitly reports unavailable or oversized windows. History refreshes every four seconds independently of live redraws. Inspection shows the latest 100 readings within the selected window, including observation/receipt UTC, provenance, quality, reasons and optional raw invalid values.

Plots preserve good zero. Missing/invalid values and cadence gaps break line segments. Source/epoch changes also break segments. Reducing dense good segments retains each bucket's minimum/maximum in time order and both endpoints. Axes declare stored units and simulation seconds. This is display reduction, not stored-data aggregation or an interpolated model. Multi-signal synchronized cursors/aggregate exports remain subsequent dashboard work.

## Access, compatibility and acceptance

Dashboard review exposed a preexisting heading conversion defect: TraCI `getAngle` returns degrees, while the catalog has always declared radians. Convert at the simulation adapter boundary and independently compare every private plant-trace heading against the original SUMO degree value times pi/180. [SUMO's vehicle value reference](https://sumo.dlr.de/docs/TraCI/Vehicle_Value_Retrieval.html) specifies degrees. This fixes emitted values without changing a wire format or rewriting old packets/catalog versions. Older pre-C1 run headings are mislabeled and unsuitable for orientation analysis; preserve them as historical evidence with that limitation. The current catalog's permissive heading upper bound remains a future versioned tightening, not a reason to accept new degree-valued source output.

Existing asset-scoped reader authentication remains mandatory on snapshot, stream and history routes. A reader cannot change data. The local reader credential stays in page memory only; refresh/logout ends the browser session. Responses disable caching and add a same-origin content policy; no query token, persistent browser token, external service or command route is introduced. Remote TLS, production login/session revocation, network-facing hardening and multi-user load limits remain future gates.

Retain old API paths and observation contracts. Build from pinned Node/Python/uv images and exact locks. Required checks cover strict TypeScript, chart/stream/identity tests, scoped APIs/query limits, actual SQL backfill preserving packet bytes, transaction isolation/rollback/writer serialization, real 20-signal delivery and raw pagination, and real Edge/Chromium offline/reconnect/asset-switch/layout behavior. Record source fingerprints and owned cleanup. A screenshot supplements assertions; it is not proof of correct data or a qualified visual-regression baseline.

## E2-B refresh amendment

The original C1 one-second loop remains historical behavior. E2-B observed incomplete per-event DOM coverage and selects a 250 ms elapsed-time-aware refresh budget while retaining replacement snapshots, scoped cursor validation and explicit resynchronization. This does not promise every intermediate state under arbitrary overload. Complete nominal rendered-event coverage and full W1 latency remain separately measured gates; see [ADR-0020](0020-observation-stage-and-resource-measurement.md).
