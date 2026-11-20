# Monitoring smoke test

A **smoke test** is a short end-to-end run. Here it checks whether one simulated bus can send readings through the durable queue, broker, and database and whether Chromium displays the same values. The run has 60 seconds of warmup and 30 measured seconds at one event per second. Each event has 20 readings. Expect 60 warmup events and 30 measured events, for 1,800 readings across the two windows. [Frozen baseline](../../benchmarks/release-monitoring-baseline.json) · [Smoke release](../../benchmarks/release-monitoring-smoke.json)

**Current result.** Offline qualification and the November 6 live retry passed. The first attempt stopped at the memory precheck with **1.41 GiB** available Windows RAM against the **2 GiB** requirement. The first attempt started the bus and Chromium but stopped about **22 seconds into warmup** after a **1.793-second** Ubuntu clock jump. Both earlier results are preserved. [First attempt](../verification/monitoring-smoke-attempt.json) · [First external attempt](../verification/monitoring-smoke-external-review.json) · [Verified retry](../verification/monitoring-smoke-pass.json)

The original smoke release is bound to the source recorded in the verified retry. Reproduce that release at the recorded revision. The current checkout advances the shared harness under the separately pinned P6-B1 release. [Capacity guide](monitoring-capacity.md)

| Retry check | Verified result |
|---|---|
| End-to-end accounting | 90 events accepted and observed by Chromium. 1,800 readings reached storage, including 600 in the measured window. Zero drops, duplicates, quarantined events, or pending events. |
| Dashboard latency | The 95th-percentile upper bound was 0.649 seconds against a 2-second limit. The 99th-percentile upper bound was 0.658 seconds against a 5-second limit. |
| Memory and clocks | Windows had at least 4.07 GiB available during precheck and 2.45 GiB during the attempt. The clock checks passed and Ubuntu recorded zero out-of-memory events. |
| Source and cleanup | Windows and Ubuntu matched the same 313 reviewed inputs before and after the run. Docker inventories matched and the temporary sleep request was released. |

A percentile describes how long most events took to appear. The 95th percentile gives the time at or below which at least 95% of the measured events appeared. These latency estimates are upper bounds because they include the time for Chromium to confirm that the reading appeared.

The recorded retry used the external PowerShell launcher with the committed runner and memory gate. It saved an operator result, checked the Docker inventory, and released the temporary sleep request. The clock probe and live retry passed. [Launcher](../../scripts/run-monitoring-smoke.ps1) · [Clock stability](host-clock-stability.md) · [Clock readiness](../verification/host-clock-readiness.json)

## Why this runner has its own release

The Phase 1 runner protected the version it qualified. Later phases changed the dashboard, scoped read APIs, queue and source behavior, and related build inputs. P6-A2 uses the current Phase 6 baseline and reuses the existing simulator, service setup, event accounting, browser recorder, and latency calculations. Its **source guard** checks the committed release and the fingerprints of the reviewed file inventory before and after the run. A fingerprint is a short identifier that changes when the file changes. The historical Phase 1 duration selector and scope guard still apply when using the Phase 1 entry point. [Baseline compatibility audit](../verification/integrated-release-baseline.json)

The one-bus run uses the monitoring profile with command services, optional bounded storage, and automatic retention disabled. It produces a distinct smoke result. The 30-minute workloads, full host-capacity qualification, outage tests, and integrated commands have separate acceptance requirements.

## Run it from Windows PowerShell

Open PowerShell in the DriveTwin project folder. Use the existing Windows Python, pinned Playwright Chromium, and Ubuntu Docker Engine. Playwright drives Chromium and records which readings actually appear. The Windows controller uses the project's launcher to start the Linux worker when all launch checks pass.

1. Check the configuration without starting services.

   ```powershell
   python scripts/release_checks/qualify_monitoring.py --check
   ```

   Expect a passing source check and a profile with bus-001, 60 warmup samples, and 30 measured samples. This check needs neither a browser session nor Docker.

2. Run the focused offline tests if you changed the harness.

   ```powershell
   $env:PYTHONPATH = 'scripts/release_checks;scripts/monitoring;src'
   python -m unittest discover -s tests/release -v
   ```

   The path setting tells Python where to find the project modules. The tests check timing, counts, changed inputs, missing counters, memory limits, and the launch receipt. Expect `OK`.

3. Use a new evidence folder for each actual attempt.

   ```powershell
   python scripts/release_checks/qualify_monitoring.py --evidence-dir evidence/p6/my-smoke-attempt
   ```

   The controller first confirms the exact committed release and takes a local run lock. A run lock lets one controller use the test resources at a time. The precheck looks for active Docker containers, checks the 20 GiB disk reserve plus 4 GiB projected smoke growth, and samples Windows memory while Ubuntu is active. Allow about a minute for those checks. The required physical memory floor is 2 GiB available at every precheck sample.

   A passing precheck allows the controller to issue a **launch receipt**. That is a short-lived permission tied to this evidence folder. The Linux worker consumes it once. Service setup can take several minutes. The worker has a ten-minute bound, including setup and the rehearsal. Chromium runs automatically. Expect 90 paced source events and a passing final report if the entire path succeeds.

4. Read the result.

   - `phase6-preflight.json` records readiness, including memory and disk results. If it fails, `qualification.json` should show `fixture_started: false`. There should be no service or browser evidence for that attempt.
   - `qualification.json` is the overall result. `measurement.json` appears after complete collection. It joins each original event to the database and displayed reading. On a pass, check that all 90 events are accounted for and owned cleanup is empty.
   - The live guards stop on missing required counters, Windows reserve violations, Ubuntu memory or swap failures, or a disk reserve breach. The controller preserves partial evidence and targets only the test's temporary resources. On manual interruption, press **Ctrl+C once** and let cleanup finish.

Raw traces, temporary service credentials, and reports stay in ignored project folders. Preserve an unsuccessful attempt and inspect its exact cause before choosing a new one. [Phase 6 readiness and workload order](integrated-release-baseline.md)

## Review a finished run without starting another

Use the evidence folder printed by the launcher. In `operator-result.json`, verify the smoke result, cleanup, and release of the sleep request. Compare `inventory-before.json` and `inventory-after.json`. In `smoke/qualification.json` and `smoke/services/qualification.json`, compare the source fingerprints before and after the run. Check the event, latency, and resource totals in `smoke/measurement.json`. The November 6 review also recomputed that measurement from the saved traces using `build(evidence, profile, phase6=True)` in `scripts/monitoring/full_measurement_report.py` and verified that it matched the saved report exactly. [Verified retry](../verification/monitoring-smoke-pass.json)
