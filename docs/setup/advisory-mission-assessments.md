# Energy forecast and station assessment guide

Historical known-route energy procedure. Its original model, workload and evaluation identities remain scoped to the frozen study; pending statements below describe that checkpoint. Use the [route-aware operating guide](route-aware-energy.md) for current geographic forecasting and the [final advisory closeout](../verification/energy-advisory-acceptance.md) for the completed legacy product scope. Inspection of retained results does not authorize rerunning a consumed evaluation.

Open **Energy** to see what the twin expects under a stated driving plan. For example, it can estimate how much charge a simulated bus would have when it reaches a particular road near a hypothetical charger. A charge reserve is the amount you want left in the battery as a buffer. This profile uses 10%; reaching the road with less than that counts as a reserve shortfall even if the bus could still drive there.

| What you see | How to read it |
|---|---|
| Latest reading age | Real seconds elapsed since the most recent admitted observation. The server hides current results when the latest observation exceeds five seconds or is otherwise inadmissible. |
| Assumed route forecast horizon | Simulated minutes and kilometers covered by the forward plan from the 120-second cutoff. The cutoff is where the forecast starts, and the horizon is how far forward it looks. |
| Estimated route energy and final charge | The frozen model applied to the assumed remaining route. A kilowatt-hour (kWh) measures energy; charge is shown as a percentage of declared capacity. |
| Conditional time and distance to reserve | A separate reference: if recent net consumption and speed continued, how much time/distance would remain before the 10% reserve? This can be unavailable and is not the time until the battery is empty. |
| Station assessment | Modeled road approach, trip duration, energy and final charge for each hypothetical site. Reachability requires modeled energy and reserve to hold along the whole approach plan. |
| Saved forecast history | Immutable earlier results for this run and vehicle. A saved value can still be read when current values are hidden. |

The route assumptions include a 350-kWh declared battery, 18,000-kg loaded mass, flat grade, a frozen training motion template and constant measured auxiliary equipment demand. Charging exclusion is a declared source assumption. Scenario ranges reflect changes in selected model parameters. The independent study measured coverage for six known-route end-of-trip cases; the station scenarios have no independently measured arrival coverage. No vehicle commands, charger selection or charger availability checks are performed.

```mermaid
flowchart LR
  O[Registered readings] --> A[Check source, age, route and quality]
  A --> C[Trusted controller]
  N[Reviewed bus-road paths] --> C
  C --> W[Isolated calculation worker]
  W --> S[Append-only advisory record]
  A --> R[Read view checks current availability]
  S --> R
  R --> D[Energy page: current details and saved history]
```

## How a station is evaluated

1. The reviewed road map is a directed network: each road segment has a permitted travel direction and vehicle classes. The static profile uses SUMO (Simulation of Urban MObility) to link bus-permitted roads from the known route to a nearby bus-permitted approach lane for each station. It includes the distance through intersections. Both locations are hypothetical; the approach ends at the public road, with physical site access and charger availability unverified.
2. The controller matches the latest longitude, latitude and heading to a nearby road. A heading is the direction of travel. A point more than 15 meters away or ambiguous between similarly plausible roads is rejected. Missing location affects station rows only if the route-energy inputs are otherwise valid.
3. The controller subtracts the bus's already traveled portion of that segment from the reviewed path distance. It declares a forward motion plan that transitions from observed speed toward recent mean speed capped by the path's road limits. This is a simplified, flat-grade motion assumption. It does not predict traffic, turns, queues or charger conditions.
4. The calculation worker receives the same four observed quantities as before (battery energy, charge fraction, distance and speed), plus the declared plan and parameters. It has no map, raw sensor store, future traffic feed or credentials. It checks usable energy and reserve across the whole plan; a later recovery cannot erase an earlier shortfall.

| Station result | Meaning |
|---|---|
| Reachable under assumptions | The modeled plan reaches the road approach with energy and reserve maintained. |
| At station approach | The bus is already within the modeled approach tolerance and the zero-distance energy/reserve checks pass. |
| Reserve shortfall | The plan falls below the declared reserve at some point. |
| Energy shortfall | Available energy or discharge power cannot serve the assumed demand. |
| Unavailable | The input, road match, path or calculation was unsuitable. This is different from a predicted shortfall. |

The [navigation profile](../../qualification/energy/station-navigation.json) records the road inputs, generator fingerprint, lane approaches and paths. It derives from OpenStreetMap contributors' data under the Open Database License (ODbL); see the [source and attribution record](../../data/public/montreal-core-v1/README.md). The generator uses the locked simulator image's tools to build the map with zero vehicles and zero physical simulation steps. Keep original station coordinates and approach coordinates separate. A new map or station set needs a new, reviewed navigation profile and binding; changing it cannot expand the accuracy domain automatically.

To reconstruct the profile in a private folder using the previously built `evdt-energy-collector:p2` image, run this from the project folder in PowerShell. The image is a packaged copy of the locked simulator and its tools. The first mount lets the container read the project; the second gives it a separate output folder. `--rm` removes the temporary container when it exits.

```powershell
New-Item -ItemType Directory -Path .runtime/station-navigation-check -Force
python scripts/linux.py -- docker run --rm --network none --read-only --memory 512m --tmpfs /tmp:size=128m,mode=1777 --mount type=bind,source=<checkout>,target=/workspace,readonly --mount type=bind,source=<checkout>/.runtime/station-navigation-check,target=/output -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 /app/.venv/bin/python scripts/known_route_energy/build_station_navigation.py --scenario simulation/montreal --osm data/public/montreal-core-v1/roads.osm.xml --output /output/navigation.json
```

For the current inputs, expect 105 route road segments, two station approaches, and zero vehicles or simulation steps. Compare the generated `roads` and `stations` entries with the reviewed profile. The raw network fingerprint records a particular import; a new import can include different generation metadata even with the same geometry. Keep the reviewed profile in use unless a new version is explicitly accepted. Choose a new output filename on later runs; the generator refuses to replace existing output. Do not run a training or held-out collection just to rebuild this navigation file.

## Compatibility and reproduction

The [product profile](../../qualification/energy/product-profile-1.1.0.json) binds the exact navigation file and the previously evaluated model release. New records carry a versioned optional `extensions` field for explanations and stations. The original stored record, read view and database schema remain compatible: old records have no extension and do not get fabricated station values. The server only exposes the new fields as current values when the underlying record is admissible.

1. In Windows PowerShell, run `Set-Location <checkout>`. The qualifier starts Ubuntu tools through the existing launcher.
2. Choose an unused private evidence folder. Run this command to check the entire E1 feature set against a temporary monitoring service and actual browser:

   ```powershell
   python scripts/known_route_energy/qualify_energy_product.py --finish-e1 --evidence-dir evidence/p2/YOUR-NEW-E1-CHECK
   ```

   `--finish-e1` selects the complete E1 cases. `--evidence-dir` keeps the reports separate from previous attempts. The qualifier needs the retained training and model files, the reviewed local map context, and the previous D1 archive; it never regenerates the held-out study to replace missing files. Image building can take several minutes. Playwright, the browser test driver, opens Chromium in the background, so no visible browser window is expected.
3. Expect a fresh observation window with both station rows, then hidden current values after staleness or a disconnected forecast request. A second asset demonstrates missing charge input and a cutoff outside the evaluated domain. A new registered run supplies fresh admissible observations to prove recovery without editing prior records. The qualifier verifies scoped reads, write denial, original data accounting, and both current and legacy backup reads.
4. Wait for cleanup and read `qualification.json` in the new folder. Success requires the report and `cleanup.passed` to be true, with browser, access, accounting, backup and legacy checks present. The private `browser/` folder contains captured pages. Keep a failed or interrupted attempt as evidence; inspect its private logs before deciding whether to correct it. Do not clean unrelated Docker resources.
5. Run `python scripts/known_route_energy/review_energy_advisory.py --evidence-dir evidence/p2/YOUR-NEW-RELEASE-AUDIT` to verify the reviewed release evidence and original requirement coverage. It starts no services. Expect `Review completed: True; evidence integrity: True; Phase 2 ready: True` when the original reviewed files and approved current sources match. The audit checks the sealed acceptance session, even when you have created another local run. For a forecast in an already prepared local session, see the [controller and restore guide](persistent-energy-forecasts.md).
