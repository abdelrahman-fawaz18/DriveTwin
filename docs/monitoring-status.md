# Monitoring status

Monitoring is complete under the revised local acceptance policy. The final release adds the one-bus 30-minute run, clean-source browser check, current history-reader restoration, and scoped restart evidence. [Monitoring closeout](verification/monitoring-acceptance.md) · [Current release evidence](release/verification.md)

## Completed capabilities

| Capability | Evidence and reproduction |
|---|---|
| Registered motion and sampling | [Measurement plant](setup/measurement-plant.md) and [registry and quality](setup/run-registry-and-quality.md) |
| Durable delivery and stored receipts | [Operational uplink](setup/operational-uplink.md) |
| Dashboard, map, history, and downloads | [Demonstration](release/demo.md) and [browser reproduction](verification/source-reproduction.json) |
| Isolated replay | [Monitoring and replay guide](setup/monitoring-start-view-stop.md) |
| Coolant signal and second asset extension | [Extension proof](setup/sensor-and-asset-extension.md) |
| Database and queue backup | [Backup and restore](setup/backup-and-restore.md) |
| Five-minute outage recovery | [Current recovery review](verification/outage-delivery-compatibility.json) |
| History volume, queries, and exports | [Reader and restore guide](setup/history-query-and-restore.md) |
| Final local monitoring acceptance | [Closeout record](verification/monitoring-complete.json) |

## Workload history

The original short-run acceptance uses 60 warmup seconds plus 120 measured seconds for one bus and 300 measured seconds for ten buses. The manually initiated ten-bus run passed with 3,600 original messages and 72,000 readings, including warmup. [External run review](verification/external-w1-review.json)

The release's separate one-bus 30-minute run passed with 1,860 messages including warmup. Ten-bus endurance and capacity workloads were deferred after the recorded host-memory stops. Earlier short-run acceptance and later endurance attempts retain their individual policies and outcomes. [One-bus review](verification/one-bus-monitoring-review.json) · [Deferral decision](adr/0068-defer-fleet-capacity-and-reorder-integration.md)

## Optional storage mechanisms

Overflow accounting, bounded filesystems, critical-fault records, and vehicle-storage binding have separate component and integration checks. The accepted release uses the normal queue, with optional bounded storage and automatic retention deletion disabled. [Storage binding](setup/bound-vehicle-storage.md) · [Release profile](../qualification/release/simulation.json)

The verification directory retains the earlier incomplete and failed attempts, followed by identified passing corrections. The final acceptance review is the reference for current scope. [Verification records](verification/)
