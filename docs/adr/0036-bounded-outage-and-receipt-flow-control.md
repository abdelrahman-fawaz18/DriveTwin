# ADR-0036: Bounded vehicle outage recovery and application receipt flow control

Date: November 4, 2026. Status: actual single-vehicle bounded-storage outage/API acceptance and shared checks passed. Fleet, browser, backup/reboot and remaining release gates stay open.

## Connect the qualified provider to normal operation

Reuse the existing source/broker/receiver/registry/API fixture. Before application startup, its Linux worker requests exact identity/catalog/capacity and an immutable local runtime image. A shared Windows `StorageLease` starts the existing scoped root provisioner, validates ready identity/owner/volume/settings and returns private binding paths. The worker declares those existing volumes external to Compose and checks the actual merged source/sender/probe mounts and read-only binding before startup. Keep one canonical source tree and normal Windows-native/WSL execution.

A lease is a time-limited reservation of temporary test storage. Stop/remove its application consumers before owner-bound release. Attempt every owned lease close even after another cleanup failure. Root cleanup still refuses referenced volumes or changed device/file/ownership associations. The temporary holding limit is not a benchmark measurement duration. Failures retain reports and available partial source/queue/critical/monitor state before owned cleanup; partial captures are not coordinated backup acceptance.

## Callback handoff and application flow control

Pinned Paho 2.1.0 calls its acknowledgment callback under an outgoing-message mutex, which `publish()` also uses. An application lock held across `publish()` and acquired in that callback can invert lock order. Its disconnected QoS-1 messages can also remain queued for reconnect. See the [pinned primary source](https://github.com/eclipse-paho/paho.mqtt.python/blob/v2.1.0/src/paho/mqtt/client.py).

Remove that application lock. The network callback records only message ID, actual callback timestamp and failure status into a bounded 512-entry memory queue. The publisher alone owns the attempt map, registers the returned ID before draining early callbacks and emits the original callback time. Preserve retained disconnected attempts. Unknown acknowledgments and trace overflow cannot become complete nominal instrumentation. A broker acknowledgment never retires a durable original; only the matching server persistence receipt can do that.

Bound unique originals awaiting server receipts with `ReceiptWindow`: default 32 originals and a five-second retry deadline measured with a monotonic clock. Repeated half-second polls do not immediately republish an already waiting original. Actual queue disappearance frees scheduling slots, whether caused by a terminal receipt or audited eviction; the cache does not decide durable disposition. The source continues acquiring into its queue. Do not mutate packet bytes or weaken loss accounting.

Startup settings `EVDT_RECEIPT_WINDOW_LIMIT` and `EVDT_RECEIPT_RETRY_SECONDS` are validated to 1–256 and finite 0.5–30 seconds. Current qualification uses 32/5 and checks the effective values through actual sender/API metrics. These are sending-policy settings; every sampling, outage, recovery, integrity, latency and resource requirement stays unchanged.

## Measured results and retained failures

The controlled concurrency test reproduced the old lock-order deadlock before the fix and now passes early-callback, disconnected-return and rejected-publication cases with exact attempt/time correlation. The first actual bounded outage missed its recovery deadline with incomplete delivery. Its process stack was not retained, so attribution of that specific stall to the reproduced defect remains an inference. The next attempt delivered every original but took 122.199 seconds and remains failed. No duplicate-delivery count was measured in those attempts; retransmission pressure was a code-level diagnosis, not a claimed measured count.

After the bounded application window, a fresh unchanged 480-observation run passed an actual 300.357-second outage and 22.987-second recovery against the unchanged 120-second limit. All 480 originals / 9,600 readings, original recipe and final current sequence 479 matched; quarantine/drop/pending were zero. Reordering actual latest/older originals never regressed current state. There were 488 monitor samples; 83 additional originals followed the first sampled complete drain. Actual API checks passed connected, stopped/unavailable, reconnected and final states, including anonymous and asset-scope denials and current verified physical/fault status.

All source/image checks and owned application/volume/device cleanup passed. Shared checks passed 503 Python / 22 frontend tests, eight strictly checked Python modules and unchanged 339+81 contract outcomes per language. Historical review manifests remain immutable; the exact broker-ack successor records this sender change. See [sanitized results](../verification/bounded-outage.json) and [the worked guide](../setup/bounded-outage-and-flow-control.md).

This is single-asset local simulation acceptance. It does not pass ten-asset outage/load, full W0/W1/Q-12, current-image historical migration, formal browser visuals, new-provider coordinated restore, deployment/reboot attachment, retention/startup or final E5 review. Commands remain disabled.
