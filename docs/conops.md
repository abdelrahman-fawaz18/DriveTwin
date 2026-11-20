# Operating model

DriveTwin runs a local simulated electric bus and the services that observe, analyze, and control it. The release includes live monitoring and replay, advisory energy forecasts, guarded speed commands, charging decisions, and separately evaluated thermal studies. The release evidence defines each configuration's accepted scope. [Release evidence](release/verification.md)

**Working time** is the bus's simulation clock, measured in working seconds or working minutes. Route progress, forecast input windows, and prediction cutoffs use working time. **Real-life time** is time outside the simulation, measured in real-life seconds or real-life minutes. Reading age, browser refresh intervals, and request timeouts use real-life time. For example, a forecast can start at 120 working seconds and require a reading no more than five real-life seconds old.

## Users and responsibilities

| Role | Work | Permission boundary |
|---|---|---|
| Operator | Inspect the selected bus, review advice, submit a permitted command, and inspect its outcome | Separate operator credentials scoped to the bus and recording |
| Analyst | Inspect history, exports, forecasts, model assumptions, and evaluated results | Read access to authorized observations and analysis records |
| Integrator | Add a supported signal or asset and verify its catalog, source mapping, and service compatibility | Explicit configuration and contract changes with affected checks |
| Evaluator | Run fixed scenarios, compare policies, reconcile observations, and preserve reports | Experiment data and source truth stay separate from operational inputs |
| Executor | Claim an authorized action and apply it through the simulator interface | Separate credentials, expiry checks, local protection, and a durable reservation |
| Observer | Report whether the requested effect occurred | Independent observation credential and evidence references |

The local test harness exercises distinct role credentials. The simulated vehicle, chargers, and local protection inputs are declared by each scenario. [Command permissions](command-catalog.md) · [Charging integration](setup/registered-charging-and-dynamic-comparison.md)

## Monitoring and replay

The Vehicle Adapter samples SUMO and saves original packets to SQLite. The Sender publishes them through Mosquitto. Ingestion validates the registered source and commits observations, current state, history, and the storage receipt to PostgreSQL. The dashboard shows observation time, quality, freshness, location, and history. A matching receipt lets the Sender retire the packet.

During a disconnect, the queue retains pending packets and the dashboard reports the age of its last reading. On recovery, ingestion accepts the backlog without moving current state backward. Replay reads the saved recording through a separate API, credential, and playback clock. [Monitoring architecture](monitoring-architecture-walkthrough.md) · [Demonstration](release/demo.md)

## Forecasts and decisions

The Energy Forecast combines admitted observations with a declared drive plan. Temporary Energy Workers calculate the estimates, and the Forecast Writer saves the result with its input and model references. The Energy page checks whether a saved forecast remains current. [Forecast data path](engineering/from-readings-to-analysis.md)

The moving-bus Charging Advisor uses route-aware driving-leg estimates and current bus/station observations in its reviewed geographic profiles. Advice has no actuation authority. [Moving-bus scope](verification/moving-bus-charging.md#operating-scope)

The separately configured stationary Charging Advisor retains its qualified departure scenario and recent bus/station inputs. It checks plug compatibility, road reachability, reserve, and estimated time. An operator can authorize the selected diversion. The separate automatic profile uses an expiring one-use grant and a final protection and availability check. Subsequent observations establish charge delivered and resumed driving. [Charging policy](charging-policy.md) · [Automatic profile](setup/automatic-charging-qualification.md)

The thermal studies evaluate laboratory winding-temperature prediction and synthetic bus cooling faults and interventions. Their evaluation domains and retained results are recorded independently. [Thermal model card](models/drivetrain-thermal-model-card.md)

## Recovery and operation

The command journal records a reservation before handoff. If a reply is lost, the reservation persists and the outcome remains unknown until eligible evidence resolves it. Restart checks exercise each service independently. Backups retain the database, queue state, and required configuration, and restoration is checked against an independent target.

The documented environment uses Windows PowerShell with Linux commands forwarded to Ubuntu, Docker Engine, and fixed local inputs. Live qualification applies the recorded memory and timing guards. Each runner removes its own temporary resources. [Startup and demo](release/demo.md) · [Restart checks](setup/service-restart-recovery.md) · [Restore](setup/history-query-and-restore.md)
