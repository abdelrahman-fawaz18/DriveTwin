# Docker Compose configurations

The launcher uses `compose.monitoring.yaml` and a generated override for each session. The monitoring configuration defines the database, MQTT broker, ingestion, read APIs, and vehicle services. The other files configure specific workflows:

| Workflow | Configuration |
|---|---|
| Data flow and browser fixtures | `compose.dataflow.yaml`, `compose.browser-fixture.yaml` |
| Multi-asset fixtures and recovery checks | `compose.extension.yaml`, `compose.recovery.yaml` |
| Forecast persistence checks | `compose.energy.yaml` |
| Command execution and operator checks | `compose.commands.yaml`, `compose.arbitration.yaml`, `compose.live-speed.yaml`, `compose.operator.yaml` |
| Registered charging and automatic charging checks | `compose.charging.yaml`, `compose.automatic-charging.yaml` |

The runners pass `--project-directory .` from the repository root and select the files in merge order. This anchors relative paths in the top-level configuration to the checkout. When inheriting a service from another file, verify inherited bind sources and override any that need to stay relative to the checkout. [Runtime topology](../../docs/engineering/runtime-topology.md)
