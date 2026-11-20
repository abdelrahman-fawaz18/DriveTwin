# Browser verification: Windows to the Ubuntu backend

P0-03 checks that a real Windows browser and automated browser tests can reach the test backend and export evidence. The [current acceptance record](../verification/README.md) passed after P0-04's receiver update; the [original record](../verification/README.md) remains historical. This is a connection/data-display check using saved synthetic observations, separate from the full monitoring dashboard planned for Phase 1.

## Understand the connection

```text
Windows Edge or Chromium
  -> http://127.0.0.1:18763/qualification/browser
  -> WSL localhost forwarding
  -> Docker's loopback-only API port
  -> FastAPI query
  -> PostgreSQL observation history
```

**127.0.0.1**, also called a loopback address, means the current computer. A **port** is a numbered destination for a particular service. Here port 18763 selects the test API. Windows and Ubuntu have distinct networking environments; WSL forwards the Windows localhost connection to the Linux service. The browser check verifies the Windows-to-Ubuntu connection directly. [Microsoft WSL networking](https://learn.microsoft.com/en-us/windows/wsl/networking)

An **endpoint** is a particular API address, such as `/v1/observations`. **HTTP**, Hypertext Transfer Protocol, carries the browser's requests and the server's responses. A response status of 200 means the request succeeded. The page requests readiness, catalog metadata, and observation history from the existing API; it does not contain a copy of the expected readings.

The page and API use one **origin**, the combination of address scheme, host, and port. The browser therefore does not need cross-origin security exceptions. The page uses no remote scripts, fonts, or chart service. The tests reject unexpected page requests to other origins. This check observes page traffic; it is not an audit of every background browser or operating-system connection.

## What was built

The [diagnostic page](../../qualification/browser/index.html) shows the selected vehicle, qualification mode, run identifier, registered-signal count, and returned history. It renders observation time, signal name, stored value/unit, and catalog-derived display value. A stored fraction of `0.71` with unit `1` displays as 71%. The dates are artificial fixture history and the page explicitly labels the readings as synthetic.

The [small Python wrapper](../../scripts/platform/browser_app.py) adds this page to the already-qualified API. The [Compose override](../../ops/compose/compose.browser-fixture.yaml) mounts only the diagnostic files and changes that fixture's API command. At the original P0-03 checkpoint the core API, migration, contract, catalog, and existing locks were unchanged. P0-04 subsequently updated receiver validation and reran this browser check against a newly passed data-flow report. This does not choose a final dashboard framework.

**Playwright** is the automated browser-testing library. It opens a browser, reads rendered page elements, clicks controls, and checks the results. Its **assertions** are statements that must be true for a test to pass—for example, the run identifier must match this test's run, and exactly three expected observations must appear. [Playwright browser support](https://playwright.dev/docs/browsers)

The browser represents a page as a tree of elements called the **Document Object Model (DOM)**. The tests inspect the displayed table and identifiers in that tree, not just HTTP status codes. They also retrieve history through the browser's own `fetch` function and compare its event identifiers with the rendered rows. All successful data checks use the real running backend; no API response is replaced with a mock.

**Node.js** runs the JavaScript test program outside the browser. **npm** installs its libraries. [package.json](../../qualification/browser/package.json) selects Playwright 1.64.1; [package-lock.json](../../qualification/browser/package-lock.json) records the exact dependency tree and integrity values. The harness runs `npm ci`, which installs that recorded tree. It invokes the installed CLI directly, avoiding an unpinned tool fetched implicitly by `npx`.

The two test projects run sequentially with one worker:

| Browser | Why it is used | Observed version |
|---|---|---|
| Windows Edge, visible window | Proves an installed Windows host browser reaches the fixture | 156.0.4314.58 |
| Playwright Chromium, headless | Proves the same checks can run automatically without a visible window | 156.0.8078.4 |

**Chromium** is the browser supplied for this automated test. **Headless** means running without a visible window, as with the earlier SUMO check. The Chromium revision is tied to the pinned Playwright release and recorded in evidence. Installed Edge can update independently; its tested version is an observation, not a claim that Windows updates have been disabled. Both browser processes ran on Windows using Node.js 24.18.0 and npm 11.16.0. Linux browser execution and other browser families remain unqualified.

Both tests use isolated browser profiles. Browser binaries, npm cache, temporary profiles, and fixture state stay under ignored `.runtime` paths.

## The network issue found and corrected

The first [attempt](../verification/README.md) reached the database through the internal container network but failed at the Windows address. The [second attempt](../verification/README.md) added narrower diagnostics: even the Ubuntu host could not connect, `docker compose port api 8000` reported `:0`, and there was no listening host socket.

A separate [token-only Ubuntu-server diagnostic](../verification/README.md) passed from Windows using both `127.0.0.1` and `localhost`. That distinguished ordinary WSL forwarding from the Docker publication problem. The server was stopped after its check.

The API was attached only to the network marked `internal: true`. On this qualified Docker configuration, that setup had no usable published host port. The browser override now attaches **only the API** to an additional browser-access network. The database and broker remain on the private network. The API still publishes only `127.0.0.1:18763`; no database or broker port is published. The corrected run recorded that exact mapping, a listening Ubuntu loopback socket, a successful Ubuntu-host request, a successful Windows-native request, and passing browser tests. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)

This is an explicit network boundary in the project configuration, not a recurring command to repair the machine. The API's added network can provide outbound connectivity; this test does not claim an entirely air-gapped deployment or operational access-control qualification.

## Reproduce the checks

Run the verification commands from PowerShell.

1. Open PowerShell in `<checkout>`. The [Linux launcher](linux-launcher.md), [Ubuntu engine](docker-engine.md), and qualified data-flow source must be available. Windows Node.js 24 and Edge are prerequisites.
2. Run this from Windows with a **new** evidence directory:

   ```powershell
   python scripts/platform/qualify_browser.py --evidence-dir evidence/p0/my-browser-check
   ```

   This command intentionally starts on Windows because the host browser is part of this check. The script invokes Ubuntu work through the existing launcher. It installs the locked test dependency, prepares the pinned browser, starts a uniquely named fixture, publishes the known measurement batch, checks both network boundaries, runs both browsers, exports reports, and cleans up. If port 18763 is already occupied, add `--port 18765`; never stop an unrelated service to take its port.

3. Expect a brief visible Edge window. Chromium runs without a visible window. Both are closed by the test runner. The browser checks require:

   - Actual run identity, qualification mode, two catalog signals, and three stored observations.
   - Battery fractions 0.71, 0.70, and 0.69 with display values 71%, 70%, and 69%.
   - Unique event identifiers matching the browser's real history response.
   - Refresh without duplicate rows and a correct empty result for an unknown vehicle.
   - No page errors, failed page requests, HTTP error responses, or unexpected external page requests.

4. Inspect `qualification.json`: `passed` must be true, `test_summary.expected` must be 2, with zero unexpected/flaky/skipped results, and the fixture cleanup lists must be empty. The foreground Ubuntu worker keeps WSL active while the Windows browsers run. Normal completion removes only that run's containers, volumes, and networks; image/browser/download caches remain.
5. Export and validate acceptance using a **new** report filename:

   ```powershell
   python scripts/platform/accept_browser.py --evidence-dir evidence/p0/my-browser-check --report evidence/p0/my-p0-03-acceptance.json
   ```

   The exporter verifies current source fingerprints and the P0-02 prerequisite, checks both test outcomes and their saved data, validates screenshot/trace files, creates convenient named copies, and writes acceptance with artifact hashes. It does not rerun or rewrite the original test results. A prerequisite source change requires the affected qualification.

Available Windows memory increased from about 0.5 GB to 3,799 MiB after unused applications were closed. Sequential browser execution reduces peak demand. The resource readings describe this browser verification session.

## Inspect the saved evidence

The [successful run](../verification/README.md) records two passing tests, zero retries/flaky results, browser versions, real HTTP data, network readiness, source fingerprints, and owned cleanup. The [Edge screenshot](../verification/README.md) was visually reviewed. The [Chromium screenshot](../verification/README.md) records the second rendering.

A **trace** is a detailed record of actions, page snapshots, and network exchanges. It helps distinguish a page bug, wrong data, or a connection failure. The exporter gives the saved traces convenient names such as `edge-trace.zip`. You can inspect a trace locally with the installed tool:

```powershell
node qualification/browser/node_modules/playwright/cli.js show-trace evidence/p0/2026-11-03-browser-verification-03/edge-trace.zip
```

The HTML test report lives in `browser/html-report` under the run's evidence directory. Playwright's local `show-report` command can serve that folder for interactive inspection. Reports/traces are stored locally; viewing them does not require uploading project evidence. [Trace viewer](https://playwright.dev/docs/trace-viewer), [test reporters](https://playwright.dev/docs/test-reporters)

The raw JSON reporter retains its original runtime attachment paths, as Playwright generated them. Named screenshot, trace, network, and history exports and the HTML report are copied into evidence; the acceptance index supplies their persistent paths and hashes, so they remain usable when runtime folders are removed.

This is a short synthetic history check. Live streaming, freshness, disconnect/reconnect behavior, sensor coverage, map rendering, SUMO-to-dashboard integration, operational security, and performance/soak tests remain later work. Normal cleanup passed; hard app termination, interrupted runs, and host power loss are not qualified by this check.
