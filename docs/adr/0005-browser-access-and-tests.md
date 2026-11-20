# ADR-0005: browser access and P0 test boundary

Date: November 3, 2026 (America/Toronto)

Status: P0-03 passed. [Acceptance](../verification/README.md) verifies visible Windows Edge and pinned automated Chromium, real history data, exported evidence, and normal owned-resource cleanup. Final dashboard and Linux-browser CI decisions remain open.

## Decision

Keep app/data services in the existing Ubuntu container runtime. Run P0-03's host-browser tests on Windows with Node.js 24.18.0 and Playwright 1.64.1. Use a separate, private browser-test package with its committed npm lockfile and project-local caches. Test visible installed Edge and pinned headless Chromium sequentially with one worker and no retries. Do not attach to personal browser profiles.

Add a qualification-only static page through an API wrapper and a Compose override. This leaves qualified core Python modules and existing locks unchanged. The page queries real endpoints and renders generic scalar history from catalog metadata; it is not the product monitoring UI or a React/Leaflet decision. See the [reproduction guide](../setup/browser-verification.md).

## Network evidence and configuration

The first two attempts failed before browser execution: internal service queries succeeded, but the isolated API network had no published host listener. A plain Ubuntu HTTP server was reachable from Windows, excluding a general WSL localhost failure in that diagnostic. Adding a second network to only the API produced the expected loopback port and passing Ubuntu-host/Windows/browser checks.

The API publishes only the chosen loopback address/port; the database and broker publish no host ports. This is encoded in `ops/compose/compose.browser-fixture.yaml`. No Windows firewall rule, global WSL networking setting, agent switch is part of the workflow. The additional API network can allow outbound connectivity; operational identity, permissions, encryption, and database role separation are still unqualified.

## Versions, evidence, and limits

Observed Edge: 156.0.4314.58. Pinned Playwright Chromium: 156.0.8078.4. Installed Edge can update; the report records the version actually tested. Browser executables and npm caches are runtime resources under `.runtime`, not a second source tree. Playwright's package metadata declares Apache-2.0 for the installed test libraries; complete bundled-browser/system-license inventory remains part of D-16.

Both tests checked the run identifier, three known normalized battery values and percentage displays, matching event identities, refresh, empty vehicle queries, and page/network error absence. Screenshots, traces, data responses, and machine-readable/HTML reports were exported. The acceptance exporter verifies these artifacts and current prerequisite source hashes. No test response was mocked.

This covers P0-03's local host/browser route and evidence export. It does not close D-10's dashboard/stream selection, D-06's project-wide compatibility matrix, Linux CI browser execution, product monitoring/reconnect requirements, or workload/resource targets. Normal cleanup passed; interruption/power-loss behavior remains unqualified. D-01's stable Windows-agent/multi-chat requirement is preserved.
