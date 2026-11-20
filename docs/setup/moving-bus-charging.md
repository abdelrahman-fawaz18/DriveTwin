# Moving-bus Charging Advisor

DriveTwin can evaluate charging stops while a SUMO bus drives. It compares staying on the route with using the assigned charging bays. Each bay is a specific place where the bus can stop. The advisor calculates the charge it needs through the approach, wait, transfer, and rest of the trip. The 10% reserve applies to the lowest point across those stages.

## Run with charging advice

1. From the project folder in PowerShell, run `python scripts/launch_drivetwin.py` and open the launcher. All ten installed geographic presets have a simulated station assignment.
2. Set **Starting charge (%)**, or keep the preset default. Leave the pace at **1×** and enable **Energy Forecast** and **Charging Advisor**. Enable **Replay** to revisit the recorded observations.
3. Start the trip and sign in with the launcher’s **Dashboard key**. The dashboard opens an alert on any tab when the current forecast first predicts a drop below reserve or the advisor recommends a stop. Open the forecast or review the stop directly from the alert. The Montréal route from Gare Centrale to Parc La Fontaine at **11.6%** is a low-charge example for the reviewed two-bay scenario.
4. Use **Map** to locate the recommended and fallback chargers and see where the current forecast predicts the bus would pass below reserve if it continued without charging. Open **Operator** to review the target charge, wait, charging duration, and predicted arrival. The map clears plan markers when the corresponding estimate expires.
5. Let the run finish, or select **Stop and save recording** in the launcher. Check the launcher for the result and recording status. **Saved sessions** preserves the chosen trip settings.

## Approve a stop

The launcher offers approval for the reviewed Mount Royal, Gare Centrale–Parc La Fontaine, Parliament Hill–Carleton, Stanley Park, and Burnaby Mountain profiles. The selected profile or charging variant must match its reviewed input inventory and pass input-file validation. Energy Forecast and Charging Advisor must both be enabled at 1×. Advice on a newly prepared trip does not itself establish approval eligibility. [Launcher selection rules](launch-app.md#charging-advisor) · [Verified operating scope](../verification/moving-bus-charging.md#operating-scope)

1. Select a preset that offers operator confirmation. For example, select **Gare Centrale to Parc La Fontaine** at **11.6%** starting charge or **Mount Royal** at **12.7%**. Set **1×** pace and enable **Energy Forecast** and **Charging Advisor**.
2. Select **Allow me to approve a charging stop** and start the trip. The launcher waits until the dashboard key with approval is ready, then copies it and opens the dashboard. **Copy key** in **Current session** retrieves the same key during the drive.
3. Paste that key at dashboard sign-in. Review and respond from the recommendation window. If you dismiss it, use **Review charging stop** in the top bar. **Show on map** opens the mapped bay and bus position. **Return to charging decision** reopens the review.
4. Review the bay, target charge, charging duration, and lowest predicted charge with the stop. Select **Approve charging stop** to request it or **Keep driving** to record that decision in the current dashboard tab. **Review stop again** returns to the offer if it is still actionable. Select an eligible alternate from **Charging stop** if needed. **Withdraw approval** is available before execution takes authority.
5. Follow **Charging decision timeline** and **Observed charging** through approach, transfer, and resumed driving. In **Activity**, open the relevant readings around an event or locate its mapped bay.

This opt-in applies to the fingerprinted station and trip inputs reviewed for the preset and permits one eligible stop. The launcher states whether the assignment includes on-route bays, off-route bays, or both. **Stop insertion acknowledged** means SUMO accepted the scheduled stop. **Approaching approved charger** means the adapter found the approved stop on the simulator’s schedule after a completed step. **Charging observed** means the battery device independently reported transfer at the intended bay. **Driving resumed** means the bus reached the target and subsequently moved.

An occupied bay can appear in the advice if its opening is known. The wait is included in the predicted charge and trip duration. Standard operator approval requires a currently available bay. If the advisor presents an available fallback, select it explicitly to review and approve that offer.

The last displayed recommendation and the disabled **Approve charging stop** button stay visible during an offer refresh. At a red light, at a junction, or while the dashboard rechecks a previously available recommendation, select **Approve when available** to authorize one request on the displayed terms. Keep the review open. The dashboard waits up to 60 real-life seconds for a current offer. It checks the selected bay, target charge, station state, minimum predicted charge, and maximum wait and charging time. An off-route offer must also retain its exact candidate identity. Closing the review or selecting **Cancel pending approval** cancels the waiting request. The Command API still checks live road progress, station status, battery charge, and the current offer before handoff. **Limits found for other bays** lists constraints such as a wait that would take charge below the planning floor.

The review timeline keeps the events seen in the current dashboard tab for the current vehicle and run. It survives a reload of that tab after signing in again. The browser records **Keep driving** locally. Approvals and execution outcomes retain their command-service records, and the observed transfer is recorded by the source.

An alert opens once for the reserve warning and once for each newly recommended bay while that warning is active. **Hide alert** closes the window, and the sticky toolbar keeps a button to reopen it. Hiding also cancels a pending approval that has yet to be submitted. The decision area shows the current offer's real-life countdown and the next working-time checkpoint for the selected bay. Live station checks may renew the offer, and passing the turn can close approval sooner. A queued decision has its own real-life countdown. During refresh, reconnect, or shutdown, the window retains the last recommendation and labels earlier values. **Pause trip** and **Resume trip** are available in launcher sessions with trip controls. Resume to refresh the offer before approval.

Charge decisions and timestamps in the advisor use working time. At 1×, a minute of working time takes approximately a real-life minute while the bus is running. Credential, input freshness and processing deadlines use real-life time.

The sticky toolbar and the Overview, Map, and Energy Forecast tabs show estimated working time to the destination. The advisor separately shows an estimate with the recommended stop. After this browser approves a stop, the dashboard can show the estimate recorded at approval during the stop while the through-trip forecast is paused. An observed energy transfer changes the toolbar and the live map marker to **Vehicle charging**. The toolbar's speaker button controls chimes for reserve, charging, and trip events.

If the committed charger loses power, the standard launcher run stops and saves its partial recording. Its local results include the last measured battery energy, the bay and lane position, and the remaining passenger stops when those checks agree with SUMO.

The separate one-bus recovery profile exposes **Charger outage recovery** in the **Operator** tab after signing in with the dashboard key for that profile. It keeps the bus stopped during the review and looks for an eligible downstream charger. Select an available option and choose **Approve replacement charger**. The bus keeps its passenger stops. The run ends if the 75-real-life-second hold expires or the replacement cannot be verified. Recovery uses its dedicated profile.

## Review and replay a completed run

1. Confirm that the launcher shows **Trip complete** and **Recording saved**. **Needs review** indicates an incomplete result.
2. While history remains open, use **History** in monitoring to select **Battery · Pack · Soc** and **Motion · Speed** for the same working-time interval. Use the charging event times in **Operator** to find the approach, stopped interval, and return to driving.
3. If Replay was selected at launch, use the launcher's **Copy key & open replay** control. The replay reader key is separate from the monitoring and operator keys. Select the recording, choose a working-time interval, and seek to the charging stop.
4. The services shut down at the end of the review window. **Saved sessions** keeps the run details and opens the folder containing its recording and backup. Follow the [launcher guide](launch-app.md#saved-sessions) to find that folder.

## Prepare another map or trip

| Advisor mode | Available local operation |
|---|---|
| Advice | The ten installed trips and new prepared trips with a reviewed station assignment, at 1×. |
| Operator confirmation | One eligible stop on a reviewed, fingerprinted launcher profile at 1×. The launcher exposes the control for supported presets. |
| Automatic moving-bus dispatch | Dedicated Ottawa qualification profile. |
| Scheduled queue and outage replacement | Dedicated Burnaby and Montréal qualification profiles. The ordinary launcher requires an available bay for approval. |

Use the [geographic energy workflow](geographic-energy.md) to prepare connected bus lanes, mapped passenger stops, and attributed elevations. After the new journey passes the standard `map-journey` qualification, import its result following the [launcher guide](launch-app.md#preset-preparation-and-verification). Use an assignment specific to the new road sequence and network fingerprint. Define each bay’s lane and offsets, connector, clearance, power, efficiency, setup time, and source. Define the simulated availability and power schedule for each bay. Validate the assignment against the prepared route before making the advisor available in the launcher.

```powershell
python scripts/geographic/charging_template.py --trip montreal-central-lafontaine --request evidence/energy-v2/geographic-evaluation-20261113T024443Z-194b29/journey-01/input-0.json --targets-percent 12 13 15 --output .private/reviews/station-assignment.json
python scripts/launcher/catalog.py --attach-charging montreal-central-lafontaine .private/reviews/station-assignment.json
```

The first command prepares two simulated options where suitable lanes exist. The three percentages are candidate charges to leave with, expressed as a percentage of full capacity. Inspect the lane references and scenario settings before attaching the assignment. The second command verifies bus access, connector and bay length, bay position in both the forecast and SUMO networks, and conflicts with the scheduled passenger stops before saving the preset. Both commands operate on files and start no bus. The same Energy Forecast model runs on the new trip. The local options represent simulated equipment. Public charging locations need separate bus-access and equipment review.

`--request` explicitly selects the matching departure request from that journey's retained qualification. The example needs its original private reference, which is separate from the portable runtime bundle. For a new journey, use its own qualified departure request; mismatched network, trip, progress, or ordered roads are refused. Creating or repeating a reference drive is a separate authorized workload. The detour-preparation tool also requires original full import and raster artifacts; it is an engineering workflow, separate from ordinary portable trip playback and launch.

[Overview and data flow](../engineering/moving-bus-charging.md) · [Design and verification gates](../design/moving-bus-charging.md) · [Recorded cases and results](../verification/moving-bus-charging.md)
