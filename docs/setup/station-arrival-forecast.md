# Station-arrival forecasting and reserved evaluation

The forecast estimates the battery energy at a station and the lowest energy on the way there or while waiting. A bus may regain some energy during final braking, so the arrival reading can be higher than the minimum on the approach. The reserve check uses the lower end of the estimated minimum-energy range against the 35 kWh reserve. [Reference drives](station-arrival-reference-drives.md)

## Inputs and models

Each prediction gets the energy and auxiliary power available at departure, directed road distance, travel time at the mapped speed limits, signalized crossings, and expected waiting time. The inputs come from the declared route and synthetic departure fixture at time zero. The fit and evaluation compare these forecasts with the measured battery traces separately. The forecast uses no later speed, signal state, observed trip duration, or battery reading.

The baseline estimates driving time from distance at 8 m/s. The route-aware candidate starts with mapped speed limits and allows for slowing and signals. It multiplies the map-based time by 1.6, adds six seconds per signalized crossing, and adds five seconds for starting and stopping. These timing choices were made using the development routes. Each model fits one propulsion cost per kilometer from the six development drives. Equipment energy equals power times estimated time. The candidate uses a simple final-braking recovery estimate and compares its energy effect with the expected wait draw when predicting the minimum.

The frozen release includes the six allowed inputs, model coefficients, domain, limits, and fingerprints. Each model's range extends on either side of its estimate by the largest development error plus 0.95 kWh, rounded up to the next tenth. This allowance is a design choice for the reserved test. It has no probability interpretation. The candidate had slightly higher development mean arrival error than the baseline, 0.076 versus 0.050 kWh, and lower worst error across arrival and minimum energy, 0.165 versus 0.221 kWh. [Frozen release](../../qualification/charging/station-arrival-release-1.0.0.json)

## Pass criteria and scope

The release requires all six reserved forecasts and outcomes, arrival and minimum-energy mean absolute errors of at most 0.9 kWh each, and a maximum individual error of 1.5 kWh. The 1.5 kWh cap is about 4.3% of the 35 kWh reserve. Optimistic minimum-energy error is limited to 1.2 kWh. Mean arrival charge error is limited to 0.26 percentage points. Both values must lie inside their respective intervals on at least five of six drives. Interval width is capped at 3.5 kWh. The reserve test requires zero false-feasible decisions, at least two correctly feasible drives, and at most one false exclusion. A false-feasible decision would recommend a station on reserve grounds even though the measured battery energy falls below reserve.

The domain is the declared one-bus, from-rest, no-charge approach study. Phase 4B can use a passing release for offline advisory work within that domain. Qualification for live inputs, moving departures, real traffic or other vehicles, and permission to dispatch automatically have separate gates. [Scope decision](../adr/0059-freeze-station-arrival-forecasts.md)

The reserved study passed. The candidate's mean absolute error was 0.238 kWh at arrival and 0.223 kWh for the minimum through the wait. The baseline scored 0.258 kWh and 0.213 kWh respectively. The candidate's worst optimistic minimum error was 0.105 kWh versus 0.272 kWh for the baseline. The candidate covered both values on all six drives, with three correctly feasible drives and zero reserve classification errors. [Measured results](../verification/station-forecast-reserved.json)

## Reproduce the development check and inspect the reserved run

1. Open PowerShell in `<checkout>` and use the installed simulator image through the project launcher. In the command below, `develop` reads the existing six reference traces and writes a fresh copy of the release and development comparison. Choose a new run ID. It does not advance any simulator drives. [Launcher guide](linux-launcher.md)

   ```powershell
   python scripts/linux.py --timeout 180 -- docker run --rm --network none --read-only --memory 512m --tmpfs /tmp:size=128m,mode=1777 --mount type=bind,source=<checkout>,target=/workspace,readonly --mount type=bind,source=<checkout>/evidence/p4,target=/output -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 /app/.venv/bin/python -m scripts.charging.qualify_station_arrival_forecast develop --run-id my-station-forecast-development
   ```

2. Check the two model summaries in the console. The new folder under `evidence/p4` contains `development-comparison.json` and `release.json`. Compare the release with the tracked frozen file. A changed source, route, or report invalidates that comparison.
3. The one-time qualification used `reserved` in place of `develop` and a fresh run ID. It verified the frozen release, created a claim bound to the drive design, and recorded predictions and their fingerprint before starting the reserved drives. Inspect `evidence/p4/station-forecast-reserved-01` for the existing qualification report, prediction seal, and per-drive results. Keep the claim with the evidence.
