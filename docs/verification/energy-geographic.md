# Geographic Energy Forecast verification

**Retention update:** original model artifacts and consumed evaluation identities remain, while completed historical raw recordings and superseded study input copies are retired under [ADR 0075](../adr/0075-minimal-historical-retention.md). Recorded numerical outcomes retain their original scopes; inspection requiring retired payloads is unavailable. See the [issue/fix guide](../setup/known-issues-and-fixes.md).


The geographic candidate extends the route-aware model with bus-legal OSM routing, road-surface elevation preparation, remaining-route lane selection, and power-aware movement. City names and route identities select input records only. They are absent from the energy equations; there is no route-specific fitted correction.

## Ten-journey accuracy result

Candidate 06 passed all ten real OSM journeys with attributed NRCan elevations. All 70 forecasts passed the unchanged accuracy, distance, convergence, reserve, and runtime checks. The same equations and numerical coefficients were used across the ten journeys. The final run is regression evidence: its outcomes had already been inspected during development.

| Journey | Recorded distance | Working minutes | Mapped stops | Largest arrival-charge error, percentage points |
|---|---:|---:|---:|---:|
| Gare Centrale–Parc La Fontaine | 3.51 km | 8.59 | 4 | 0.266 |
| Mount Royal east approach–Côte-des-Neiges | 3.96 km | 8.47 | 4 | 0.010 |
| Lachine–Sainte-Anne-de-Bellevue | 24.79 km | 45.28 | 15 | 0.028 |
| Parliament Hill–Carleton University | 4.76 km | 14.40 | 5 | 0.063 |
| ByWard Market–Place d'Orléans | 16.04 km | 38.39 | 13 | 0.143 |
| Kanata–Ottawa city center | 23.05 km | 37.63 | 9 | 0.092 |
| Waterfront Station–UBC | 11.13 km | 27.59 | 12 | 0.028 |
| Waterfront–Stanley Park–English Bay | 9.27 km | 17.28 | 3 | 0.179 |
| New Westminster–Burnaby Mountain | 17.66 km | 32.59 | 8 | 0.318 |
| Richmond–Steveston–UBC–Burnaby Mountain | 58.98 km | 112.09 | 24 | 0.471 |

The common baseline specifies a 350 kWh bus, 18,000 kg loaded mass, 80% initial charge, 8 kW equipment demand, and 20-working-second passenger dwells. Stops are selected from mapped locations; nominal spacing is a selection target, not permission to invent a stop.

| Journey | Geographic and operating combination |
|---|---|
| Gare Centrale–Parc La Fontaine | Short urban service, mapped stops, ordinary road limits |
| Mount Royal | Climb and descent, 23,000 kg loaded mass |
| Lachine–Sainte-Anne-de-Bellevue | Longer lakeshore route, rolling resistance multiplied by 1.25 |
| Parliament Hill–Carleton University | Urban signals and stops, 10.5% initial charge |
| ByWard Market–Place d'Orléans | Suburban trip, declared restriction to 65% of the road speed cap over the middle 30–50% of the path |
| Kanata–Ottawa city center | Longer regional trip, 18 kW equipment demand |
| Waterfront Station–UBC | Coastal urban trip, rolling resistance multiplied by 1.15 and driving speed cap multiplied by 0.8 |
| Stanley Park–English Bay | Curved coastal roads, park loop, mapped stops |
| New Westminster–Burnaby Mountain | Sustained climb, 24,000 kg loaded mass, 12 kW equipment demand |
| Richmond–Steveston–UBC–Burnaby Mountain | Lowlands, crossings, several destinations, final climb, 12 kW equipment demand |

The trip-balanced normalized mean absolute energy error was **0.166**, against a limit of 1.0. The worst normalized error was **0.465**, also against a limit of 1.0. The largest absolute energy error was 1.647 kWh. There were no false-safe reserve outcomes. In the low-charge Ottawa trip, measured minimum charge was 7.982%; all seven forecasts warned of the breach of the 10% reserve.

Normalized error divides each energy error by its permitted error budget. A value of 1.0 reaches that limit; 0.166 uses 16.6% of it. It does not mean a 16.6% battery-charge error. The [criteria file](../../qualification/energy/route-aware/study-criteria.json) retains the exact denominator and aggregation rules reused from the numerical study.

The recent-rate baseline was available at 49 of the 70 cutoffs. On those common cutoffs, its mean absolute error was 16.853 kWh and the model's was 0.391 kWh. The model had a larger error at two cutoffs. This baseline extrapolates consumption per meter from the preceding 30 working seconds when that window includes at least one meter of travel; stop-heavy windows can produce very large estimates. The comparisons use the same recorded destination and remain correlated within each journey.

The first eight journeys are regression evidence. Burnaby climb and the regional tour had unconsumed battery outcomes when Candidate 05 was frozen; their region was already known and some roads overlap earlier trips. Retained sessions are `geographic-evaluation-20261113T021613Z-d5535d` and `geographic-evaluation-20261113T022115Z-fceefb`. Both verified source consistency, host safeguards, and cleanup. The latter session remains failed because of its runtime result.

## Runtime correction and compatibility

Profiling the long trip identified repeated movement reconstruction when only an existing signal wait changed. The correction retains all moving phases and recomputes their timestamps in the original addition order. A new stop still requires full braking and acceleration preparation. Initial Euler braking retains the original path. The duration bound and signal convergence checks remain active.

`geographic-retiming-equivalence-verified.json` records a source-bound recomputation of all 70 saved inputs: **70 of 70 canonical result files were byte-for-byte identical**. No energy coefficient, condition, cutoff, or acceptance threshold changed. This establishes compatibility for the retained accuracy evidence; it does not turn the previous runtime failure into a pass.

The final sequential batch, `geographic-evaluation-20261113T024443Z-194b29`, passed all ten journeys after that correction. Worker completion took **3.74 real-life seconds at the 95th percentile**, with a **4.13-second maximum** across 70 jobs. These measurements include isolated-worker startup and result handling. The longest route covered 58.98 km in 112.09 working minutes. A forecast predicts its remaining journey; it does not wait that long to return a result.

The batch verified source consistency, owned-resource cleanup, and the unchanged host guard. Reference containers retained their 512 MiB limit, numerical workers retained 256 MiB, and the Windows reserve stayed at 2 GiB. `geographic-preparation-compatibility.json` verifies that the regenerated final input packages retained identical numerical networks, plant networks, and mapped stops. No ten-bus workload was run.

## Connected lifecycle compatibility

The first subsequent service check, `connected-20261113T025255Z-66b72b`, failed its periodic-refresh assertion. Browser presentation, stale-state suppression, and recovery passed, but entering a new road shortened the prepared route and was mistakenly treated as a new assignment. The controller completed 22 jobs without a worker failure; repeated route-change triggers prevented a periodic trigger. Cleanup, source consistency, and the host guard passed. This attempt remains failed.

The correction identifies an admitted route by its authenticated source-plan revision, network, coordinate/elevation provenance, and approved assignment. Ordinary progress can shorten and reindex its prepared suffix without changing that identity. Real route, assignment, and elevation changes still invalidate previous results. Localized effects use the new suffix indices during change screening. Standalone requests without an approved assignment retain complete geometry comparison.

The next service attempt, `connected-20261113T030635Z-2bbdfa`, stopped after its first result. The test checked availability and then fetched a second response; a transient state change left that second response without a current forecast. The corrected test retains the successful snapshot. Its regression check proves that no second read occurs and preserves the deadline for unavailable input. The interrupted attempt retains its failed result and verified cleanup.

Result persistence now checks the exact durable job request, owner, source, generation, input fingerprint, and terminal classification. The trusted input builder can reindex approved localized conditions onto the remaining route. The result writer retains the original assignment fingerprint and route revision while accepting that admitted projection. A different post-claim request remains rejected. The worker and browser have no authority to approve an assignment.

The updated worker/database check, `worker-database-20261113T031806Z-0330de`, passed localized-condition persistence, immutable writes, assignment-substitution rejection, role separation, restoration, and interrupted-job reconciliation. Cleanup, source consistency, and host safeguards passed. The preceding attempt stopped on a test-input timestamp newer than its forecast cutoff; that failed record is preserved.

`geographic-service-compatibility-final.json` verifies the final changes against Candidate 06. Four candidate files changed: host scheduling, change screening, and the two service qualification harnesses. The result writer is recorded separately. All are excluded from the isolated numerical worker and geographic evaluation path. All fourteen packaged worker modules, the collector, scoring, policy, and eighty prepared input files remain unchanged. The retained ten-journey accuracy and runtime results remain applicable to that numerical implementation. The frozen candidate and its consumed claims remain unchanged; the broader candidate guard continues to reject a new run after a source change. The earlier compatibility record remains preserved.

Six new regression cases cover normal progress and periodic scheduling, true route-change invalidation, unbound geometry identity, assignment/elevation changes, localized-effect reindexing, and reserve crossings. The focused lifecycle/product/scenario suite passed 42 tests with eleven parameterized subcases. The connected correction is qualified separately from the geographic battery comparisons.

The final connected check, `connected-20261113T032132Z-c81f84`, passed. One bus produced 135 accepted observations with twenty signals. The first forecast used working second 4; ten forecasts and one complete prediction/observation comparison were saved. Periodic refresh, source-pause behavior, outage recovery, assignment changes, replay isolation, and scoped access passed. The browser verified the current, historical, stale, and recovered Energy views. All ten worker jobs completed without failure. Source consistency, host safeguards, and cleanup passed.

This connected session reuses the existing flat functional fixture to qualify service behavior. It is not a slope-accuracy result or one of the ten geographic demonstrations. The separate geographic batch uses the attributed OSM and elevation inputs shown above. Comparisons spanning a route change or incomplete observations remain excluded; three comparison windows were unfinished when the bounded source ended.

## Shared engineering checks

The final shared check passed 1,087 Python tests with 196 parameterized subcases, 39 dashboard tests, 339 contract cases with zero mismatches, and 81 monitoring-parity cases. Formatting, lint, strict type checks, candidate privacy, and secret scanning passed. Its retained report is `.runtime/dev-checks/49e321c4ba2346729b9adff98d84b240/report.json`. Source and configuration remained unchanged during the check. The preceding shared checks are retained separately.

## Evidence groups

| Group | Purpose | Separation |
|---|---|---|
| Analytical cases | Physics, accounting, motion, missing inputs, and numerical constraints | Small deterministic mathematical fixtures; not geographic demonstration maps |
| Development journeys | Find general implementation defects on real roads | Montréal Plateau, West Island, Oka, Ottawa central, and Ottawa west; retained failures remain development evidence |
| New trips in known regions | Check different journeys and conditions after freeze | Montréal and Ottawa; some roads overlap development, so this is not a held-out-region claim |
| Initially held-out region | Geographic transfer after source and input freeze | Vancouver; the UBC timing failure and Stanley Park preparation failure were inspected and are now development evidence |
| New route families after correction | Accuracy on unconsumed journey outcomes | Burnaby climb and regional tour; the region is known and some roads overlap earlier trips |

The ten journey recipes are declared in the [geographic study manifest](../../qualification/energy/route-aware/osm-geographic-study.json). The candidate receipt fixes numerical source, common configuration, prepared inputs, and unchanged study criteria before the new energy outcomes are collected. Each selected evaluation creates an exclusive claim. Changing source or inputs invalidates compatibility; an inspected failure retains its consumed claim.

## Development results

The first four-trip session passed 28 forecasts across trips of 4.5, 4.8, 11.1, and 19.6 km. The largest absolute arrival-charge error was 0.0344 percentage points. Worker 95th-percentile completion was 1.49 real-life seconds. The second session passed 14 forecasts on an 11.1 km trip with a localized speed restriction and a 52.6 km trip. Its largest arrival-charge error was 0.0586 percentage points and worker 95th-percentile completion was 2.21 real-life seconds. These are development measurements, not final held-out results.

Retained sessions: `map-journey-20261112T235802Z-396f52` and `map-journey-20261113T001101Z-109524`. Each verified source consistency, host safeguards, and removal of its owned containers. No ten-bus workload was launched.

### Fixed-signal correction

Candidate 01 passed its three Montréal journeys in `geographic-evaluation-20261113T005144Z-b85b9c`. The low-charge Ottawa journey in `geographic-evaluation-20261113T005358Z-59304e` failed the mean energy-error gate: normalized mean error was 1.503 against a limit of 1.0. Its reserve warning, source checks, host safeguards, and cleanup passed. The batch stopped before its other two journeys; their claims remain retained as unrun.

The failed trip contained eight ordinary signal stops absent from the projected movement. The correction accounts for stop/start energy and waiting under a known fixed-cycle program. SUMO's signal braking bound is a separate declared parameter. No accuracy, resource, or reserve threshold changed. Recalculation on the retained Ottawa outcomes passed all seven cutoffs with normalized mean error 0.156 and maximum arrival-charge error 0.064 percentage points. The three retained Montréal trips also passed. These recalculations are development checks, not independent qualification.

Candidate 01 and all its results remain unchanged. The Vancouver battery outcomes were not inspected during this correction. A new candidate fixes the corrected source and regenerated input packages before the Vancouver evaluation.

## What the comparison measures

SUMO executes a bus on the imported roads, including native junctions and signals. Its electric model produces the battery readings. The collector records them independently of the Energy Worker. At each cutoff, the worker receives current readings, the remaining route, planned passenger stops, and declared conditions. Later battery readings are used only for scoring.

Candidate 02's six-journey batch stopped during loading of the sixth reference network. The original regional file contained 82,102 external and internal road links; SUMO exceeded the reference container's 512 MiB limit before the bus began. The five completed journeys passed their individual checks, but the batch remains failed. The stopped owned container was subsequently removed and checked separately.

Candidate 03 keeps the same numerical model and prepared geographic inputs. Its reference extractor loads the route, complete adjoining intersections, and linked signal groups, preserving their XML definitions. Remote unused approaches terminate at the extraction boundary. The original regional file remains intact. The failed Kanata input becomes 621 external and 1,139 internal links. The first two repeated reference recordings were identical. Other trips had small lane-selection differences; the new recordings are scored in full and remain regression evidence. No memory, accuracy, or reserve threshold changed.

The first Vancouver Waterfront–UBC evaluation passed energy accuracy but failed its five-real-life-second worker completion target: the recorded 95th percentile was 7.64 seconds. A diagnostic on the retained input measured about 0.2 seconds for the calculation and 1.67 seconds through the isolated container. An unchanged-source rerun passed at 1.79 seconds. The initial timing failure remains a failure; no definite cause for its additional launch overhead was established.

The following Stanley Park trip exposed a route-preparation rejection at working second 520. On a two-kilometer curved road, matching equal fractions of adjacent lane lengths put their sampled points more than five meters apart, although the actual lanes were about 3.2 meters apart. The correction matches nearby carriageway positions within a bounded longitudinal window and retains the five-meter gap limit, lane permissions, and monotonic progress. Mathematical regression cases and retained positions at working seconds 520 and 780 passed. The source revision requires newly prepared inputs and a new candidate. The two inspected Vancouver trips become development/regression evidence; remaining unconsumed journey families are recorded separately.

All geographic trips use actual OSM roads and attributable public elevation. Vehicle mass, initial charge, equipment demand, stop dwell, rolling-resistance changes, and speed restrictions are declared experiment inputs. They are not observations of present-day traffic, weather, construction, or pavement condition.

The unchanged gates cover energy error, reserve classification, numerical convergence, distance closure, runtime, and resource limits. Results are aggregated by trip as well as by cutoff. Seven cutoffs within one trip are correlated observations; they do not constitute seven independent journeys.

## Reference and input limitations

- SUMO's electric-model `maximumPower` parameter is unused. The reference records maximum observed electrical draw. The forecast enforces its own declared discharge/regeneration limits, but agreement with these reference trips does not certify real-bus power feasibility. [SUMO electric model](https://sumo.dlr.de/docs/Models/Electric.html)
- Ground and surface rasters are measurements of a geographic area, not surveyed lane-center profiles. Carriageway sampling, isolated-spike treatment, portal interpolation, and the 15-meter local grade fit are recorded approximations. They preserve intermediate hills and valleys and expose unresolved conflicts. Crossfall is not modeled.
- The ground and surface layers share a vertical datum. Bridge boundaries are protected during road simplification. Tunnel routes require separate road-height information and are rejected by the automatic raster workflow.
- The predictor handles mapped minor-road yield approaches using the visibility distance in the imported network. Optional fixed-cycle signal projection uses a declared unchanged program, including stop/start energy and equipment demand during red-light waits. Native traffic lights remain active in SUMO. Imported timing is a study assumption, not surveyed real-world signal timing. Adaptive signals and queues remain outside scope. [SUMO intersection behavior](https://sumo.dlr.de/docs/Simulation/Intersections.html)
- Wind, HVAC, battery curves, and time-varying passenger/equipment inputs retain their independent factor checks. These geographic SUMO trips do not provide a new whole-trip wind or HVAC reference. Live feeds require coverage and issuance checks.
- Automatic data retrieval currently covers the Canadian provider. A supplied attributable elevation catalog supports another region without changing the estimator. Missing or ambiguous data cannot establish a valid road profile.

Earlier frozen model artifacts, synthetic reserved studies, connected dashboard results, and failed geographic attempts retain their own versions and scopes. The geographic result does not replace a prior failure or extend a physical-vehicle claim.
