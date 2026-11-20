# Local launch workspace verification

Historical connected results below retain their original source and operating
scopes. Completed raw recordings were subsequently retired under
[ADR 0075](../adr/0075-minimal-historical-retention.md); summaries and consumed
evaluation identities remain. Inspection requiring those retired payloads is
unavailable from the reduced tree.

## Saved-state validation review

The final folder review found that an invalid existing `current.json` could be
treated as idle or crash status loading. The launcher now bounds and validates
the saved ownership reference, preserves invalid bytes, displays cleanup review,
and blocks another start. An absent file remains normal idle state. Invalid
history timestamps and deeply nested metadata are skipped without crashing the
history page. Startup rejects malformed connection/status records and requires
known idle activity before closing an outdated service. Launcher API version 16
keeps the page and service behavior aligned.

All 25 current input-preparation cases passed, covering ten energy profiles,
ten charging-advice profiles, and five reviewed operator variants. These checks
prepared networks, geometry, map context, and assignments in disposable storage;
they started no simulator, database, forecast worker, or operational service.
All ten actual launcher map endpoints and empty saved-session History passed.
The launcher browser checks passed at desktop and narrow widths, including the
new damaged-status warning and disabled Start. Every browser mutation request
was intercepted. The exact preference allowlist now includes the implemented
noncredential advisor-delay setting and asserts its restored zero-delay value.

The shared source-stability inventory also binds the launcher assets, browser
qualification scripts, and Windows entry point, so changes to those files during
a check invalidate its source-consistency result.

The final locked offline suite passed 1,292 Python tests with 373 subtests,
68 frontend tests, 339 portable cases with zero mismatches, 81 monitoring-parity
cases, formatting/lint, strict type checks, build, privacy, and secret scans.
This qualifies saved-state handling and current preparation/UI paths; it is not
a new live-trip, model-accuracy, recovery-duration, or resource qualification.

## Original scoped checks

The launch workspace selects a prepared real-road trip and compatible services, starts the existing guarded runner, exposes reader access, and requests a graceful stop. It does not change energy-model parameters or earlier evaluation claims.

| Check | Result |
|---|---|
| Launch authority and HTTP boundaries | Passed: missing/wrong credentials, foreign origin, unexpected host, cross-site requests, malformed mutations, and arbitrary paths are rejected |
| Run ownership and lifecycle | Passed: duplicate start rejected, early stop retained, saved run recovered, expired reader access removed, and missing cleanup blocks a new run |
| Service selection | Passed: browser choices are checked against preset capabilities; no shell command passthrough |
| Source behavior | Sixteen focused source/route-capture tests and three subtests passed |
| Desktop and narrow-screen browser behavior | Passed: ten presets, regional filtering, service availability, route outline, visible reader keys, Start/Stop requests, and no browser errors |
| Preset input preparation | Passed for all ten trips: fingerprints, network and route preparation, map context size, and supported energy assignments; no bus started |
| Shared engineering checks | Passed: 1,120 Python tests, 237 subtests, 44 frontend tests, 339 contract cases, and 81 monitoring parity cases; strict type checking and privacy/secret scans passed |
| Saved-session workflow | Passed: earlier-run discovery, completed/partial/failed outcomes, backup presence, search, filtering, pagination, and indexed-folder boundaries |
| Run controls and preferences | Passed: stop confirmation and cancellation, progress stages, settings reuse without starting, and trip/service persistence without credentials |
| Connected launch with Energy Forecast and replay | Passed: page Start, twenty readings, live map, current forecast, reader access, page Stop, partial recording backup, and cleanup |
| Connected monitoring-only launch | Passed: twenty readings and live map, with the forecast controller and replay service disabled; page Stop, partial recording backup, and cleanup |
| Windowless desktop entry point | Passed: startup, repeated-open reuse, authenticated status, and idle Close; no bus started |

The browser-only check intercepts mutations. Its desktop images show the real page and declared test lifecycle states. Raw screenshots and its report remain in `.private/design/launch-app/`. Python tests use disposable local state and temporary loopback servers. No bus or container is started by those tests.

## Connected lifecycle checks

Both checks ran from the actual launch page on November 13, 2026, using source commit `0924d84` and the unchanged host safeguards.

| Trip | Selected services | Accepted observation packets | Retained run |
|---|---|---:|---|
| Gare Centrale–Parc La Fontaine | Monitoring, Energy Forecast, replay | 13 | `live-journey-20261113T181803Z-e7f810` |
| ByWard Market–Place d'Orléans | Monitoring | 4 | `live-journey-20261113T182105Z-38ef7a` |

Each packet contains twenty readings. The first run completed a forecast calculation and exposed its result through the Energy Forecast tab. The second run omitted the forecast controller and replay access. Both runs were intentionally stopped after startup verification. Their trip reports retain `stopped_incomplete`; the lifecycle checks passed because startup, viewing, stopping, and cleanup met their expected outcomes. These short checks do not establish complete-trip endurance.

Both runs saved a private recording backup, released temporary sleep prevention, and reported no runtime host-guard failure or finalization error. Independent Docker inspection found no remaining Compose containers, networks, or volumes after cleanup. Raw reports remain under ignored `evidence/energy-v2/`; browser and windowless-launch reports remain under `.private/reviews/launch-app/`. The shared-check report is `.runtime/dev-checks/4961a2820324495dbfcb5ddc528547bd/report.json`.

The operating instructions and supported service combinations are in the [launch guide](../setup/launch-app.md). Earlier live-run resource stops remain recorded in the [geographic viewer guide](../setup/live-geographic-journey.md).

## Localized forecast assignment update

The Orléans preset now supports live Energy Forecast. The runner reads the authenticated input snapshot, verifies the original network and ordered road sequence, and provisions an append-only assignment for the current stop-plan revision. A pending or mismatched assignment prevents calculation. The existing input builder maps full-route restriction indices onto the remaining route. The controller and worker retain their existing permissions and numerical model.

Focused checks cover the initial assignment wait, stop-plan changes, commit ordering, repeated roads, junction connectors, overlapping restrictions, and foreign route/source rejection. A separate source test verifies that the restricted speed ends at its assigned road boundary.

The real-road projection check passed at twenty retained Orléans states, including all thirteen completed stops and both boundaries of the restricted section. Each remaining road occurrence and junction connector matched its declared speed factor. The checked source states span 0.2–2,303.6 working seconds. The original study results remain unchanged; this check evaluates input projection and does not create a new forecast-accuracy claim. Evidence: `orleans-restriction-projection-7fe93c4d` under ignored `evidence/energy-v2/`.

The browser-only check passed with Energy Forecast available on all ten presets. Shared checks passed on source commit `38abb41`: 1,112 Python tests, 225 subtests, 44 frontend tests, unchanged contract parity, strict type checking, and privacy/secret scans. Report: `.runtime/dev-checks/60992dd00dfb4f2b94298960ccf5738e/report.json`.

The connected Orléans check passed with 176 observation packets containing all twenty signals. At two working seconds the runner bound the initial thirteen-stop plan. At 128 working seconds it committed a new restriction assignment for twelve remaining stops. A current forecast at 167 working seconds confirmed continued calculation after the first stop. The controller completed ten jobs with no failed jobs. The operator then stopped the run intentionally; its report retains `stopped_incomplete`. Recording backup, host guards, sleep-request release, and independent resource cleanup passed. Evidence: `live-journey-20261113T184819Z-b5848d`. This bounded check verifies the live transition; the twenty-state projection check covers the rest of the route without another full-length drive.

## Saved sessions and run controls

The launcher now indexes local live-journey recordings independently of its current run. Eighteen focused tests with twenty-six subtests passed for launch authority, restart recovery, saved outcomes, earlier-run import, pagination, missing backups, malformed records, and rejection of redirected or unindexed result paths. Folder-opening tests use an injected opener and verify the exact resolved directory; the browser check intercepts the open request.

The desktop browser check passed at 1920 × 1080, with an additional 390-pixel layout check. It verifies stop confirmation and cancellation, saved-session search and filters, multiple history pages, unchanged refresh preserving expanded details, and reusing settings without launching a trip. Browser preferences contain only a trip identity and the two optional service choices. The real API and page separately discovered five retained sessions, including four saved database recordings. The latest Orléans entry retained its partial-trip result and verified cleanup. Readback report: `.private/reviews/launch-app/saved-session-readback.json`. Desktop screenshots and the browser report remain in `.private/design/launch-app/`.

The first shared check exposed a test fixture that assumed a local temporary directory existed. That attempt remains at `.runtime/dev-checks/1c4fb10e1df84707842e4db5cfc8b448/`. After making the fixture work in a clean build, all shared checks passed: 1,120 Python tests, 237 subtests, 44 frontend tests, 339 contract cases, 81 monitoring parity cases, strict type checking, and zero privacy or secret findings. Final report: `.runtime/dev-checks/d5aaa93617644e8e865c5691982b89d8/report.json`.

These changes affect launcher metadata and page controls. The live runner, source, forecast model, telemetry contracts, and recording backup mechanism are unchanged from the connected checks above. Their evidence remains applicable; no further bus run was needed for the saved-session interface.

## Trip duration labels

Each preset now shows separate working-time and real-life-driving estimates in the trip list, small-screen selector, and selected-trip details. Both use the retained trip's recorded working duration at the launcher's normal 1× pacing. Additional startup, five-minute history viewing, and saving/cleanup time are listed separately. Browser checks passed for both labels on all ten presets, selected-trip updates, desktop layout, and small-screen overflow.

The [selectable pacing update](launch-pacing.md) adds 0.5×, 1×, 2×, 5×, 10×, and 20× with dynamic real-life estimates and a 1× default. Browser checks passed for all ten trip cards at all six rates. The corrected connected one-bus sequence passed at every rate, preserving 69,600 readings and the existing forecast freshness limits. The 20× setting achieved 15.313× on this host. Current forecast availability was intermittent at 10× and 20×; the pacing record reports the observed samples and acceptance scope.

All ten installed profiles passed a fresh fingerprint and service-compatibility check. Their displayed durations matched their completed geographic study results. The read-only review is `.private/reviews/launch-app/trip-time-verification.json`. This presentation change does not alter the runner or energy model and starts no bus. Full-length connected launcher runs across all ten presets remain unverified; the completed geographic batch and bounded connected checks above retain their separate scopes.

## Start feedback and dashboard opening

The launcher now reports its API version. A page served by an older background process disables Start and explains how to reopen the application. Reopening replaces an outdated idle service through its authenticated shutdown endpoint; an active trip remains running. The observed idle service was refreshed successfully, retaining its prior partial-run record and verified cleanup.

A rejected Start focuses its visible error. A successful Start shows preparation progress and opens the monitoring dashboard when reader access becomes available. The automatic request uses the current run identity and launch authority. The server accepts only the fixed monitoring URL, copies its reader key through standard input, and records the open operation to prevent duplicate tabs. Visible keys and manual open controls remain available when browser or clipboard operations fail.

Focused checks passed: 29 Python tests and 37 subtests in Ubuntu; the Windows suite passed with one existing platform-specific skip. Coverage includes version mismatch, graceful idle shutdown, active-run preservation, ready/current-run validation, URL restriction, launch authority, clipboard failure, browser failure, and duplicate-open prevention after restart. The browser check passed at desktop and narrow-screen sizes, including rejected Start, readiness polling, refresh during startup, one automatic open request, and stale-service feedback. All browser mutation requests were intercepted. No vehicle or container started during these checks; the pacing and forecast qualification evidence remains unchanged.

## Pause, passenger stops, and the vehicle bar

Focused Python checks exercise source heartbeats at frozen working time, acknowledgment of resume, stale revision rejection, and stopping during a control transition. Browser checks exercise the launcher controls with intercepted requests and verify the pinned dashboard bar at desktop and mobile widths with fixture data. All ten installed maps load from the retained OpenStreetMap inputs. Each contains one planned route, two trip endpoints, and the passenger-stop count from its prepared stop plan. The first trip's dashboard and launcher each display four passenger stops.

The first live attempt stopped at host admission before starting SUMO. Windows available memory reached 1.54 GiB against a 2 GiB launch reserve. The next attempt passed host admission and exercised the Gare Centrale to Parc La Fontaine trip at 5× with Energy Forecast. SUMO held at 19 working seconds through the pause. The monitoring API retained the same observation and reported paused freshness after more than six seconds. Resume produced fresh observations and a current forecast. A second pause was confirmed at 28 working seconds before the operator stopped the trip.

The original source recording contained 28 consecutive one-working-second observations, each with all twenty signals. The runner saved the database backup, removed the owned resources, and released sleep prevention. The Windows runtime memory guard also reported available RAM below 2 GiB for over 30 seconds. This run supplies historical connected control evidence and does not qualify resource performance. The launcher reports that guard failure even when a stopped trip has a verified backup. Raw payloads for `live-journey-20261114T000812Z-0299d5` and `live-journey-20261114T002208Z-63a563` were later retired under ADR 0075; their outcomes remain summarized.

Stop confirmation includes the current trip identity. A delayed confirmation for a different trip is rejected. The browser ignores status responses issued before a control request and keeps the pause button disabled until the latest request is confirmed.

## Monitoring trip controls and loading feedback

The monitoring toolbar accepts pause and resume using a separate per-trip capability. Focused tests verify expiry, cross-origin restrictions, and rejection of reader credentials and launcher-wide actions. Browser checks verify pending and confirmed button states, loading feedback with reduced motion enabled, and click-away and Escape behavior in the History signal picker.

The forecast chart marks boundaries between comparable forecast segments. In the retained Gare Centrale to Parc La Fontaine run, the 185-working-second forecast still included Sherbrooke / De Bleury. The 215-working-second forecast used the updated remaining stop plan. The displayed break preserves that distinction.

An attempted connected check exposed a Python module name collision when generating the trip key from the Windows entry point. Key generation now uses the same system randomness as the launcher, before it starts the runner. The attempted trip stopped at memory admission with 1.94 GiB available against the 2 GiB reserve. The toolbar was exercised in Chromium with recorded forecast data and simulated control responses. A live browser check of the dashboard-to-launcher handoff remains pending.
