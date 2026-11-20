# Route-aware Energy Forecast status

**Retention update:** original model artifacts and consumed evaluation identities remain, while completed historical raw recordings and superseded study input copies are retired under [ADR 0075](../adr/0075-minimal-historical-retention.md). Recorded numerical outcomes retain their original scopes; inspection requiring retired payloads is unavailable. See the [issue/fix guide](../setup/known-issues-and-fixes.md).


The [implementation specification](../design/energy-forecast-v2.md) and [ADR-0071](../adr/0071-route-aware-energy-forecast.md) define this work. The original Energy Forecast remains the accepted implementation for its recorded domain. This page does not extend its qualification.

## Checkpoints

| Checkpoint | Status | Result |
|---|---|---|
| Specification | Complete | Three improvement tiers, contracts, lifecycle, independent evidence, resource limits, deferred hooks, and acceptance gates documented. |
| EF-01 Contracts and decision | Complete | Immutable input and result records, strict bounded JSON, vehicle/condition domains, runtime policy, and study criteria. |
| EF-02 Route and elevation | Complete | Occurrence-aware geometry, explicit lane/junction paths, bounded terrain import, true headings, stops, and a Montréal terrain-preparation check. Geographic coverage is extended below. |
| EF-03 Movement and numerical model | Complete | Continuous motion, stable energy ledger, strict results, and one-request worker passed analytical and development checks. |
| EF-04 Trusted inputs | Implemented and verified | Producer-only context registration, observation binding, current-source admission, and revision-aware route preparation passed focused checks and the one-bus connected fixture. |
| EF-05 Update lifecycle | Implemented and verified | Deterministic scheduling and restart fencing passed offline and database checks. First-ready, periodic, recovery, and reassignment behavior passed the connected fixture. |
| EF-06 Vehicle/environment factors | Implemented; offline checks passed | Weather normalization/coverage, explicit equipment loads, battery/efficiency factors, and causal residuals. Whole-trip accuracy remains EF-09. |
| EF-07 Scenario-supported factors | Implemented; offline checks passed | Occurrence-local conditions, road-class profiles, closures, stop delays, departure mass, and declared equipment inputs. |
| EF-08 Product integration | Implemented and verified | Database, worker, and one-bus service/browser checks passed. |
| EF-09 Independent model evaluation | Passed for the reserved synthetic routes | Study 2.0.1 produced 21 of 21 forecasts in each of the development, validation, and fresh reserved partitions. |
| EF-10 Connected verification | Passed for the one-bus fixture | The redesigned Energy tab passed with 15 saved forecasts and two completed comparisons. The first forecast used working second 4. Browser, access, host, and cleanup checks passed. |
| EF-11 Documentation and delivery | Complete for the opt-in simulation scope | Short and detailed guides, operating procedure, evidence review, privacy checks, and local delivery are recorded below. |
| Geographic extension | Implemented and verified within the documented input domain | Ten real OSM journeys, attributed elevation, 70 passing forecasts, unchanged estimator parameters, and passing database and connected lifecycle checks. [Current geographic record](energy-geographic.md) distinguishes new route families, repeated trips, and timing evidence. |

## Evidence policy

Accelerated launcher pacing has a separate [verification record](launch-pacing.md). Its version 2.1 input-admission correction preserves the numerical model and forecast age limits. Offline, database, and all six bounded connected speed checks passed. The 20× setting achieved 15.313× on the qualification host; current forecast availability decreased at the higher rates. Earlier connected results retain their original source scope.

Raw results belong in ignored `evidence/energy-v2/`. Downloaded terrain/weather source files remain ignored until source and rights review. Versioned synthetic recipes, protocols, aggregate results, and fingerprints can be reviewed for source control. Each result records the source/configuration it tested.

The independent accuracy result covers three reserved synthetic route families. The connected pass covers the reviewed one-bus fixture. Ten-bus endurance, physical hardware, production deployment, automatic model promotion, and new charging-control adoption remain outside this effort.

## Current service implementation

The controller uses authenticated registered route context and committed readings. It stores bounded durable claims, launches one restricted numerical worker, appends validated results, and records completed prediction comparisons. The Energy page displays version, lifecycle, age, assumptions, ledger, immutable history, and persistent-error status. Localized scenario inputs bind to the route revision; obsolete localized assignments cannot migrate onto different roads.

### Fixed-cycle yellow clearance in geographic live runs

At working second 351 in the 11.6% La Fontaine run, the bus was moving at 13.89 m/s with about 16.8 m to the signal. The forecast projected a crossing during the three-working-second yellow phase. It then required an infeasible stop because the imported schedule retained only green phases. The recorded bus continued at 13.89 m/s through the signal.

The launcher’s route-aware model `2.1.3-dev` reads yellow windows from the bound SUMO network. It permits a projected yellow crossing when adding the signal stop would violate the braking envelope. Feasible signal stops and required passenger stops remain in the plan. The rule leaves red and red-yellow stops in force. Legacy requests and the explicit source-distance candidate retain their original policies.

Offline replay recovered the failed cutoff. The other 32 requests from the four launcher runs retained their numerical summaries, and all 70 historical geographic results reproduced under their recorded policy. This check used retained inputs and started no bus. Evidence ID `forecast-yellow-replay-20261116T201558Z`.

The short introduction and implementation walkthrough are in [learning](../engineering/route-aware-energy-overview.md). The [operating guide](../setup/route-aware-energy.md) defines setup and bounded checks. No release profile, legacy reserved evaluation, original telemetry contract, or charging authority has been replaced.

## Product database and worker evidence

The isolated check in `evidence/energy-v2/worker-database-20261112T092701Z-7f885f/` passed with no bus or browser. It verified migration idempotency, one restricted worker, global ownership, immutable/idempotent result and comparison writes, role denials, fresh-target backup restoration, and interrupted-claim fencing. Cleanup and host safeguards passed. The recorded worker took about 1.06 real-life seconds; one result does not establish a latency percentile.

Subsequent changes added motion-assumption recovery, route-bound localized assignments, and persistent-error presentation. The final check records below supersede the relevant implementation checks; the earlier evidence remains bound to its own source.

## Connected attempts and corrections

| Attempt | Outcome | Correction and disposition |
|---|---|---|
| `connected-20261112T093910Z-e27c33` | Incomplete; setup read the network before observations existed | Readiness now requires an actual accepted observation, rather than the registered signal catalog alone. Cleanup passed. |
| `connected-20261112T094108Z-0fc1bb` | Failed; source registration exceeded the unchanged 128-entry input-inventory limit | The new numerical package is represented by one fingerprint of its sorted per-file inventory. Every package file remains bound, and legacy file entries remain individual. Offline mutation and size tests pass. Cleanup passed. |
| `connected-20261112-101047` | Operator attempt failed after accepted readings arrived. `/v2/energy-forecasts` returned HTTP 500 before the browser check. Host checks, source consistency, and cleanup passed. | The API now converts the trusted snapshot to JSON-compatible values before any consumer decodes it. The focused regression reproduced the failure with a fresh route both before and after a forecast was saved. Both cases pass after the correction. |
| `connected-20261112T152037Z-46a04d` | Passed on revision `45b53d7`. | One bus, 135 accepted observations of 20 signals, 11 completed forecast jobs with zero failures, two saved comparisons, and verified cleanup. Browser checks covered a current result, hidden stale guidance with history retained, and recovery after the source outage and assignment change. |
| `connected-20261112T183948Z-bb59b9` | Redesigned Energy tab review blocked before launch. | Available Windows RAM reached a minimum of 0.64 GiB during preflight, below the 2 GiB requirement. The updated browser script captured all four states using sample responses. |
| `connected-20261112T184352Z-b683c7` | Redesigned Energy tab passed on revision `e02070c`. | One SUMO bus, 135 accepted observations of 20 signals, 15 completed forecast jobs with zero failures, two completed comparisons, and verified cleanup. Chromium captured current, saved, stale, and recovered views. It verified the pink remaining-route card, matching charge values and colors, and retained history during the outage. Minimum available Windows RAM during the run was 5.59 GiB. |

In the original passing run on `45b53d7`, the first forecast used working second 4. The final history includes first-ready, periodic, recovery, and route-change triggers. The final source state was finished, with history retained. The outer host guard and source consistency checks passed. The source inventory matched that run before the collector correction below. The later source comparison isolated the change to the standalone study collector, which does not run in the connected fixture. Each result retains its recorded source fingerprint.

### Stop countdown and input coverage

In `connected-20261112T194203Z-c91cf9`, the browser passed its checks before the Linux resource sampler lost a running container's counters during an exit. The attempt remains incomplete. The remaining containers, volumes, and networks for that attempt were removed by project label.

The guarded retry `connected-20261112T194602Z-29edc3` passed. Ten positive countdown readings shared one route revision and one expected release time within a SUMO step. The run accepted 135 observations, saved six forecasts and two comparisons, and passed browser, host, source, and cleanup checks. The browser verified declared flat elevation and declared calm wind in the new coverage panel.

The guarded terrain run `connected-terrain-20261112T200202Z` passed with the NRCan terrain manifest. The forecast first used working second 6. One bus produced 135 accepted observations, six forecasts and two comparisons. Ten countdown samples passed the stop checks. The browser verified the terrain input and the current, saved, stale and recovered forecast views. The resource guard, source consistency and cleanup checks passed. SUMO's reference configuration for this connected run still uses flat elevation. The independent synthetic study supplies the measured slope comparisons.

An offline provider check downloaded the Montréal City Page Weather feature and prepared four issued hourly intervals for the saved geographic route. It verified site identity, units, issue and validity times, and route binding. A numerical check used the prepared wind and temperature for a retained cutoff. Continuous weather provisioning, live traffic and road-obstruction ingestion, and independent whole-trip accuracy for wind and HVAC remain open.

## Independent study development

The study defines three development, three validation, and three reserved route families. A standalone SUMO collector produces reference battery outcomes without importing the forecast package. Candidate requests receive present readings, assigned geometry/stops, and engineering parameters declared before the trip. Future battery states and realized future speeds are excluded. Metrics and normalization denominators are versioned with the candidate.

| Attempt | Outcome | Retained result |
|---|---|---|
| `study-development-20261112T095108Z-2fb2c0` | Incomplete after collecting a 3,724.6-working-second long trip | Forecast construction used an unsupported trigger name. The reference and failure remain intact. Cleanup passed. |
| `study-development-20261112T095721Z-475654` | Incomplete after guarded reuse of the long-trip reference and collection of the loop trip | The loop exposed a comfort-speed admission issue and an incorrectly assigned repeated-road stop in the reference setup. Cleanup passed. |
| `development-20261112-102434` | Long corridor and its seven forecasts completed. Loop collection stopped on its completed-stop progress assertion. | Host checks, source consistency, and cleanup passed. No full development score was produced. |
| `development-20261112-104127` | Reference collection completed for all three development routes. Forecast availability was 19 of 21. | Both unavailable results were `current_speed_cannot_meet_plan`. The 19 completed plans closed distance within tolerance. The study failed its availability gate. Host checks, source consistency, and cleanup passed. |

The movement correction preserves observed speed and brakes toward a softer traffic/comfort assumption. Hard legal limits, stops, acceleration limits, and energy conservation remain enforced. The saved 60-working-second input now returns an available calculation in `motion-recovery-20261112T101527Z/`. This offline diagnostic is not an accuracy score against the invalid loop reference.

The corrected reference builder assigns repeated-road stops to their explicit occurrences and rejects observed progress inconsistent with that assignment. It accumulates minimum charge at every 0.2-working-second plant step, including between persisted rows. These changes have offline tests. They require a new reference collection; old data is not silently rewritten or reused across the changed collector.

The progress check previously compared a completed stop's position on the incoming lane with the bus's position measured on a junction connector. A retained loop trace showed that SUMO can report the same route index while the bus is already on that connector. The corrected check applies the offset comparison on the stop lane and still rejects completion on an earlier route occurrence. The retained junction row now passes and the earlier wrong-lap stop still fails. Twenty-two focused tests and five subtests passed. The corrected collector completed all three development trips in `development-20261112-104127`. [SUMO vehicle state reference](https://sumo.dlr.de/docs/TraCI/Vehicle_Value_Retrieval.html)

At the two unavailable cutoffs, the remaining distance was shorter than the distance required under the declared continuous deceleration of 1.5 m/s². The required distance is the difference of squared speeds divided by twice the deceleration.

| Development cutoff | Required speed change | Remaining distance | Required continuous braking distance |
|---|---|---:|---:|
| Loop, 124 working s | 11.34 to 3.90 m/s before the junction | 36.45 m | 37.83 m |
| Switchback, 151 working s | 2.97 to 0 m/s before the scheduled stop | 2.64 m | 2.94 m |

For each cutoff, the next recorded second of lane displacement matched SUMO's semi-implicit Euler update over five 0.2-second braking steps. This update uses the speed at the end of each step and covers less distance during braking than the forecast's continuous constant-acceleration calculation. The largest absolute energy error among the 19 scored cutoffs was 0.287 kWh. The two missing forecasts prevent a complete accuracy score. [SUMO integration methods](https://sumo.dlr.de/docs/Simulation/Basic_Definition.html#defining-the-integration-method)

The study now declares the reference simulator's 0.2-second Euler position step in its forecast policy. The planner uses end-of-step speed for a bounded initial braking maneuver when the continuous calculation cannot proceed. It checks speed limits for each road piece the step travels and rejects a maneuver that would pass a required stop. Energy accounting uses the actual change in speed for kinetic work and the Euler displacement for distance-dependent work. The continuous planner handles the remaining trip. Route families, braking limits, cutoffs, and acceptance criteria retain their original definitions.

The isolated rerun `study-development-20261112T155758Z-2f80f7` passed. All three reuse receipts matched the retained collector, route recipes, and reference bytes. The runner started no reference simulator. All 21 cutoffs produced forecasts and closed distance, with zero false-safe reserve outcomes and 1.31 real-life seconds at the worker's 95th percentile. The trip-balanced normalized absolute error was 0.118 against a limit of 1.0. The run passed its host and cleanup checks and meets the development evidence requirements for a later freeze. The 19-of-21 attempt remains a separate failure record.

The focused route-energy suite passed 182 tests and 148 subtests. Strict typing and lint/format checks passed. All 11 saved connected requests retain their original canonical bytes and reproduced their recorded default-policy results within numerical tolerance. The largest net battery energy difference was 0.00061 J. The connected pass covers the default continuous configuration. The new study configuration carries its own model version and evidence.

Acceptance additionally requires distance closure, all selected supported cutoffs, trip-balanced energy error, worst-case error, zero false-safe reserve outcomes, numerical convergence, and measured isolated-worker runtime. A freeze requires passing development and validation results, current source fingerprints, and successful outer host/resource guards. A late memory failure cannot be masked by a successful inner calculation. Reserved evaluation consumes its claim before accessing outcomes.

## Validation and reserved evaluation

| Check | Result | Reviewed evidence |
|---|---|---|
| B2 validation | Passed | `validation-20261112-110703` produced 21 of 21 forecasts on three independent validation routes. Every plan closed distance. The trip-balanced normalized absolute error was 0.284 against a limit of 1.0, the worker's 95th percentile was 0.77 real-life seconds, and there were zero false-safe reserve outcomes. Host and cleanup checks passed. |
| B3 candidate freeze | Verified | The freeze binds revision `c7b54f8` and the exact passing development and validation reports. Both guarded qualification hashes, the candidate inventory, and the source fingerprints matched on review. |
| B4 reserved evaluation | Incomplete; claim consumed | `reserved-20261112-110703` completed seven forecasts each for the coastal and mountain routes. The industrial-ring reference collector aborted before saving a complete reference. Host, source consistency, and cleanup checks passed. |

The original industrial-ring run raised `Reference current stop differs from its assigned occurrence` before retaining the offending observation. The two completed reference files matched their recorded hashes. That run stopped before calculating aggregate reserved metrics. The claim matches the recorded freeze and output directory.

### Stop-entry correction

The development loop reproduced the assertion at 57 working seconds. The route index, lane, and position matched. SUMO reported the stop as reached at 0.044 m/s. SUMO 1.28 can mark a stop as reached on a step with residual speed up to 0.1 m/s. [SUMO 1.28 stop processing](https://github.com/eclipse-sumo/sumo/blob/v1_28_0/src/microsim/MSVehicle.cpp#L1785-L1808) · [Halting speed definition](https://github.com/eclipse-sumo/sumo/blob/v1_28_0/src/utils/common/StdDefs.h#L56-L57)

Collector version 2.0.1 retains that entry reading, requires the original route occurrence, lane, and position checks, and applies the 0.1 m/s ceiling only to the first step of the stop. The study selects the next eligible recorded sample if a target lands on a stop-entry transition. The energy ledger retains the full reference history. The forecast model, trip families, target times, and accuracy thresholds retain their definitions.

All six guarded development loop variants completed. A motion-only diagnostic rebuilt the historical industrial-ring route with identical route and network bytes. It recorded the bus entering the second scheduled stop at 0.092 m/s at 165 working seconds, then verified completion of all seven scheduled stops. The diagnostic read no battery data and produced no forecast or accuracy score. The host and cleanup checks passed. The route-aware offline suite passed 183 tests and 153 subtests.

| Study 2.0.1 partition | Result | Forecasts | Trip-balanced normalized absolute error (limit 1.0) | Worker p95, real-life s (target 5) |
|---|---|---:|---:|---:|
| Development | Passed | 21/21 | 0.118 | 2.77 |
| Validation | Passed | 21/21 | 0.284 | 1.08 |

The guarded runs `stop-fix-development-20261112-112605` and `stop-fix-validation-20261112-112605` regenerated the six independent reference trips. Recorded readings matched the prior references after excluding the new transition field. Both partitions passed distance closure and convergence, recorded zero false-safe reserve outcomes, and passed the host, source, and cleanup checks.

The original freeze, reserved claim, and incomplete result remain intact.

### Fresh reserved evaluation

The B4 refresh uses three newly designed route families. The six development and validation families retain their assignments and reference inputs. Route layouts, trip identities, and random seeds were selected before the reserved runs. The acceptance thresholds, forecast code, and corrected reference collector retain their definitions.

| Route family | Scenario |
|---|---|
| Market grid | Offset streets, frequent turns, six scheduled stops, and elevated equipment demand |
| Escarpment traverse | Two climbs over a roughly 15 km route, higher vehicle mass, and low departure charge |
| Three-pass junction | One road traversed three times through two different return branches, with stops tied to specific visits |

Each trip requires seven distinct forecast samples. A separate freeze binds the preparation reports, source inventory, trip manifest, criteria, and SUMO and worker image identities. A dedicated claim records when the reserved outcomes are opened. [Fresh route recipes](../../qualification/energy/route-aware/study-recipes-b4-refresh-v1.json)

The first refresh development attempt, `b4-refresh-development-20261112-114827`, completed 21 forecasts and failed its Linux resource guard when a short-lived worker's counters disappeared during Docker inspection. The attempt retains its failed status. The study sampler now takes one fresh inventory after a 50 ms wait when a configured finite job loses its counters while Docker still reports it running. A continuing gap, abnormal exit, or exceeded resource limit still fails. The 31 focused tests passed, including an out-of-memory exit and a persistent missing-counter case.

Both preparation runs tagged `20261112-115301` passed. Their worker builds had identical platform manifests and distinct provenance attestations, which gave the enclosing image indexes different identities. The freeze stopped before opening reserved outcomes. Preparation now builds one worker image and verifies its exact identity for each partition. The reserved run uses the image recorded in the freeze. [Docker build attestations](https://docs.docker.com/build/metadata/attestations/)

**B4 passed.** The development and validation reruns and the reserved run tagged `20261112-115752` each completed 21 forecasts. The preparation runs reused six complete reference trips after verifying the collector, recipes, network and reference fingerprints, and prior cleanup. The guarded runner generated all three reserved references after the freeze. Review matched the exact source, reference and worker images, run report hashes, and consumed claim.

| Reserved route | Trip duration, working s | Forecasts | Mean absolute energy error | Minimum SUMO charge |
|---|---:|---:|---:|---:|
| Market grid | 436.6 | 7/7 | 0.171 kWh | 78.58% |
| Escarpment traverse | 1,365.6 | 7/7 | 0.646 kWh | 5.61% |
| Three-pass junction | 814.4 | 7/7 | 0.344 kWh | 77.73% |

The trip-balanced normalized absolute error was 0.280 and the worst normalized error was 0.268, each below its limit of 1.0. All plans passed distance closure and convergence. Worker p95 was 1.08 real-life seconds against a target of 5. The Windows memory minimum was 4.31 GiB against the 2 GiB guard. Host, source, and cleanup checks passed.

The escarpment trip crossed below the 10% reserve. All seven forecasts for that trip predicted a reserve breach. These are seven forecast windows from one trip. Across all three trips, mean absolute energy error was 0.537 kWh at the first observation and 0.039 kWh at the 75% trip-time sample.

At the 15 cutoffs where recent-rate extrapolation was available, its mean absolute error was 10.223 kWh and the route-aware forecast's was 0.327 kWh. The baseline was closer at 408 working seconds on the three-pass junction route. Its absolute error was 0.004 kWh versus 0.194 kWh for the route-aware forecast.

This result covers simulated battery outcomes under the declared route, mass, equipment, and movement assumptions. Wind, live traffic, charging scenarios, and other factor-specific behavior retain their separately recorded evidence.

### Expanded route evaluation

The guarded expanded study tagged `20261112T195246Z` passed development, validation, and reserved partitions with three fresh networks and 21 forecast cutoffs per partition. The reserved intercity corridor took 4,717.6 working seconds and the metro route took 3,726.2 working seconds. The reserved trip-balanced normalized mean absolute error was 0.187 against a limit of 1.0. Maximum arrival-charge error was 0.373 percentage points. The worker's 95th-percentile time was 1.57 real-life seconds. No false-safe reserve outcomes were observed. The source, resource, and cleanup checks passed. [Expanded route manifest](../../qualification/energy/route-aware/study-recipes-expanded-routes-v1.json)

The frozen candidate inventory differs from the current tree in three helper files. Those changes cover connected terrain admission, the connected fixture, and the standalone weather preparation tool. The model, study collector, study runner, recipes, and criteria still match the evaluated hashes. The original reserved outcome remains bound to its recorded candidate.

Separate factor comparisons reused the independent SUMO development and validation outcomes with the matching model source. On validation cutoffs, holding roads flat raised mean arrival-charge error from 0.096 to 0.360 percentage points across 21 cutoffs. Holding vehicle mass at 18 tonnes raised it from 0.140 to 0.417 across seven cutoffs. Holding equipment demand at 8 kW raised it from 0.106 to 0.589 across 14 cutoffs. These comparisons record calculation timing and reserve outcomes. None of the preparation reference cutoffs crossed below reserve. The reference model does not independently measure wind or cabin HVAC behavior. Raw comparisons remain in the ignored evidence directory.

## OpenStreetMap route portability

The current geographic workflow prepares real OSM roads, mapped stops, and attributed ground or roadway-surface elevations. It uses one numerical model across ten journeys of 3.5–59 km. All 70 recorded predictions passed the accuracy criteria. Runtime corrections, current compatibility, and the final service checks are recorded in [geographic verification](energy-geographic.md). The [new-map guide](../setup/geographic-energy.md) defines the required inputs and preparation commands.

### Initial import diagnostics

The following table retains the early preparation results. Its declared-flat diagnostics are not the final geographic accuracy evidence. The current workflow requires attributable heights and records missing or ambiguous coverage.

Geographic acceptance uses real OSM roads. Separate Montréal and Ottawa extracts supply roads, access tags, and retained turn restrictions. SUMO NETCONVERT imports those roads. The route tool selects directed bus roads near independently chosen geographic anchors and verifies that the reduced network retains the route. Every departure check uses the same numerical model and vehicle-profile fingerprint with a different prepared route. A passing departure check verifies route import, geometry, and forecast execution. Accuracy against independent SUMO battery readings requires a separate full-trip reference.

| Imported route | Assigned road length | Departure forecast |
|---|---:|---|
| Montréal Plateau | 4.54 km | Available with the declared flat check and the 10 m NRCan terrain grid |
| West Island | 19.27 km | Available with declared flat elevation after fixing duplicate lane-change vertices |
| Ottawa Central | 4.61 km | Available with declared flat elevation. With the 10 m NRCan terrain grid, the assumed driving plan exceeded the declared battery power limit and the worker withheld an arrival charge |
| Ottawa West | 10.75 km | Unavailable. The selected road sequence needs a lane transition within a 0.20 m connecting segment |
| Montréal to Oka | 50.22 km | Unavailable. The selected road sequence needs a lane transition within a 9.08 m connecting segment |

The duplicate-vertex fix dropped coincident samples from assumed lane-change shapes. The West Island retest used the same imported route and vehicle profile. The lane-continuity, bridge-height, and power-limit findings led to the subsequent legal-lane routing, surface-elevation processing, and power-aware movement work described in the current geographic record. These original outcomes remain unchanged. [Montréal OSM extract](https://download.bbbike.org/osm/bbbike/Montreal/) · [Ottawa OSM extract](https://download.bbbike.org/osm/bbbike/Ottawa/)

## Original route-aware verification

| Check | Result | Evidence and scope |
|---|---|---|
| Shared engineering checks | Passed | `.runtime/dev-checks/550edcec9c1a47b081081e7448f627fa/`: 1,017 Python tests and 155 parameterized subcases, 38 dashboard tests, 339 portable contract cases with zero mismatches, 81 monitoring parity cases, lint/format, strict typing, TypeScript, and dashboard build. |
| Direct database permissions and worker | Passed | `worker-database-20261112T103304Z-fac902/`: migrations through 011, immutable/idempotent results, direct assignment-substitution rejection, role restrictions, global ownership, interrupted-claim fencing, and fresh-target restoration. No bus or browser. |
| Worker isolation and cleanup | Passed for the database fixture | The restricted numerical worker completed in about 1.16 real-life seconds. The host guard, source guard, and owned-resource cleanup passed. This single calculation does not establish a latency percentile. |
| Ubuntu setup | Passed | Pinned uv dependency export and hashed installation completed in the project-local environment. Dependency consistency passed. The terrain Click pin was aligned to the existing application lock. |
| Connected service/browser | Passed | The one-bus result above followed the API correction, which passed 33 focused tests and 64 subtests. |
| Independent forecast accuracy | Passed under study 2.0.1 | 21 forecasts across three fresh reserved routes. The original incomplete attempt retains its own freeze, claim, and result. |

The shared check's source remained unchanged during execution. Changes through the initial development delivery were limited to the direct-assignment database trigger and its fixture, the standalone setup command, and terrain dependency alignment. The final database fixture verifies the trigger. The setup command was executed, and affected preparation, admission, and product tests were rerun. The later API correction changes snapshot serialization in the monitoring view. The focused checks above cover that change. Study 2.0.1 changes reference collection and cutoff selection. The numerical model, scheduler, and dashboard retain their prior source.

Compatibility review confirms that the earlier database source inventory matches after accounting for the added setup command and the terrain-only dependency pin. Neither participates in its numerical/SQL path. Legacy energy code, original study definitions, original telemetry contracts, and the portable legacy reader retain their committed content. Existing release evidence keeps its original source scope; it is not promoted into acceptance for the new connected path.

The [short introduction](../engineering/route-aware-energy-overview.md), [implementation walkthrough](../engineering/route-aware-energy-details.md), [model card](../models/route-aware-energy.md), and [operating guide](../setup/route-aware-energy.md) describe the implemented behavior and remaining gates. The external sequence is connected verification, independent development, validation, candidate freeze, and one-use reserved evaluation. Each workload runs once, preserves its result, and stops on failure. No ten-bus workload is part of this sequence.

Candidate and staged privacy/secret scans cover the delivered source. Raw recordings, temporary credentials, failed attempts, private backups, and local operator instructions remain ignored. Public documentation links were checked. Delivery is local; publication is not authorized.

## EF-01 result

The focused check passed on Ubuntu Python 3.12 using the development-tool versions and package hashes recorded in `uv.lock`. It passed 28 tests with 74 parameterized subcases, Ruff lint/format checks, and strict Mypy checks for the five new package files. Source fingerprints were unchanged during verification. The raw report is retained under ignored `evidence/energy-v2/ef01-20261112T064741Z/`.

The cases cover finite numbers, units and physical limits, immutable records, curve domains, repeated route occurrences, missing elevation, bridge/tunnel ground-height rejection, valid zero readings, directional wind, source identities, strict/duplicate JSON fields, input bounds, route-revision consistency, condition availability, and canonical fingerprints. These are input and policy checks. No bus, database, container, or browser was started, and no forecast-accuracy or process-isolation result is inferred.

The project-local lightweight environment is ignored under `.runtime/energy-v2/venv/`. It supplements the existing shared checks for focused development. The shared strict type-check list now includes the new package; the full shared suite is reserved for the stabilized implementation.

## EF-02 result

The focused Ubuntu check passed 60 tests with 74 parameterized subcases, Ruff lint/format checks, and strict Mypy checks for eight package files. Source fingerprints were stable. The report is retained under ignored `evidence/energy-v2/ef02-20261112T071818Z/`. No bus, database, container, or browser was started.

Static preparation used the reviewed Montréal road subset, its assigned mission, and an existing SUMO network file. This reuse concerned road geometry only; no original model evaluation outcomes were used. A new approximately 1 MB NRCan terrain clip was downloaded, hashed, and inspected. It contains 175,100 valid cells at approximately 10-meter spacing. The raster and request receipt remain in ignored download storage.

The prepared route contains 203 road/junction occurrences, 724 vertices, three declared adjacent-lane changes, and no missing terrain points. Remaining-path distance at the route start is 5,150.005781 meters. A separate sum of three-dimensional vertex distances agrees. Ascent is 40.637935 meters; descent is 40.545317 meters. The maximum sampled absolute grade is 0.136802. These are geometry results; the existing flat plant and energy-accuracy claims are unchanged. Raw preparation and geometry reports are retained under `evidence/energy-v2/ef02-montreal-terrain-01/`.

The implementation retains missing-data failures, bridge/tunnel roadway-height requirements, nominal-lane versus geometric-length distinctions, stop-completion admission, and explicit lane-change assumptions. The supported lane-change domain and terrain-provider limits are described in [route and elevation preparation](../data/route-elevation.md). The geospatial dependency lock is confined to optional preprocessing; the numerical worker does not import it.

## EF-03 result

The focused Ubuntu check passed 86 tests with 76 parameterized subcases, Ruff lint/format checks, and strict Mypy checks for twelve package files. The retained report is `evidence/energy-v2/ef03-20261112T073620Z/`. The preceding type-check failure remains recorded separately. The checks start no bus, database, container, or browser; the worker subprocess check verifies its input/output boundary without claiming container isolation.

Independent arithmetic cases cover distance-closing triangular and trapezoidal motion, stops, braking feasibility, turn limits, flat cruising, signed gravitational work, kinetic energy, head/tail/crosswind, full-battery recovery rejection, equipment supplied by regeneration, discharge-power limits, depletion followed by recovery, far-horizon equipment assumptions, accounting balances, and 0.2 versus 0.1-working-second integration. Result checks reject mismatched source/generation, corrupt balances, unknown fields, and nonfinite values. An infeasible plan omits achievable arrival charge.

An offline development calculation used the prepared terrain route and the declared 350 kWh vehicle profile, starting at 80% charge and rest with 8 kW auxiliary demand. It produced a 656.443577-working-second motion plan and 9.218661 kWh net energy at the 0.2-working-second step. The 0.1-second result was 9.218681 kWh, a difference of approximately 0.00002 kWh. Minimum predicted charge was 77.3525%. The process's recorded peak resident memory was 21,888 KiB; individual pure calculations took approximately 0.03–0.04 real-life seconds. These two development samples do not establish a runtime percentile, sandbox memory gate, or accuracy against a plant.

The raw input, both outputs, timings, and source fingerprints are retained under `evidence/energy-v2/ef03-terrain-development-02/`. The earlier development result remains intact. Geometry evidence remains applicable: the EF-03 contract addition concerns the forward auxiliary assumption, and the complete focused suite reran the geometry tests. Original model code, plant inputs, evaluation claims, telemetry contracts, and release qualifications are unchanged.

The [development model card](../models/route-aware-energy.md) describes the implemented movement assumptions and energy accounts. At the EF-03 checkpoint, source admission, scheduling, factor-specific qualification, persistence, and connected operation remained pending; their subsequent results are recorded in this document.

## EF-04 result

The focused Ubuntu check passed 153 tests with 117 parameterized subcases, Ruff lint/format checks, and strict Mypy checks for fifteen route-aware package files. The final source-fingerprinted report is retained under `evidence/energy-v2/ef04-20261112T080157Z/`. The earlier report remains intact. No bus, database, container, or browser was started.

The new source context is explicitly enabled, producer-authenticated, and linked to a scalar observation by a canonical content fingerprint. Tests cover cross-asset/source/run/epoch/session rejection, replay exclusion, stale/future timestamps, working-time agreement, charging, required units/quality, route changes, stop semantics, request bounds, and source endpoint permissions. Existing queue, registered policy, and plant tests also passed. Approved network preparation supports exact route occurrences and internal junctions. The automatic SUMO projection explicitly rejects ambiguous repeated-road stops and unsupported departure conditions.

The original telemetry schemas, twenty-signal catalog, legacy energy implementation, and consumed studies are unchanged. Shared source and registration application files have changed additively; their previous connected qualifications do not qualify the new context path. Migration 008, database role enforcement, and actual TraCI behavior remain connected gates in EF-08/EF-10. The original shared-source guards and historical fingerprints were not rewritten. [Input architecture and supported domain](../design/energy-forecast-inputs.md)

The test environment now includes existing API dependencies at their `uv.lock` versions and wheel hashes. No new application dependency or service framework was introduced.

## EF-05 result

The final focused Ubuntu suite passed 177 tests with 117 parameterized subcases, Ruff lint/format checks, and strict Mypy checks for sixteen route-aware package files. The source-fingerprinted report is retained under `evidence/energy-v2/ef05-20261112T082044Z/`. Earlier development results remain retained. No bus, database, container, or browser was started.

Explicit-clock cases cover first-ready and periodic updates, the newest waiting snapshot, missed-period accounting, pause/resume, source epoch changes, working-clock regression, materiality persistence and hysteresis, reserve-trigger priority, current-input versus forecast-age distinctions, worker deadlines, UTC rollback, stale completions, duplicate/foreign fences, one retry, stable retry exhaustion, and restart reconciliation. A new route/model assignment can recover an exhausted context without reviving its old claim.

Migration 009 and the controller store add durable snapshots, monotonically allocated fences, immutable claim identities, terminal outcomes, a global session lock, and a database constraint permitting only one running job. Offline storage tests check ownership and cross-asset/old-owner rejection. SQL execution, concurrent database locking, actual worker cleanup, and restored-state behavior remain connected gates. [Lifecycle design](../design/energy-forecast-lifecycle.md)

## EF-06 result

The final focused Ubuntu check passed lint, format, strict type, and affected unit checks. Its source-fingerprinted report is `evidence/energy-v2/ef06-20261112T083809Z/`. Separate arithmetic qualification passed nine constant-speed cases against closed-form work and equipment-energy calculations: baseline, headwind, tailwind, crosswind, air density, mass, rolling resistance, aggregate equipment power, and drive efficiency. The factor report is retained as `evidence/energy-v2/ef06-factor-arithmetic-02.json`.

Additional unit cases cover forecast issue/availability and location coverage, explicit weather-to-working-time mapping, HVAC heat balance and power limits, cold inaccessible capacity, component interpolation, aggregate-load double-counting prevention, causal prediction prefixes, charging/source/route/gap rejection, and independent persistent-error windows. These checks establish the implemented arithmetic and input boundaries. They do not establish external weather-provider accuracy, cabin comfort accuracy, or whole-trip forecast accuracy. No bus or service was started.

## EF-07 result

The final source-fingerprinted check is retained under `evidence/energy-v2/ef07-20261112T084614Z/`. The affected suite, Ruff lint/format, and strict Mypy checks passed. The preceding type-check failure remains retained. Eight new scenario tests cover occurrence-local speed/rolling/wind, road-class expansion, departure mass and completed-stop persistence, dwell/equipment consumption, doors/solar, closure abstention, unavailable/future inputs, and current-source dwell. Legacy frozen artifacts and telemetry schemas remain unchanged.

The optional scenario record is now part of the development request and lifecycle context identity. It participates in the bounded worker calculation and causal-prefix assessment. There is no new weather feed, automatic rerouting, or control permission. [Scenario inputs and limits](../models/energy-scenarios.md)

Product integration and its connected results are recorded above.
