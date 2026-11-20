# ADR-0072: Local launch workspace

Date: November 13, 2026. Status: implemented.

## Decision

Provide a separate loopback-only launch application for selecting prepared journeys and their compatible services. A double-click Windows entry point opens the page. Reuse the guarded live geographic runner for source checks, host admission, recording, resource ownership, and cleanup.

Keep launch authority separate from monitoring readers, replay readers, and vehicle command roles. The local application accepts a catalog identity and an allowlisted service list. It never accepts an executable or arbitrary shell text. Authenticated requests require the expected local host and same origin. The server serves a fixed asset inventory and no filesystem browser.

One active run holds the existing live-workload lock. Start fixes the selected preset and services for that run. Stop requests graceful termination through an owned marker. Reopening the launcher restores its run reference. An unverified cleanup result blocks another start. Recovery takes the same lock, keeps the trip's volumes, and permits another start after verifying that the interrupted trip's containers and networks are gone. Browser tab closure does not stop a run.

Trip pacing accepts 0.5×, 1×, 2×, 5×, 10×, and 20×, with 1× as the default. The selected factor is fixed at start, validated as a numeric allowlist value, and saved with the run. It scales monotonic wall-clock waits without changing integration steps or sampling cadence in working time. Qualified synchronized workloads and speed-command profiles remain at 1×. Forecast age limits and worker deadlines are unchanged. A slow host may deliver less than the requested pace.

The launch page uses the established dashboard colors, typography, rounded cards, and sidebar. It shows the selected road alignment and operational settings before launch. The launcher exposes one dashboard credential for the chosen role. A request from the fixed monitoring origin must present that credential for the active run to obtain a replay-only handoff. The replay service keeps its own read scope and the browser removes the handoff from the address bar. Documentation and verification distinguish page checks, runner lifecycle checks, and complete-trip evidence.

Keep a credential-free local session index and discover earlier geographic live recordings from their retained profiles. Show trip outcome, verified backup presence, and cleanup independently. Opening a results directory accepts only an indexed run identity resolved to an owned folder. Reusing prior settings selects a new run configuration without starting it. Store trip and service preferences in browser local storage; keep access credentials out of that preference record. Stop requires a page confirmation and retains the existing graceful-stop mechanism.

The launcher accepts pause and resume for the active, ready drive. A request carries its run identity and the last observed control revision. The source reads an atomic request at the end of an observation and confirms it only after publishing the authenticated clock transition. It sends paused heartbeats at fixed working time and shifts the pacing origin on resume. Stop and the real-life session deadline remain effective throughout the pause.

The pinned monitoring bar uses a separate random capability scoped to the one launcher-owned run. The launcher passes it in the dashboard link fragment. The browser removes the fragment and stores the capability for that tab. Only the geographic monitoring origin can call the launcher control endpoint from a browser. That endpoint returns the run, asset, and pause state and accepts only pause and resume. Reader credentials do not grant control. The capability expires when the launcher-owned process ends.

Generate the launcher and live map layers from the same prepared road network, trip endpoints, and mapped passenger-stop plan. Load the selected launcher map on demand and cache its projection against the prepared input and projector fingerprints. Serve local streets with Leaflet from the installed dashboard dependencies. Both maps distinguish passenger stops, trip endpoints, and charging stations.

## Service scope

Monitoring is mandatory. Continuous Energy Forecast and replay are optional on all ten installed geographic presets. For localized speed restrictions, the trusted runner appends a route-bound assignment after receiving an authenticated source plan and whenever the remaining stop plan changes. The controller rejects mismatched revisions until the update is committed. Restrictions follow ordered road visits, including repeated roads and junction connectors. The runner retains assignment authority; no administrator credential is added to the controller or worker. Control and thermal workflows retain their existing configuration and authority boundaries. A new service choice requires a compatible preset, actual runtime wiring, and affected qualification.

## Consequences

Normal operation no longer requires command entry. The launch application adds a small local Python process, its own temporary capability, and a separate port. It does not weaken memory safeguards or change the predictor. The catalog uses retained local inputs; publication packages must supply or prepare compatible inputs before those presets can run on another machine.
