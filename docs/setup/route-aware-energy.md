# Operate the route-aware Energy Forecast

Enable the route-aware Energy Forecast explicitly in the geographic launcher or a reviewed service assignment. Its [qualification record](../verification/energy-route-aware.md) and [geographic evidence](../verification/energy-geographic.md) define the tested domain. The legacy known-route forecast and stationary Charging Advisor retain their original configuration; moving-bus charging has [separate operating scope](../verification/moving-bus-charging.md#operating-scope).

## Environment

Use the Windows project terminal and the existing Ubuntu launcher. The following commands prepare a project-local Python environment from the repository lock and the separately hashed terrain-processing requirements. They start no vehicle or service.

```powershell
python scripts/linux.py --timeout 900 -- python3 scripts/route_aware_energy/setup.py
npm --prefix apps/dashboard ci
npm --prefix apps/dashboard run build
```

The setup command uses the project's pinned uv container to export exact dependencies and package hashes from `uv.lock`, then installs them into the private Ubuntu environment. It does not require a system-wide uv installation. The export container has no network and exits before installation. `--export-only` checks the export without changing the environment. Docker must already have the pinned uv image used by the engineering checks.

The numerical worker uses Python's standard library. Rasterio and NumPy are confined to trusted route preparation. PostgreSQL and HTTP dependencies remain the versions selected by `uv.lock`. Follow the existing [platform setup](windows-wsl.md) if the launcher, Docker Engine, or pinned browser tools are unavailable.

## Bounded verification commands

Each command creates a fresh ignored result folder, performs the unchanged host preflight, runs once, and cleans up its own resources. Do not launch commands concurrently. A blocked preflight starts no workload. Failed and incomplete reports remain intact.

| Command | Work | Expected real-life duration |
|---|---|---|
| `python scripts/route_aware_energy/qualify.py --case worker-database` | Isolated worker, SQL permissions/idempotency, interrupted claims, fresh-target backup restoration | About 2–5 minutes |
| `python scripts/route_aware_energy/qualify.py --case connected` | One paced bus, controller, APIs, Energy browser check, stale/reconnect behavior, accounting | About 4–8 minutes after setup |
| `python scripts/route_aware_energy/qualify.py --case study-development` | Independent accelerated development trips and isolated forecasts | About 2–8 minutes |
| `python scripts/route_aware_energy/qualify.py --case study-validation` | Separate validation route families | About 2–8 minutes |

The launcher has a 20-minute Ubuntu command timeout. If a command remains active after 25 real-life minutes, press Ctrl+C once and wait for cleanup. Do not terminate Ubuntu. Inspect `qualification.json`, the case-specific result, and cleanup fields before another attempt. A terminal exit code alone does not establish acceptance.

The connected check saves four desktop screenshots in its result folder: current forecast, selected saved forecast, stale source, and recovered forecast. If Windows RAM blocks launch, close unused applications and run the same command from Windows PowerShell from a dedicated PowerShell session.

Add `--terrain-manifest <project-terrain-cache-manifest.json>` to check the connected forecast with terrain from the cache. The launcher binds the manifest hash, and route preparation checks the terrain raster hash, projection, elevation coverage, and roadway structures. The source still supplies the configured SUMO readings. This checks the terrain input through the service and browser.

Reserved evaluation is a separate operation. `study.py freeze` requires passing development and validation reports bound to the exact candidate. `qualify.py --case study-reserved` then consumes a one-use claim before accessing reserved outcomes. An unsuccessful result cannot be relabeled or silently repeated. Use `study.py inspect --result <result-folder>` to reread a retained result without running a workload.

The B4 refresh uses `--fresh-b4` on the two guarded preparation runs, the freeze command, and the reserved run. The flag selects a separate route manifest, freeze, and claim. The freeze records the exact guarded source, the two passing reports, and the container image identities for SUMO and the forecast worker. The reserved command is `python scripts/route_aware_energy/qualify.py --case study-reserved --fresh-b4`. The original study has its own retained results and claim. [Reserved route manifest](../../qualification/energy/route-aware/study-recipes-b4-refresh-v1.json)

The expanded study uses `--expanded-routes` on those four commands. Its nine independent synthetic networks span short stop-heavy routes, repeated-road branches, climbs, and corridors up to about 59 km of assigned roads. The expanded freeze, image receipts, and one-use claim have a separate directory. Run development and validation sequentially. Freeze their passing reports before starting the reserved partition. [Expanded route manifest](../../qualification/energy/route-aware/study-recipes-expanded-routes-v1.json)

Explicit `--reference-reuse <prior-study-folder>` is supported for development and validation only. The runner checks partition, recipe, reference-generation source, complete-trip status, file fingerprints, and prior cleanup. It records which independent reference was reused. Forecast results are recalculated; the earlier failed outcome remains unchanged.

The independent study uses `sumo_euler_step_working_s: 0.2` to match the recorded reference collector's positional update. For a service assignment, set this optional policy field only when the source configuration establishes both the Euler method and its step duration. An omitted field selects the continuous model and preserves the existing request and assignment fingerprints.

The collector retains SUMO's first stop-entry reading when the simulator marks the stop as reached while the bus still has residual speed. The reading must still match the planned route occurrence, lane, and position. A study cutoff uses the first eligible sample at or after its target time, skipping junction samples and stop-entry readings with residual speed.

## Check a new OpenStreetMap route

Use the [geographic preparation and verification guide](geographic-energy.md) for new maps. It imports actual OSM roads, preserves bridge boundaries, obtains attributable elevation, selects mapped stops, and records complete input fingerprints. Missing elevation remains an error. A new map does not require model training.

The earlier departure-only portability checks remain historical evidence. Their declared-flat mode is not the current geographic workflow. Analytical fixtures and legacy frozen studies retain their original inputs.

## Configure a service session

The deployment boundary consists of existing monitoring services and a trusted Ubuntu controller. Setup must supply the following records in ignored runtime storage.

| Record | Required contents |
|---|---|
| Registered source policy | Current run, source, package/input fingerprints, twenty signals, cadence, and clock policy |
| Producer configuration | Start the Vehicle Adapter with `--route-aware-energy`; its authenticated registration path supplies route context |
| Geometry bundle | Network file/hash; geographic `raster_3d` mode with height receipts or another explicitly configured adapter; elevation origin and vertical datum; reviewed OSM structure input |
| Approved assignment | Run/asset/source, increasing revision, enabled state, matching network/bundle hashes, vehicle profile, conditions, update policy, optional scenario |
| Input credential | Dedicated `energy_input` reader scoped to the asset |
| Forecast database credential | Generated password for `evdt_forecast`; no administrative credential in the controller |
| Controller configuration | Run/asset, local Monitoring API port, credential paths, private Docker database address, geometry bundle, immutable worker image ID, bounded duration |

Apply migrations with the existing administrative migration service. Provision the assignment using `python -m evdt.monitoring.route_energy_views provision` with the JSON record on standard input. Enable the forecast login through its `enable-writer` action with `FORECAST_DB_PASSWORD_FILE` pointing to the generated private credential. Both actions require the administrative database role. Their credentials and input records stay outside source control.

Build the numerical image with the trusted runtime builder. It records the numerical source fingerprint and returns an immutable Docker image identity. The controller checks that identity against the current package before use. Choose a new output folder for each build:

```powershell
python scripts/linux.py -- .runtime/energy-v2/venv/bin/python scripts/route_aware_energy/runtime.py --output evidence/energy-v2/worker-build-01
```

The printed `sha256:...` value is the worker image identity. The same value and exact source fingerprints are saved in `worker-image.json`. This command builds an image and starts no worker or vehicle.

The controller configuration has exactly these fields. Substitute the identifiers and private file paths from the owning service session; the values below describe the format:

```json
{
  "run_id": "UUID from the registered source session",
  "asset_id": "bus-001",
  "monitoring_port": 18765,
  "reader_credential_file": ".runtime/energy-v2/session/secrets/energy_reader",
  "db_host": "private IPv4 address of the session's PostgreSQL container",
  "db_password_file": ".runtime/energy-v2/session/secrets/forecast_db",
  "geometry_bundle_file": ".runtime/energy-v2/session/geometry.json",
  "worker_image_id": "immutable sha256 value from worker-image.json",
  "max_real_life_seconds": 180
}
```

The connected qualification tool creates a complete temporary example of this configuration and the associated assignment. Credentials are passed to the controller through private files. They never enter the numerical request or worker container.

Start the controller from the project terminal:

```powershell
python scripts/linux.py --timeout 15000 -- .runtime/energy-v2/venv/bin/python scripts/route_aware_energy/controller.py --configuration .runtime/energy-v2/session/controller.json --output evidence/energy-v2/session-controller
```

The configuration duration must be a positive integer no greater than 14,400 real-life seconds. The output folder must be new. Configuration and credential paths must remain under `.runtime/` or `.private/`. The reference connected-check implementation prepares these records in a temporary session; it is an executable example of the same setup boundary.

## Interfaces and permissions

| Interface | Access | Contents |
|---|---|---|
| `POST /v2/energy/route-contexts` on Source Registration API | Registered producer, its own run/asset/source | Current assigned route, progress, stop/charging state, manifest binding |
| `GET /v2/energy-input?asset_id=...` on Monitoring API | Scoped monitoring or dedicated energy-input reader | Consistent stored state and approved assignment |
| `GET /v2/energy-forecasts?asset_id=...` | Monitoring reader | Current validity, lifecycle, up to twenty records and comparisons |
| `GET /v2/energy-forecasts/{fence}?asset_id=...` | Monitoring reader | One scoped immutable historical result |
| PostgreSQL `evdt_forecast` | Trusted controller | Scheduler claims/state, append-only results and comparisons |
| Worker standard input/output | Trusted launcher | One bounded request and result; no service credentials |

Replay credentials cannot read live forecast endpoints. Forecast credentials cannot change observations, assignments, commands, or old results. The worker cannot reach any service. Existing operator/executor/observer permissions remain separate.

## View and stop

Open the monitoring dashboard and select **Energy**. The route-aware view is available for configured vehicles. **Battery charge** compares measured charge at the forecast cutoff with the lowest predicted charge and the predicted arrival charge. The reserve marker comes from the settings saved with that forecast. **Remaining route** shows net battery energy, distance, and working duration. Expand **Input assumptions and traceability** to inspect the energy accounts and input fingerprint.

Use the **Forecast** selector or select a cutoff in **Saved forecasts** to inspect an earlier calculation. Select **Current forecast** to return to the live view. Connection loss or stale source input hides current numerical estimates. **Forecast vs. observed** shows completed comparisons over matching intervals. **Original model records** opens the original forecasts and station details. Vehicles without a route-aware assignment retain the original Energy view.

**Input coverage** shows the basis recorded by the selected forecast for vehicle state, elevation, wind, air density, equipment, and movement. A historical record can omit a source basis that later forecasts record.

Ctrl+C requests a bounded controller stop. The controller checks and removes its owned active worker before releasing durable work. A later restart reconciles interrupted claims before launching another job. Stop monitoring services through the owning session's normal cleanup command. Keep unrelated Docker resources and retained evidence in place.

## Prepare an issued weather assignment

`scripts/route_aware_energy/prepare_weather.py` downloads one Environment and Climate Change Canada City Page Weather feature. It checks issuance time, units, contiguous hourly coverage, and the approved geographic box against the prepared route. Use the active assignment JSON and the matching prepared `route.json`. Select a forecast hour that covers the simulation's working-time origin and the full assigned horizon. The clock correspondence is one forecast second per working second.

```powershell
python scripts/route_aware_energy/prepare_weather.py --site qc-147 --assignment <active-assignment.json> --route <prepared-route.json> --coverage -73.60 45.48 -73.54 45.55 --weather-time-at-working-zero <ISO-8601-time-with-offset> --output .private/energy-v2/weather-session
```

Review the output `assignment.json` and `receipt.json`, then provision that revision through the existing administrator command. The assignment is tied to the reviewed route revision. Use the current remaining stop plan when preparing a replacement after a revision. The tool preserves the downloaded bytes, base assignment, and both fingerprints. The city feed supplies wind and temperature. Mass, equipment and road assumptions retain a reference to the base condition. The vehicle's declared density applies when pressure is unavailable. [ECCC City Page Weather](https://eccc-msc.github.io/open-data/msc-data/citypage-weather/readme_citypageweather_en/)

## Propose a detour around confirmed closures

Run the detour tool with the SUMO network, a private registered source context, and the directed edge ID of each confirmed closure. Use the `context` object from the route-aware input endpoint as the source context file.

```powershell
python scripts/energy_detour.py --network <network.net.xml> --source-context <context.json> --closed-edge <directed-edge-id>
```

The proposal preserves the assigned destination, upcoming stops, and current lane. The tool uses bus-permitted lane connections to find a path and checks lane continuity before returning it. A closed stop or missing bypass produces an explicit error. The source must confirm an assigned detour before it can become the current forecast route.

## Current operational limits

- One active worker globally; this is a single-bus qualification scope.
- Geometry bundles must match the source network bytes. Reimporting a network requires a reviewed matching assignment.
- Terrain mode requires geographic projection, coverage, and structure classification. Synthetic 3D mode declares a local east/north frame explicitly.
- Saved prediction comparisons survive restart. In-flight comparison buffers do not; their unfinished count is reported.
- The weather preparation tool fetches issued forecasts on demand. Traffic, passenger and road conditions require supplied inputs. Connected assignments require administrator review.
- New route-aware forecasts do not alter the Charging Advisor or dispatch commands.
- Localized road/stop scenarios require `scenario_route_revision` in the assignment. Supply `null` when no route-specific scenario is configured. Reassign localized effects after a route or remaining-stop revision, including the appropriate present load. Stale localized assignments return unavailable.
- A persistent-mismatch notice uses three nonoverlapping saved windows for the current source, route, and assignment. Its default material-error threshold is 0.25 kWh per window; it has no automatic tuning or control authority.
