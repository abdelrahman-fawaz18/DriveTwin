# Electric Bus Digital Twin — DriveTwin scope and release status

DriveTwin is a local digital twin for a SUMO electric bus. Its current product paths are durable monitoring, History and replay, geographic route-aware energy forecasts, and explicitly configured guarded commands and charging. Separately evaluated thermal studies retain their laboratory or synthetic domains. The [capability and evidence matrix](docs/release/capabilities.md) is the current product index. The original [simulation acceptance profile](qualification/release/simulation.json) and [twelve-gate release record](docs/release/verification.md) retain their recorded source and configuration scopes.

## Implemented capabilities

| Area | Current implementation | Reference |
|---|---|---|
| Platform | Windows, Ubuntu through WSL, Docker Engine, pinned builds, and source checks | [Setup](docs/setup/windows-wsl.md), [engineering checks](docs/setup/engineering-checks.md) |
| Observation delivery | Registered one-bus source, twenty standard signals, original-packet SQLite queue, MQTT, PostgreSQL transactions and post-commit receipts | [Architecture](docs/architecture.md), [release verification](docs/release/verification.md) |
| Dashboard and replay | Nine views, raw measurements, all-twenty-signal History/export, map, separate replay clock and read permissions | [Dashboard verification](docs/verification/energy-dashboard.md) |
| Geographic Energy Forecast | Physics-based movement and battery accounts using remaining roads, elevation, stops, vehicle parameters and declared forward conditions; continuous refresh and immutable results | [Model card](docs/models/route-aware-energy.md), [qualification](docs/verification/energy-route-aware.md), [geographic evidence](docs/verification/energy-geographic.md) |
| Launch workspace | Ten installed trips, compatible services, pace and pause controls, reader/replay access, saved sessions and guarded recovery | [Launch guide](docs/setup/launch-app.md), [verification](docs/verification/launch-app.md) |
| Commands and charging | Separate operator, executor and observer roles; durable one-use reservations; station advice, reviewed operator stops and dedicated automatic profiles | [Commands](docs/verification/command-acceptance.md), [moving-bus scope](docs/verification/moving-bus-charging.md#operating-scope) |
| Thermal studies | Bench winding-temperature model, synthetic fault rules, matched intervention and charging coordination | [Thermal closeout](docs/verification/thermal-acceptance.md) |
| Recorded browser story | Matched Montréal charging/continuation outcomes, semantic export and causal nine-view playback | [Preparation](docs/setup/browser-demo.md), [verification](docs/verification/browser-demo.md) |
| Source preparation | Byte-verified source archive, rights and dependency notices, separately approved single-commit publication | [Source package](docs/release/source-package.md), [publication](docs/release/publication.md) |

## Energy model and timing

The current geographic profile uses one physics-based predictor for all prepared routes. A new map supplies roads, elevation, stops and operating inputs; it does not require route-specific model training. SUMO independently supplies reference battery readings. All 70 forecasts in the final ten-journey regression batch passed its stated accuracy and runtime criteria. Earlier frozen-candidate and reserved synthetic studies retain their separate outcomes and consumed claims. [Geographic results](docs/verification/energy-geographic.md)

Energy Forecast is an explicit service selection. The first calculation starts when the required inputs become valid. In the live geographic profile, another periodic update is due 120 working seconds after the preceding input cutoff. Plan changes, material changes or recovery can request an earlier update. One numerical calculation runs at a time. Earlier 30-second qualification profiles keep their original timing and results. [Operating guide](docs/setup/route-aware-energy.md)

The legacy template model, historical readers, frozen model artifacts, and consumed evaluation identities remain preserved. Completed historical raw recordings and study input copies are retired under the [minimal-retention decision](docs/adr/0075-minimal-historical-retention.md); their summaries retain their original study scopes. Legacy fitted values and evaluation splits do not describe the route-aware model. [Historical energy reference](docs/engineering/legacy-energy-example.md)

## Operating and qualification boundaries

From Windows PowerShell, use `python scripts/linux.py -- COMMAND` to run project tooling in Ubuntu. Live runs enforce host memory and storage limits, check source fingerprints, and clean up only the resources they own.

Monitoring and recovery acceptance cover one-bus workloads and a separate small two-asset identity/permission exercise. Ten-bus endurance, burst, outage and queue-capacity workloads remain deferred after recorded host-memory stops. The original [workload definitions](docs/workloads-and-metrics.md) and recorded outcomes establish the scope of those attempts.

Moving-bus advice has bounded evidence on ten original configurations at 1×. Ordinary operator approval is limited to the five named reviewed profiles and one eligible available bay. Automatic dispatch, queue activation and outage replacement have separate one-bus profiles. The 11% planning floor applies to the reviewed Ottawa and Burnaby trial conditions. [Charging scope](docs/verification/moving-bus-charging.md#operating-scope)

Commands retain a durable reservation before handoff. An incomplete handoff remains unresolved and cannot be claimed again. Authenticated runtime state, declared future assumptions, acknowledgment and independently observed effect remain distinct. Monitoring, replay, advisory, writer, operator, executor and observer permissions stay separate.

Physical integration, fleet endurance, production deployment, adaptive model promotion and automatic background retention deletion remain outside this release. Optional bounded storage stays disabled. Energy and thermal results apply only to their declared simulation or laboratory conditions.

## Documentation and publication

The [documentation index](docs/README.md) links setup, operation, architecture, interfaces, model assumptions and scoped evidence. Decision records explain engineering choices; historical checkpoints are not new release acceptance.

Original code, documentation and synthetic scenarios use the [MIT license](LICENSE). Third-party inputs retain their [notices and attribution](docs/rights/README.md). Keep credentials, machine settings, raw diagnostics, original recordings, private model artifacts and backups ignored. Local commits and private source packaging do not authorize publication. GitHub and hosting changes require separate approval of the reviewed result. [Privacy](docs/privacy.md) · [Publication procedure](docs/release/publication.md)
