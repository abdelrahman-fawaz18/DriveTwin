# Monitoring capacity verification

This checkpoint asks whether the normal monitoring system can keep up for 30 minutes. For example, ten buses each send one message with 20 readings every second. The test verifies that the original values reach the database and the fleet dashboard, measures their display delay, and checks how much memory is still available while the system runs.

**Status.** W1-30 is deferred at the maintainer's request. The one-time diagnostic stopped during measurement and its claim was used. W0-30 remains passed. The procedures below document the official test configuration for future qualification. [Deferral decision](../adr/0068-defer-fleet-capacity-and-reorder-integration.md) · [Diagnostic review](../verification/fleet-memory-diagnostic-review.json)

W0-30 passed its live run and evidence review. All 1,860 messages and 37,200 stored readings reconciled. At least 95% of measured messages appeared within 0.713 seconds against a 2-second limit. Windows had at least 2.41 GiB available during the run, and the Docker inventory and temporary sleep release checks passed. [Preparation record](../verification/monitoring-capacity-preparation.json) · [W0-30 review](../verification/one-bus-monitoring-review.json)

Both W1-30 attempts passed the launch check and stopped on the 2 GiB Windows memory rule before the measured window began. The first reached 1.47 GiB and matched 240 warmup messages to database commits. The plugged-in retry reached 1.81 GiB and matched 280 warmup messages to database commits. Cleanup and temporary sleep release passed in both. The difference between the attempts does not establish a power-source effect. Diagnose fleet startup memory use before another full run. [First W1-30 review](../verification/fleet-monitoring-first-attempt.json) · [Plugged-in retry review](../verification/fleet-monitoring-powered-attempt.json)

| Profile | Buses | Warmup messages | Measured messages | Measured readings | Total messages |
|---|---:|---:|---:|---:|---:|
| W0-30 | 1 | 60 | 1,800 | 36,000 | 1,860 |
| W1-30 | 10 | 600 | 18,000 | 360,000 | 18,600 |

Each profile runs 60 warmup seconds and 1,800 measured seconds at one message per bus per second. A shared start signal releases all sources after the services and browser are ready. All messages must reconcile, including warmup. The measured window supplies the latency percentiles. Command authority, optional bounded storage, and automatic retention are disabled in this profile. The 30-minute passes have their own scope. The two-hour soak and fault cases have separate checkpoints. [Frozen baseline](../../benchmarks/release-monitoring-baseline.json)

## Prepare and operate

The external PowerShell launcher selects W0-30 or W1-30, validates source and host readiness, records the result, and removes its temporary sleep request. The recorded procedure ran each workload separately from a dedicated PowerShell session. Fleet endurance remains deferred under the release scope. [Launcher](../../scripts/run-release-monitoring.ps1) · [Release evidence](../release/verification.md)

To check the committed inputs and installed browser tools without starting either run, add `-CheckOnly` to either PowerShell command in the Notepad guide. Expect `CHECK ONLY: no attempt or sleep request started.` A source fingerprint records the exact bytes used. The P6-B1 release has its own fingerprint. P6-A2 evidence remains bound to its earlier revision and the original source guard. [P6-A2 result](../verification/monitoring-smoke-pass.json)

For each real attempt, the launch check needs at least 2 GiB available Windows memory and 1 GiB Windows commit headroom at every sample over at least 30 seconds with Ubuntu active. Commit headroom measures how much more memory Windows can promise to back with RAM or disk. Free disk space must be at least 24 GiB for W0-30 or 28 GiB for W1-30 on Windows and Ubuntu. Those figures allow 4 or 8 GiB of projected growth while preserving 20 GiB free. The growth amounts are conservative launch allowances. The timer and thresholds do not change between attempts. [Frozen resource rules](integrated-release-baseline.md)

## Read a result

1. Check `operator-result.json` for the correct workload and release, `passed`, `cleanup_verified`, and `sleep_request_released`. Compare the Docker inventory files from before and after the run. The comparison covers containers, volumes, and networks. Locally built images can remain cached.
2. Check `monitoring/qualification.json` and `monitoring/services/qualification.json` for the exact workload, matching before and after source fingerprints on Windows and Ubuntu, and service cleanup.
3. In `monitoring/measurement.json`, check exact counts, full wall-clock duration for every bus, latency, and memory. Latency is the elapsed time from source to dashboard. The conservative 95th- and 99th-percentile bounds must be at most 2 and 5 seconds, and each message must be witnessed within 10 seconds. The percentiles describe the time by which at least 95% and 99% of measured messages appeared.
4. Confirm complete resource samples and the unchanged capacity conditions. Those include at most 6 GiB peak application memory, at most 30 consecutive seconds below 2 GiB available on Windows, zero Linux out-of-memory events, and the Linux rolling swap limit. Swap is disk activity used to move memory pages out of or back into RAM. A rolling window recomputes the previous 60 seconds at each sample. Three consecutive windows above 64 MiB fail. Windows retains its emergency floor and Ubuntu and Windows retain their clock checks.

The live Linux checker validates each new sample once and keeps only the recent history needed for the rolling swap calculation. This keeps the work at each sample bounded as the run gets longer. The completed report checks the same rules against the saved timeline. W1-30 supplies the host-capacity evidence for this operating configuration. Raw logs and reports stay under ignored `evidence/p6/external-b1-*` folders. A launcher pass is ready for review, and an incomplete run retains its recorded evidence.
