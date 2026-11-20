# Prepare and open the interactive browser demonstration

The demonstration is a separate static site. A visitor needs a modern browser and a link; SUMO, Docker, PostgreSQL, credentials, and model workers are not required on the visitor's computer. The local live dashboard and authenticated replay keep their existing behavior and permissions. [Design](../design/branched-browser-demo.md) · [Contract](../design/browser-demo-contract.md) · [Verification status](../verification/browser-demo.md)

## Current readiness

**The two-outcome demo is available online:** [open the recorded demo](https://drivetwin-demo.pages.dev/). Its deployed file fingerprints and both decisions passed anonymous HTTPS verification. The November 17 matched Montréal recordings passed the physical-history, charging-effect, browser, privacy, and packaging checks. Open the existing accepted local site with `python scripts/demo/serve.py`; preparing another pair is unnecessary for normal use. [Measured outcomes](../verification/browser-demo.md#accepted-matched-recordings)

## Prepare a new local case

### Select an existing verified local candidate

`python scripts/demo/serve.py` first reads the explicit current receipt at `.private/browser-demo/current.json`. The receipt binds the site to its verified file inventory and actual-case browser/privacy/package reports. If no current receipt exists, serving retains the historical latest-`READY.json` discovery path. An invalid explicit receipt fails instead of silently serving an older candidate.

To select an already verified candidate, run in Windows PowerShell from the project root, replacing both example paths with the existing matching artifacts:

```powershell
python scripts/demo/current_demo.py --site .private/browser-demo/reviewed-candidate/site --verification .private/browser-demo/reviewed-candidate/browser/browser-report.json
```

Selection verifies the candidate and writes the private receipt; it starts no browser, simulation or publication. A newly successful `prepare_demo.py` selects its packaged candidate after verification. Keep the current site, matching semantic case, and verification receipt under the [local retention policy](local-data-retention.md). The local retention policy retires the historical source recordings and backups. Serving the prepared case remains available; re-exporting it from the retired original pair is unavailable.

### Prepare a different matched recording

The existing accepted case needs no new simulation. When a new matched recording is intentionally required, run in **Windows PowerShell**, from `<checkout>`, after saving a clean reviewed source revision. The prepared Montréal inputs, existing Ubuntu Docker Engine, local geospatial environment, pinned browser, and dashboard dependencies must already be installed. This procedure reuses that environment.

```powershell
Set-Location '<checkout>'
python scripts/demo/prepare_demo.py
```

Allow approximately **35–45 real-life minutes** for two sequential one-bus runs at 1×, their five-minute history windows, setup, and offline checks. Both runs use the same 11.6% starting charge and reviewed configuration. The actor sends no approval in the continuing run. In the charging run it waits until at least working second 30, then uses the ordinary scoped approval endpoint when the reviewed bay is actionable. No passwords or browser charging clicks are needed during preparation.

Keep power connected and the terminal open. Existing memory, storage, source, lease, backup, and cleanup safeguards remain active. Preparation stops after a failed or blocked stage; it does not automatically retry. If the host reports insufficient available RAM, close memory-heavy applications and review the retained result before another attempt. Do not lower the host reserve to make this demonstration pass. Ctrl+C once requests interruption; allow the owned runner to finish cleanup before closing the terminal.

The command then verifies exact physical history through second 30, checkpoint route and stop state, preserved passenger stops, one approval after the checkpoint, measured charging and energy balance, reserve contrast, both complete trips, backups, cleanup, and source compatibility. It exports the semantic case, builds the isolated viewer, tests the actual case in Chromium, and assembles a private static candidate. Failed and incomplete evidence is retained.

Success prints **`DEMO READY: both recorded decisions verified; static site prepared.`** and writes `.private/browser-demo/preparation-…/READY.json`. The preparation directory contains the semantic `case`, browser report/screenshots, `site`, and a publication inventory. Original recordings, source-to-export audit, and credentials remain outside the site in ignored storage.

For a future newly authorized pair, if both recordings succeeded but a later offline stage failed, continue from its still-retained protocol and source recordings without starting another bus. This does not apply to the original pair whose raw recordings were deliberately retired:

```powershell
python scripts/demo/prepare_demo.py --protocol 'evidence/energy-v2/browser-demo-pair-REPLACE_WITH_RECORDED_ID/protocol.json'
```

Use the actual protocol path from that attempt; the exporter checks compatibility and will reject an unsuitable pair. A failure requires diagnosis rather than loosening its acceptance rules.

## Open the finished site

From the same project directory:

```powershell
python scripts/demo/serve.py
```

This verifies and serves the selected current receipt, binds only `127.0.0.1:18770`, and opens the browser. Workspaces without a current receipt use the historical latest-READY fallback. It starts no simulator or operational services. Stop the static viewer with Ctrl+C. To serve a particular accepted site for this invocation, pass its private `--site` directory. Use the selection command above to change the default. Do not serve the repository, evidence root, or a directory containing credentials.

The site must be served over HTTP locally or HTTPS when hosted; opening `demo.html` with `file://` is not supported. It loads and verifies both outcomes before Start. The demo shares the live dashboard shell, ribbon, measurement tables, summary cards, forecast details, fleet vehicle panel, map switches, and legend. The sidebar identifies it as **Recorded demo** and opens a working About dialog. The first 30 active playback seconds run at 1×. Hidden tabs, loading, user pause, and long scheduling interruptions do not consume that introduction. At working second 30 it pauses and opens the decision. **Approve charging stop** and **Continue without charging** remain available without a deadline. Hide the window to inspect other tabs and reopen it from the sticky top bar.

After the choice, playback rates are 0.5×, 1×, 2×, 5×, 10×, and 20×. The slider seeks within that selected recording. A backward seek keeps the chosen outcome; Review alternative decision clears it and returns to the checkpoint. Compare both outcomes becomes available after the selected trip ends. Restart recording restores the full introduction.

The Overview map starts close to departure and follows the recorded bus after readings arrive. **Fit map** shows the full route and stops following; dragging also stops following. **Follow vehicle** restores the close view. Street labels adapt to the zoom level. These controls change the map view only.

Overview keeps four compact readings above the map and forecast, with Signal and Window controls on the trend below. During charging, measured transfer replaces the paused forecast summary. Quality and age warnings remain visible; original precision and units remain in measurement metadata, chart inspection, and exports. The fixed playback controls show the introduction countdown or selected outcome without resizing the slider as the timer changes.

Energy Forecast presents charge, remaining-route energy, and the battery balance first; forecast provenance and input assumptions expand on demand. Its update chart appears once more than one available forecast has been reached. Measurements keeps search and grouping visible, with **More filters** for freshness, component, and quality. History retains the searchable grouped signal picker and all-signal selection; **Export values** is beside the query tools. Fleet shows the recorded vehicle once, Activity has one filterable timeline with event inspection, and System summarizes recording coverage with an expandable architecture reference. These presentation choices do not change the recorded outcomes or live dashboard permissions.

## Offline development checks

These checks start no bus:

```powershell
npm.cmd --prefix apps/dashboard run demo:build
npm.cmd --prefix apps/dashboard test
python -m unittest discover -s tests/unit -p 'test_demo_*.py'
node --experimental-strip-types qualification/browser/demo-check.mjs --fixture --output .private/browser-demo/interface-review
```

The browser checker uses the existing `.runtime/playwright-browsers` installation. Fixture reports and screenshots qualify interface behavior only. Actual-case checks use `--case DIRECTORY` and bind their result to the case manifest and built application. Packaging refuses a fixture report or changed files.

## Hosting handoff

The `site` directory is the upload candidate; it includes a root `index.html`, `demo.html`, relative assets, the two-outcome case, and notices. Both root hosting and a project subpath are checked. There is no backend configuration or secret to provision. No external tile service is needed.

Review that exact directory and its adjacent `site-publication.json` inventory before selecting a public destination. Cloudflare Pages Direct Upload is the selected hosting path. The [hosting procedure](browser-demo-hosting.md) prepares a separate fingerprint-checked ZIP, adds static security and cache headers, and checks both decisions without using a visible browser. A website subpath or static GitHub Pages deployment can also serve the original static candidate. Public upload, repository/remote changes, and domain setup require explicit authorization for the reviewed candidate and destination. A public URL is added to the README after anonymous hosted verification. [Publication rules](../release/publication.md)
