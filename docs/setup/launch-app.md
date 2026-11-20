# DriveTwin launch workspace

Double-click **DriveTwin.cmd** in the project root to open the local launch workspace. Select a prepared trip and its services, then start the bus from the page. PowerShell is not needed for normal operation after project setup.

## Start, view, and stop

1. Plug in the laptop and keep its lid open. Double-click **DriveTwin.cmd**. The browser opens the launch page; no vehicle or container starts yet.
2. Select a trip and **Trip speed**: 0.5×, 1×, 2×, 5×, 10×, or 20×. New page visits default to 1×; reopening an active session restores its selected speed. Every trip shows **Working time** and **Real-life driving**. Changing speed immediately updates the estimated real-life driving duration, including the small-screen selector. The selected trip also shows its OpenStreetMap road alignment, distance, stops, and declared vehicle settings. Startup, five minutes of history viewing after arrival, and up to three minutes of saving/cleanup are additional real-life time.
3. Select the services and check the trip name, starting charge, working duration, and real-life driving estimate beside **Start trip**. The Charging Advisor setting shows whether the selected trip and pace support advice or approval. Monitoring always includes twenty readings, the map, history, and downloads. **Current session** shows the host check, dashboard build, and service startup. Allow about 45 seconds for the host check and a few minutes for startup. If Start is rejected, the page displays the reason.
4. The launcher opens the dashboard when its key is ready and copies it to the Windows clipboard. Paste it into **Dashboard key** and select **Connect to monitoring**. With charging approval enabled, that single sign-in gives you live readings and charging decisions. **Copy key** in **Current session** retrieves the same key during the drive. Keep the dashboard tab open to review the trip after arrival. Use **Open replay** in the signed-in dashboard while the launcher session is open. The dashboard requests a scoped replay handoff for that run.
5. Click **Stop and save recording**, then confirm **Stop and save**, to finish early. **Keep running** cancels the request. Wait for **Cleanup verified** before starting another trip. Cleanup can take up to three minutes. After normal arrival, five minutes remain for browsing history before automatic backup and cleanup. Select **Close DriveTwin** when no trip is active to stop the launch application.

Closing a browser tab does not stop the trip. Double-click the launcher again to reopen the existing workspace. The application recovers its saved run reference. If an interrupted trip shows **Cleanup needs review**, select **Recover previous trip**. Once cleanup is verified, **Start trip** is available again.

Reopening the launcher uses a verified completed result to restore a finished session. Damaged session metadata displays **Cleanup needs review** and blocks another trip until recovery verifies the retained resources.

### Saved-session metadata repair

The launcher's `current.json` file identifies its previous owned trip. A missing
file is a normal idle state. An existing unreadable, oversized, malformed, or
inconsistent file displays **Cleanup needs review** and blocks another start.
DriveTwin preserves the file and does not guess a process ID or recovery target.

Close DriveTwin, preserve the damaged file, and inspect the matching run's saved
profile, result/recovery receipt, and actual process/Docker ownership locally.
Restore a known valid reference for that same run only after verifying its
identity; do not delete the marker to bypass unresolved cleanup. Reopen the
launcher after repair. A valid interrupted reference exposes **Recover previous
trip** through the normal guarded recovery procedure. If the matching identity
cannot be established, keep its resources for operator review. Do not share the
file's private trip-control key.

### Recording finalization

Recording finalization checks the saved PostgreSQL archive before removing owned storage. If writing or validating the backup fails, the runner preserves the attempted file, records recovery ownership, and stops its services while retaining their containers and volumes. The session stays failed with **Cleanup needs review**. **Recover previous trip** can retry the archive from the retained database before releasing services. A failed or incomplete archive is not reported as a saved recording.

An earlier source/controller shutdown or recording-copy failure also retains those resources, even when the PostgreSQL backup is valid. The saved database snapshot and incomplete source capture are separate outcomes; the queue and original source files remain available for recovery.

Recovery also validates a cached archive instead of trusting its filename, header, or earlier saved flag. A failed recovery attempt retains the stopped database owner for another retry and preserves partial files. An invalid existing recovered archive requires inspection before replacement; it is not overwritten automatically.

After a launcher update, double-click **DriveTwin.cmd** to reopen it. An outdated idle launch service closes gracefully and is replaced with the current version. An active trip is preserved; the page asks for stop and cleanup before updating. Refreshing the page during startup retains the pending dashboard-open request. Once opened, periodic status updates and page refreshes do not open duplicate dashboard tabs or repeatedly replace the clipboard.

During a drive, select **Pause trip** in the pinned monitoring bar or under **Current session** in the launcher. Open monitoring with the launcher's dashboard link to enable its controls. The button appears once live startup checks pass and changes to **Resume trip** when the simulator confirms the pause. Monitoring stays connected and shows **Trip paused**. Resume continues at the selected speed. The session time limit includes time spent paused. **Stop and save recording** remains in the launcher.

To verify the live controls, start Gare Centrale to Parc La Fontaine at 1× with Energy Forecast enabled. Open the monitoring dashboard from the launcher and wait for **Pause trip** in the pinned bar. Pause for at least eight seconds. Confirm the working time holds, readings show **Paused**, and the current forecast is withheld. Resume and confirm working time advances, readings become fresh, and a current forecast returns. Stop the trip from the launcher and wait for **Cleanup verified**.

## Trip and service selection

A fresh source checkout needs the separately supplied [portable geographic scenario bundle](../../simulation/geographic/README.md) before these ten presets are available. Verify and install it with `scripts/launcher/scenarios.py`; its source manifest binds each runtime input by size and hash. Installation starts no simulation. The source-prepared Montréal release demo has its own [preparation procedure](../release/source-package.md). Installed presets use immutable canonical inputs rather than old evaluation-run folders.

The installed local catalog contains ten prepared journeys across Montréal, Ottawa, and Metro Vancouver. Routes and elevation come from the retained geographic preparation. Working time is time inside SUMO; it advances during a traffic stop and holds while the trip is paused. Working duration uses the earlier observed trip duration. Estimated real-life driving duration is working duration divided by the selected speed, rounded to one decimal minute. Host slowdowns can make it longer. Each selection creates a fresh recording and run identity.

The launcher displays local streets from the prepared OpenStreetMap network. Drag or zoom to inspect the route. Small yellow dots mark planned passenger stops, teal marks departure, and pink marks destination. Select a stop marker to inspect its name and scheduled dwell. The live Map tab uses the same trip geometry and stop plan. Initial map preparation uses the installed Ubuntu geospatial environment and normally takes a few seconds. Subsequent views reuse the cached map.

Speed is fixed when a trip starts and is retained with its saved settings. It changes the delay between SUMO steps. Physical step size, vehicle speeds, stops, energy calculation, and one-working-second recording intervals remain unchanged. At 0.5×, readings arrive approximately every two real-life seconds; at 20×, the target is twenty observation packets per real-life second. The browser can show fewer intermediate updates while the database retains every reading.

Set **Starting charge (%)** before launching a trip. Use the field for an exact value from 0.1% to 100% or the slider for a quick adjustment. **Reset to preset** restores the reviewed default. A value below 10% starts the simulated bus below reserve. DriveTwin records the configured value and checks it against SUMO's measured insertion energy. **Use these settings** restores the saved starting-charge override along with speed and services.

The live Energy Forecast requests its first calculation as soon as the required readings and route inputs are valid. A periodic update is due 120 working seconds after the preceding forecast's input cutoff. A confirmed plan change, material change in forecast inputs, or recovery after an interruption can trigger an earlier update. Reserve crossings and changes to the supported input domain can request immediate recalculation; other material changes use persistence, cooldown, and reset thresholds. One worker calculates at a time, and the controller keeps the newest pending input. These are scheduling rules; calculation completion times need not be exactly 120 working seconds apart. The forecast age limits cover the regular interval and the worker deadline. The real-life limit allows for the longer interval at 0.5×. Faster playback can still advance working time past a forecast’s validity while a worker runs. Earlier qualification profiles retain their recorded intervals and results. [Lifecycle rules](../design/energy-forecast-lifecycle.md) · [Pacing verification](../verification/launch-pacing.md)

All six rates passed the bounded connected recording and forecast checks. The 20× selection achieved about 15.3× on the qualification laptop: thirty working minutes took about 118 real-life seconds. Duration labels show the requested pace, so actual driving can take longer. Use 1×–5× when following forecast updates closely.

| Service | Launch workspace behavior |
|---|---|
| Live monitoring | Always enabled; twenty readings, current state, map, historical queries, and downloads |
| Energy Forecast | Optional on all ten presets; starts the existing continuous controller and isolated workers |
| Recording replay | Optional; starts the separate read-only replay service, opened from the signed-in dashboard |
| Charging Advisor | Advisory estimates at 1× with Energy Forecast and a reviewed station assignment |
| Speed commands and charging control | Separate control profiles with their own authority and test evidence |
| Thermal studies | Retain their separate laboratory and fault-study workflows; bus temperature readings remain in monitoring |

The ByWard Market–Place d'Orléans preset includes a localized speed restriction. The live source applies it on the assigned road occurrences. A trusted assignment updater supplies matching forecast conditions and appends a new assignment when the remaining stop plan changes. Forecasting waits for a matching assignment during each transition. The source, controller, and worker permissions remain separate; the predictor's numerical parameters are unchanged.

Turning off Energy Forecast prevents the controller and calculation workers from starting. Turning off replay prevents its API container from starting. Changing services applies to the next run; active run settings stay fixed.

The browser remembers the selected trip and optional services. Reopening an active session shows its fixed choices. Vehicle mass and equipment demand come from the selected prepared profile. The starting-charge control changes only the new run's configuration.

## Charging Advisor

For trips with a reviewed station assignment, select **Charging Advisor** alongside **Energy Forecast** at 1×. Open **Operator** in the monitoring dashboard to see the provisional stop, departure target, projected wait and charging time, and any available fallback. The result expires when its inputs age or the route moves past the stop. This mode uses simulated station status and leaves the bus on its assigned route.

**Advisor startup delay** defaults to **Immediate**. Choose **30 seconds**, **1 minute**, or **2 minutes** to hold charging recommendations, reserve pop-ups, and their sounds until that much working time has elapsed. A paused trip pauses the delay. Energy Forecast and live readings continue on their usual schedules. At release, the Advisor uses the current route and reachable bays. A longer delay can let the vehicle pass an early charging turn. The saved session retains the selected delay for **Use these settings**.

The reviewed Mount Royal, Gare Centrale–Parc La Fontaine, Parliament Hill–Carleton, Stanley Park, and Burnaby Mountain profiles also offer **Allow me to approve a charging stop**. The selected base profile or charging variant must match the reviewed road, stop, elevation, and station input inventory. Energy Forecast and Charging Advisor must both be enabled at **1×**, with a compatible station assignment and unchanged input files. A new trip or changed inventory requires its own approval review. The dashboard key includes approval access for that run and vehicle when this option is selected. [Charging operation](moving-bus-charging.md#approve-a-stop)

Ordinary launcher approval permits one eligible stop at a currently available bay. Automatic dispatch, scheduled queuing, and charger-outage replacement retain their dedicated qualification profiles and evidence. [Verified operating scope](../verification/moving-bus-charging.md#operating-scope)

## Saved sessions

**Saved sessions** lists earlier geographic live runs, newest first. Search by trip name, filter for saved recordings or runs needing review, and use the page controls to browse older entries. **Refresh** also discovers recordings made with the command-line live runner. The list keeps run outcomes separate from backup and cleanup status.

- **Open results folder** opens that session's directory in Windows File Explorer. **Recording location** shows its full path and a copy button.
- **Database recording saved** means the runner reported a successful backup and a nonempty `database.dump` is present. That backup contains accepted observations and saved forecast records. The folder also retains the run reports and source records.
- **Stopped early** preserves the partial-trip outcome. A partial recording can still have a verified backup and clean shutdown.
- **Use these settings** selects the same trip, pace, services, and starting-charge override for a new run. Review the selection and click **Start trip** when ready.

After normal completion, **Find saved recording** leads to **Saved sessions**. Live monitoring and replay URLs close when cleanup finishes. To reopen readings from a saved database backup, use the [backup and restore procedure](backup-and-restore.md).

## Host checks and outcomes

The launch workspace uses the existing guarded Windows-to-Ubuntu runner. Its memory, storage, clock, source-compatibility, and one-live-workload checks remain in force. A host-preflight failure starts no bus. A runtime guard failure stops the active run and preserves its partial data. No automatic retry follows a failure.

Local trips check for abrupt changes in the Windows clock between resource samples. Memory and sample coverage use an elapsed-time clock. Benchmark qualification retains its fixed clock-reference check. After a normal arrival and backup, the Monitoring API sends a final snapshot before closing. Open dashboard tabs stop reconnecting and keep the final readings available. Unexpected connection losses still show a warning and retry.

| Page state | Meaning |
|---|---|
| Checking host / Building dashboard / Starting services | Startup is in progress; Stop remains available |
| Running | The selected services have started and reader access is available |
| Browsing history | The bus arrived; the five-minute viewing window is open |
| Saving and cleaning up | The runner is stopping its own services and saving the recording |
| Complete | Arrival, recording, and cleanup completed |
| Stopped early | The operator stopped the trip; its partial recording is retained |
| Host check blocked / Needs review | Read the visible reason and the saved run report |
| Cleanup needs review | Select **Recover previous trip** to release the interrupted trip's services |
| Recovering interrupted trip | The launcher is saving the database when available, retaining the raw volumes, and verifying that the trip's containers and networks are gone |

Every run has a new ignored `evidence/energy-v2/live-journey-*` folder. The launch page displays its location. Recording, forecasts, resource samples, and the database backup stay there. Launch application state, the credential-free session index, and runner logs stay in ignored `.runtime/launch-app/`. The progress strip follows the runner's actual stage: host checks, service preparation, driving and history viewing, then saving and cleanup.

Recovery writes a separate `cleanup-recovery.json` record and leaves the original run result in place. When PostgreSQL can be opened, recovery saves `recovered-database.dump` and verifies that PostgreSQL can read its archive table of contents. Retained volumes stay local even if the database export fails.

## Local authority and implementation

The small Windows Python process serves the workspace at `http://127.0.0.1:18760`. It starts the existing live runner with an allowlisted preset and optional services. The Monitoring API stays at port 18765; replay uses 18766 when enabled. The launcher needs no Docker socket in the browser or Monitoring API.

Launch access uses a random temporary capability—a key that authorizes this page to start and stop local runs. It is separate from monitoring and replay reader credentials. The browser receives it through a URL fragment, removes the fragment from the address bar, and sends it in request headers. State responses are not cached. Host, origin, request size, and credential checks protect the mutation endpoints. Other websites and reader credentials cannot start a trip.

The interface submits a trip identity and a service list. It cannot submit a shell command, executable, arbitrary file path, PID, or Docker resource name. Stop writes the current owned run's stop marker. The existing runner performs bounded cleanup and releases its temporary sleep-prevention request.

Automatic dashboard opening uses the launch credential and the current run identity. The server verifies that the run is active and its dashboard access is ready, then opens the fixed loopback monitoring address. The request cannot supply a URL or a credential. Dashboard keys are passed to the clipboard utility through standard input and remain absent from process arguments. Browser opening does not log the dashboard in; sign in once with the dashboard key.

Opening results accepts an indexed session identity only. The server resolves its fixed results directory under the project and rejects redirected or missing folders. History requests use the same launch credential and return no monitoring or replay keys. Only trip and service preferences are stored in browser local storage.

## Preset preparation and verification

The private active catalog is `.private/operator/live-trips.json`. Install the ten baseline presets from the [portable scenario bundle](../../simulation/geographic/README.md). For a separately approved new journey, the importer can add a complete compatible geographic qualification:

```powershell
python scripts/launcher/catalog.py evidence/energy-v2/<completed-geographic-evaluation> --append
```

The importer requires a completed, passing standard `map-journey` qualification from the [geographic energy workflow](geographic-energy.md#verify-a-new-journey). It verifies the retained preparation fingerprints and binds the reference network, route, and recipe files into each live profile. It rejects duplicate trip IDs. Use `--replace` for an intentional catalog rebuild. When the launcher is idle, select **Close DriveTwin** and then open **DriveTwin.cmd** to load the new list. Catalog preparation starts no bus and uses the existing predictor.

Focused Python tests cover launch authority, service allowlisting, process ownership, early stop, restart recovery, missing cleanup, saved-session import, filtering, pagination, and folder-opening boundaries. The browser-only check covers all ten presets, region filtering, service choices, visible keys, Start/Stop requests, stop confirmation, saved-session controls, preference recovery, desktop rendering, and narrow-screen overflow. It intercepts mutation requests and cannot start a vehicle. [Verification record](../verification/launch-app.md)
