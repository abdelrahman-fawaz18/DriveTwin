# Verification records

Historical reports preserve the original input scope, source fingerprints, and
recorded outcomes. The repository includes summary evidence for runs whose raw
payloads were retired. [Retention policy](../adr/0075-minimal-historical-retention.md) · [Known issues and fixes](../setup/known-issues-and-fixes.md)

The [release evidence index](../release/verification.md) records current acceptance for the local simulation release. Reports in this directory retain their original checkpoint, input scope, source fingerprints, and measured outcome. Earlier failed and incomplete attempts remain part of that record.

## Release checks

| Capability | Result and evidence |
|---|---|
| One-bus monitoring and display timing | [Thirty-minute monitoring review](one-bus-monitoring-review.json) |
| Five-minute delivery outage | [Recovery compatibility review](outage-delivery-compatibility.json) |
| Individual service restarts | [Restart matrix](service-restart-status.json) |
| Historical data restoration and current queries | [Release safety review](release-safety-review.json) |
| Live readings, map, history, isolated replay, and reconnect | [Clean-source browser reproduction](source-reproduction.json) |
| Command effects and charging comparison | [Demonstration review](demonstration-review.json) |
| Combined charging and cooling faults | [Integrated cases](charging-and-cooling-checks.json) |
| Supported schema upgrades | [Compatibility fixtures](schema-compatibility.json) |

## Model and command evaluations

| Study | Evidence |
|---|---|
| Energy development and reserved evaluation | [Calibration](energy-calibration.json), [reserved outcomes](energy-heldout.json), and [advisory closeout](energy-advisory-acceptance.md) |
| Speed commands and durable execution | [Command closeout](command-acceptance.md) |
| Charging selection, comparison, and automatic authority | [Charging closeout](stationary-charging-acceptance.md) |
| Moving-bus charging advice and operator-confirmed stops | [Geographic and connected verification](moving-bus-charging.md) |
| Laboratory thermal prediction and synthetic intervention | [Thermal closeout](thermal-acceptance.md) |

## Foundation and earlier monitoring

The foundation record covers the platform, container engine, SUMO, browser access, portable contracts, and engineering checks. The monitoring record connects the short-run acceptance, normal-queue recovery, extension, and later release results. [Foundation record](../foundation-status.md) · [Monitoring record](../monitoring-status.md) · [Original monitoring closeout](monitoring-acceptance.md)

Raw diagnostics, recordings, generated credentials, and complete per-run traces are held in ignored storage. Public reports contain reviewed results, scopes, and fingerprints. Reproduction instructions are linked from each capability guide. [Documentation](../README.md) · [Privacy policy](../privacy.md)
