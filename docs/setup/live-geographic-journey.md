# Live geographic journey

The geographic viewer runs one SUMO bus at normal speed through its prepared OpenStreetMap route. The Vehicle Adapter sends twenty readings each working second through the existing queue, broker, database, and Monitoring API. The Forecast Controller refreshes the route-aware estimate using the current readings and remaining stops.

## Start and view

The [launch workspace](launch-app.md) provides trip selection and Start/Stop controls in the browser. The command below remains available for direct operation of the longest prepared trip.

Run from Windows PowerShell in the repository root:

```powershell
python scripts/geographic/live.py --profile .private/operator/longest-live.json
```

The local profile selects the retained Richmond–Steveston–UBC–Burnaby Mountain journey: 58.98 km, 24 mapped stops, and approximately 112 working minutes. At normal speed, one working minute takes one real-life minute. Allow additional time for startup, five minutes to view history after arrival, and recording backup.

Keep the laptop plugged in with its lid open. The launcher checks memory and storage, builds the dashboard, starts one isolated service session, and opens the browser. It prints both reader credentials in the terminal and copies the monitoring credential to the clipboard. Paste that credential into **Reader credential**, then select **Connect to monitoring**. Replay uses its separate printed credential at the separate address.

The Map, Measurements, History, and Energy Forecast tabs use the live recording. The first forecast needs a few seconds after accepted readings arrive. Startup stops with a diagnostic report if twenty readings and a current forecast are unavailable after three minutes. The geographic profile supplies real road geometry and attributed elevation; vehicle and equipment settings retain the trip's declared operating assumptions.

## Stop and retained results

After arrival, the dashboard remains available for five minutes. The launcher then saves the recording and removes its own containers, network, and volumes. Press **Ctrl+C once** for an earlier stop and leave PowerShell open for cleanup. An early stop is recorded as incomplete. The Windows memory reserve remains 2 GiB; a guard failure stops the session without an automatic retry.

Each session creates a new ignored `evidence/energy-v2/live-journey-*` folder. It contains the source fingerprint, input profile, startup result, resource samples, forecast jobs, original source recording, and private database backup. `operator-result.json` records the outcome, and `session-result.json` records cleanup. Credentials in `ready.json` apply only to that local session.

## Input and model boundaries

The private profile fingerprints the original geographic preparation and reference collector inputs. The live source reuses the elevated legal SUMO network and stop assignment. It performs a fresh drive; it does not play back the earlier measured trajectory. Repeated-road stops carry their assigned route occurrence. The endpoint hold is excluded from forecast dwell and ends the source when arrival is observed.

The source manifest binds the actual SUMO network and prepared route files. Route context identifies the separately prepared forecast geometry, whose fingerprint is also bound into the source inputs and approved forecast assignment. The forecast worker has the same vehicle profile and numerical policy used for geographic evaluation. This viewing session neither retrains the model nor changes the retained evaluation results.

`--check` verifies input fingerprints without starting services. `--preview-seconds 60 --no-browser` runs a bounded startup check and records its result as `preview_complete`; it does not qualify the complete journey. Only one session can hold the project's live-workload lock.

## Verification

The shared engineering checks passed with 1,096 Python tests, 201 subtests, 44 browser-component tests, and unchanged contract parity. Focused source checks cover input tampering, repeated-road stop assignment, terminal-hold exclusion, and observed arrival. Static preparation validated the long trip's network, elevation receipt, and map context.

The connected startup attempt produced twenty observation packets with all twenty signals and one available route-aware forecast. Windows available memory then remained below the unchanged 2 GiB reserve for more than thirty seconds, stopping the attempt before the full startup check completed. The private recording backup, owned-resource cleanup, and sleep-request release were verified. The complete live journey remains an external operator run; the earlier completed geographic evaluation retains its separate scope.

Subsequent bounded checks of the browser launch workspace passed with Energy Forecast and replay enabled, and with monitoring alone. Both saved their partial recordings and cleaned up without a host-guard failure. Their scope and results are recorded in the [launch workspace verification](../verification/launch-app.md).
