# Documentation

DriveTwin is a local digital twin for a SUMO electric bus. Start with the [capability and evidence matrix](release/capabilities.md) for current supported behavior and the [release record](release/verification.md) for source-bound acceptance gates. Roads and elevation represent real geography; bus readings, chargers and future operating conditions are simulated or declared inputs.

## Start and operate

| Task | Guide |
|---|---|
| Inspect the recorded charging decision | [Browser demo](setup/browser-demo.md), [verified outcomes](verification/browser-demo.md) |
| Install Windows, Ubuntu and native Docker prerequisites | [Platform](setup/windows-wsl.md), [Linux launcher](setup/linux-launcher.md), [Docker Engine](setup/docker-engine.md) |
| Prepare a fresh source checkout | [Source preparation and external prerequisites](release/source-package.md) |
| Run the one-bus source-prepared example | [Local demonstration](release/demo.md) |
| Install the ten geographic presets | [Portable scenario bundle](../simulation/geographic/README.md) |
| Select a trip, services, pace and pause controls | [Launch workspace](setup/launch-app.md) |
| Use monitoring, History, export and replay | [Dashboard](setup/dashboard.md), [monitoring/replay](setup/monitoring-start-view-stop.md), [History and restore](setup/history-query-and-restore.md) |
| Review or approve a charging stop | [Moving-bus operation](setup/moving-bus-charging.md), [verified scope](verification/moving-bus-charging.md#operating-scope) |
| Recover a run or restore data | [Launch recovery](setup/launch-app.md), [backup and restore](setup/backup-and-restore.md), [service restart checks](setup/service-restart-recovery.md) |
| Review local retention | [Deliberate local cleanup](setup/local-data-retention.md), [read-only database preview](setup/retention-preview.md) |
| Diagnose setup and run failures | [Engineering troubleshooting](setup/engineering-checks.md#troubleshooting), [launcher guide](setup/launch-app.md), [source preparation](release/source-package.md) |

## Architecture and interfaces

The repository groups the runtime in [`src/evdt`](../src/evdt), the browser apps in [`apps`](../apps), and operational tools in [`scripts`](../scripts). The scripts are grouped by platform, monitoring, forecasting, commands, charging, thermal analysis, and release checks. Test suites follow the corresponding areas in [`tests`](../tests), with scenarios and acceptance inputs in [`qualification`](../qualification). Docker Compose configurations live in [`ops/compose`](../ops/compose).

Forecast service setup and checks live in [`scripts/route_aware_energy`](../scripts/route_aware_energy). Map preparation, trip inputs, and live geographic sessions live in [`scripts/geographic`](../scripts/geographic). [`scripts/known_route_energy`](../scripts/known_route_energy) contains tools for the original known-route model and its qualification.

The [dashboard source](../apps/dashboard/src) groups components, hooks, and data models under [`features/`](../apps/dashboard/src/features). The application shell lives in [`app/`](../apps/dashboard/src/app), observation models in [`monitoring/`](../apps/dashboard/src/monitoring), and shared controls and charts in [`ui/`](../apps/dashboard/src/ui).

| Reference | Contents |
|---|---|
| [Overview](engineering/quick-overview.md) and [technical walkthrough](engineering/technical-walkthrough.md) | Product behavior, units, assumptions and scoped results |
| [Architecture](architecture.md), [runtime topology](engineering/runtime-topology.md), [diagrams](engineering/diagrams.md) | Components, storage, process lifetimes, ports and ownership |
| [Engineering traces](engineering/engineering-walkthrough.md) | Durable delivery, forecast provenance and observed charging effect, linked to code and failure cases |
| [Readings to decisions](engineering/from-readings-to-analysis.md) | Observation storage, forecast calculation and actuation authority |
| [Operating model](conops.md), [requirements](requirements.md), [compatibility](compatibility.md) | Roles, contracts, scope and acceptance trace |
| [Command catalog](command-catalog.md) | Admission, expiry, reservation, execution and effect interfaces |
| [Tools](engineering/toolkit.md), [engineering checks](setup/engineering-checks.md) | Dependencies, locks, offline checks and hosted CI boundary |
| [Signal/asset extension](setup/sensor-and-asset-extension.md) | Catalog, adapter, schema and generic service compatibility |

## Models, assumptions and geography

| Reference | Contents |
|---|---|
| [Route-aware introduction](engineering/route-aware-energy-overview.md), [implementation walkthrough](engineering/route-aware-energy-details.md), [model card](models/route-aware-energy.md) | Physics-based movement and energy calculation; no route-specific training |
| [Energy operation](setup/route-aware-energy.md), [new-map workflow](setup/geographic-energy.md), [elevation data](data/route-elevation.md) | Installation, assignments, roads, heights, preparation and input quality |
| [Input boundary](design/energy-forecast-inputs.md), [lifecycle](design/energy-forecast-lifecycle.md) | Authenticated state, declared future assumptions, scheduling and recovery |
| [Environment](models/energy-environment.md), [scenarios](models/energy-scenarios.md) | Supported factors, declared profiles and provider limits |
| [Moving-bus charging](engineering/moving-bus-charging.md), [design](design/moving-bus-charging.md) | Planning requests, station data, authorization and observed effect |
| [Thermal model](models/drivetrain-thermal-model-card.md), [reproduction](setup/thermal-reproduction.md) | Separate bench, synthetic fault and intervention studies |
| [Historical energy example](engineering/legacy-energy-example.md), [stationary policy](charging-policy.md) | Frozen template predictor and original stationary charging domain |
| [Data sources](data-sources.md), [model assumptions](model-assumptions.md) | Attribution, simulation inputs and physical limitations |

## Verification and decisions

The [capability matrix](release/capabilities.md) maps supported workflows to their evidence. The [verification index](verification/README.md) records measured results, test conditions, and historical limitations. The [decision records](adr/) explain architectural tradeoffs. The [workload definitions](workloads-and-metrics.md) retain deferred fleet criteria.

The [retention policy](adr/0075-minimal-historical-retention.md) defines which original data stays local. The [known issues and fixes](setup/known-issues-and-fixes.md) identify past failures, corrections, and evidence limits.

## Source, privacy and contribution

Use the [contribution guide](contributing.md), [engineering checks](setup/engineering-checks.md) and [compatibility procedures](compatibility.md) for local changes. Keep the frozen contracts, permission boundaries and scoped evaluation claims. [Source packaging](release/source-package.md) verifies every committed file; [publication](release/publication.md) requires separate approval of the exact snapshot and destination. [Privacy](privacy.md) defines excluded local material. Original project content uses the [MIT license](../LICENSE); third-party [notices](rights/README.md) remain applicable.

Source is in `src/` and `apps/`; tools in `scripts/`; checks in `tests/` and `qualification/`; interfaces in `contracts/`, `catalog/` and `migrations/`; reproducible inputs in `simulation/`, `data/public/` and `benchmarks/`. Ignored `.private/`, `.runtime/` and `evidence/` contain local settings, installed state, reviews and raw results. They are not source-release contents.
