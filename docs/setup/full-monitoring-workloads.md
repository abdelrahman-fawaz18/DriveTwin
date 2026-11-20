# Short-run monitoring acceptance workloads

This guide documents the historical short-run monitoring runner. The [monitoring record](../monitoring-status.md) records its final acceptance, and the [release evidence](../release/verification.md) includes the later one-bus 30-minute qualification.

## What is being measured

A workload is a precisely specified amount of work. Under `phase1-local-monitoring/1.3.1`, W0 sends one bus's 20 readings each second for 120 measured seconds; W1 sends ten buses' 20 readings each second for 300 measured seconds. Each includes 60 additional warmup seconds. W0 measures 120 events / 2,400 readings; W1 measures 3,000 events / 60,000 readings. Warmup events remain separately counted and must arrive intact. These checks do not prove sustained-load readiness. The separate release profiles define the 30-minute workloads. All latency, resource, identity and quality thresholds remain unchanged. See [ADR-0037](../adr/0037-monitoring-acceptance-scope.md).

The bus models actually advance through their movement, energy and temperature calculations. Their packets pass through the ordinary durable queue, broker and receiver. A start barrier waits for every model and browser view, then releases the models against one future Linux clock value. Container launch order cannot shorten the fleet test. The runner records actual timestamps, scheduling lag and elapsed time; its planned clock never replaces an observed measurement.

The short `rehearsal` profile uses all ten sources with 60 seconds of warmup and 30 measured seconds. It tests simultaneous connections and collectors. Its name and report fields prevent it from passing W0 or W1.

## Run from Windows

Use PowerShell in `<checkout>`. Keep the existing Windows-native agent and Ubuntu launcher configuration.

1. Close unneeded applications yourself if the laptop is busy. The runner does not close applications or change their settings.
2. When a later bounded run is explicitly resumed and host memory is sufficient, run W0 in the qualified visible Edge browser:

   ```powershell
   python scripts/monitoring/qualify_sustained_measurement.py --workload W0 --browser windows-edge-visible --evidence-dir evidence/p1/CHOOSE-A-NEW-ATTEMPT-NAME
   ```

3. Then use `--workload W1` with a new evidence directory. Existing applicable concurrency evidence can be reused; another rehearsal is not mandatory. `--browser chromium-automated` selects the separately qualified automated Chromium browser. Keep browser attempts separate; do not combine partial results from different runs. Earlier attempts remain failed under their original policies. This guide does not authorize automatic retries; each bounded session follows its stated retry/stopping limit.
4. Read the attempt's `qualification.json`, `preflight.json` and, when collection completed, `measurement.json`. `passed: false` is a failed attempt, even if some components passed. Preserve its evidence before correcting and retrying.

`--workload` selects a frozen profile, `--browser` selects an existing qualified browser configuration, and `--evidence-dir` selects a new private output directory. Existing output directories are refused to protect earlier evidence. Heavy wall-time runs are deliberately not accelerated.

## The memory preflight

The preflight first holds a lightweight Ubuntu process active. It samples Windows available physical memory, paging and the separate Ubuntu process working set for about 35 seconds. This avoids a misleading sample taken while Ubuntu is inactive. A continuous low-memory interval includes conservative allowances for the one-second sampling boundaries. Under ADR-0038, the former 2 GiB condition is a recorded warning in local Phase 1. The versioned physical/commit emergency condition still prevents startup or cancels an owned test. Full host capacity remains deferred to Phase 6. An unreadable counter or interrupted collector fails the preflight and records an unavailable measurement.

More memory assigned to Ubuntu is useful only if Ubuntu lacks memory and Windows has enough to spare. It cannot create physical RAM. Keep the Windows headroom result separate from Linux/container memory and from functional correctness. See the [measurement guide](observation-measurement.md).

## How the evidence is joined

Every actual observation has an event identity. Source-ready, queue, publish attempt, broker acknowledgment, receiver, transaction and displayed-row records are joined by that identity. The active `fleet-operator-table/1.0.0` harness uses one page and one browser context, with every workload bus selected and isolated by its event/asset identity. It measures one fleet operator, not ten independent users. Credentials stay in memory/headers and browser storage stays empty. Earlier attempts retain their original policies. The browser verifies every displayed value and unit against the original packets, then obtains the post-display clock witness described in ADR-0020.

Large reports are processed one asset at a time. Trace files are checked record by record for truncation, sequence gaps, clock changes and extra identities. Per-asset browser files avoid copying ten full packet/report arrays into one coordination file. All measured latency values are combined before computing fleet percentiles. A percentile is an ordered cutoff; the fleet p95 cannot be calculated by averaging ten separate p95 values.

Resources must cover the full active window, including every source and sender. The report distinguishes complete instrumentation from actual latency/resource acceptance. Missing DOM events, late samples, clock failures, resource gaps, out-of-memory events or failed local emergency guards keep the attempt unpassed. Capacity warnings remain visible and unqualified.

The fault-free full-profile collector uses `frozen-cgroup/1.0.0`. A cgroup is Linux's kernel accounting boundary for a process group, including a container. The collector fixes the owned container identities and accounting locations before source release, then reads fresh memory, CPU, event and whole-Linux paging counters every second. It caches locations, never measured quantities. Missing groups are recorded as absent; active-window coverage must still pass. A final independent inventory rejects changed/restarted containers, OOM kills, abnormal source exits or stopped persistent services. Slow Docker metadata calls remain outside the timed sampling loop. Dynamic paired fault-oriented rehearsals retain discovery. See [ADR-0025](../adr/0025-low-overhead-measurement-collection.md).

Measurement diagnostics use `action-har/1.0.0`: action/assertion traces without repeated document snapshots, screencast images or bundled source copies, plus an explicit content-omitted HTTP Archive for every operator context. HAR means HTTP Archive; it records web request/response metadata and timing. Headers can contain credentials, so these archives remain ignored/private. Actual event values, clocks, stage traces, one-second resource samples and end/failure screenshots remain required. Ordinary functional/visual tests retain their separate full diagnostics.

## Temporary sleep prevention for unattended work

`scripts/windows_awake.py` requests that Windows keep the system working while a bounded helper process is active. The display can turn off. It does not edit the saved power plan and does not override the user closing the lid or choosing Sleep. The implementation follows [Microsoft's execution-state API](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate).

Run the holder in a separate agent-managed process:

```powershell
python scripts/windows_awake.py hold .runtime/CHOOSE-A-NEW-SLEEP-HELPER --lease-seconds 900 --max-seconds 43200
```

The lease is a renewable time limit. Renew it from another process at least every 15 minutes during active work:

```powershell
python scripts/windows_awake.py renew .runtime/CHOOSE-A-NEW-SLEEP-HELPER
```

Release the request when the workload ends or pauses.

```powershell
python scripts/windows_awake.py stop .runtime/CHOOSE-A-NEW-SLEEP-HELPER
```

Check that the helper exits and its private `state.json` says `released`. Renewal also checks the live Windows process and its original creation timestamp, so a stale state file or a reused process number cannot falsely renew a dead helper. Use the normal authorized Windows execution context for this process check; a denied process-handle read is an observation failure, not proof that the holder stopped. If renewal stops, the lease expires; the 12-hour maximum is another safety bound. The request also ends with the holding thread/process. Normal Windows idle-sleep behavior then applies because the power plan was never changed. Keep the laptop plugged in, the lid open, and networking available during unattended work.

Source map: `workload_profile.py` selects counts/durations; `measurement_preflight.py` checks host readiness; the existing extension fixture owns services/secrets/cleanup; `evdt.workload.pacing` coordinates the common source clock; `measurement.spec.mjs` verifies displayed rows; `full_measurement_report.py` validates the complete evidence. Raw diagnostics, originals, tokens and screenshots stay ignored. No publishing or scheduled task is required by these scripts.
