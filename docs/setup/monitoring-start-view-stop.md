# Monitoring qualification runner

This guide documents the short-run monitoring qualification and its automated cleanup. For routine viewing of live readings, map, history, and replay, use the [local demonstration](../release/demo.md).

The original short-run monitoring milestone passed under `phase1-local-monitoring/1.3.1`. The later one-bus 30-minute check and final acceptance are recorded in the [release evidence](../release/verification.md). The external ten-bus short run and the later deferred endurance attempts have separate results. [Short-run closeout](../verification/monitoring-acceptance.md) · [External result](../verification/external-w1-review.json)

## Start from Windows

1. From PowerShell in the repository root, set the module search path for the current session.

   ```powershell
   $env:PYTHONPATH = Join-Path (Get-Location) "src"
   ```

2. Once host memory is sufficient, start a fresh demonstration with the existing controller:

   ```powershell
   python scripts/monitoring/qualify_extension.py --evidence-dir evidence/p1/YOUR-NEW-DEMO-NAME
   ```

   Replace `YOUR-NEW-DEMO-NAME` with a name that does not already exist. The controller generates private credentials, builds the locked application, and starts two simulated buses and their ordinary monitoring services. The Compose project owns the session containers and storage. Optional bounded-storage bindings remain unset in this normal configuration.

3. Wait for `Starting the owned live-source dashboard fixture`, followed by the browser-testing message. Setup can take several minutes if an image must be built. A failure message or `passed: false` means the session did not qualify; inspect its private report before retrying.

## View

The controller opens visible Microsoft Edge and supplies that session's private credential automatically. It also checks pinned Chromium separately. Edge displays the local dashboard at `http://127.0.0.1:18765`; `127.0.0.1` means this computer. The replay page uses port 18766.

Watch **Measurements** for the 20 original readings and the second bus's additional coolant reading. **Map** shows observed positions and the planned route. **History** queries stored values and supports JSON/CSV downloads. **System** shows delivery and queue diagnostics. **Fleet** keeps selected buses separate. Replay reads a frozen recording using its own credential and service; it cannot issue vehicle commands.

The automated session controls the page while checking these views, so do not type or click during its checks. Credentials remain in private runtime files and request headers, never in URLs or Git. If opening an additional viewing tab, use the credential from that session's private control file; the file contains secrets and must not be pasted into chat, screenshots, or documentation. The automated Edge view avoids needing to copy it.

## Stop and check cleanup

1. Prefer normal completion. The controller stops its temporary services automatically. It prints the result and evidence directory. A passing session requires `passed: true` in the top-level and service `qualification.json`, plus empty `containers` and `volumes` entries in the service cleanup result.
2. To interrupt, press **Ctrl+C once in the PowerShell window that started the controller**, then let its `finally` cleanup finish. Cleanup can take several minutes while a bounded worker exits. Do not close the terminal immediately, terminate Ubuntu, or run a Docker-wide cleanup command. An interrupted session is incomplete and cannot be used as passing evidence.
3. Inspect the owned cleanup result. If it is missing or reports resources, preserve the evidence and inspect the recorded Compose project before removing anything. Never delete containers or volumes using a broad name match.

To inspect the current container inventory without deleting anything:

```powershell
python scripts/linux.py -- docker ps -a --format '{{.Names}} {{.Status}}'
```

`--` passes the following command through the project launcher to Ubuntu. `docker ps -a` lists running and stopped containers. A shared computer may have unrelated containers; their presence alone is not a cleanup failure.

## Revised short-run acceptance

These separate commands measure the declared monitoring workloads. They are not necessary merely to read existing results and must not be retried automatically after a known blocker.

```powershell
python scripts/monitoring/qualify_sustained_measurement.py --workload W0 --browser chromium-automated --evidence-dir evidence/p1/YOUR-NEW-W0-NAME
python scripts/monitoring/qualify_sustained_measurement.py --workload W1 --browser chromium-automated --evidence-dir evidence/p1/YOUR-NEW-W1-NAME
```

Run them sequentially, using a new name for each attempt. W0 is one bus, 60 warmup + 120 measured seconds. W1 is ten simultaneous buses, 60 warmup + 300 measured seconds. Each bus sends 20 signals once per second. The script retains its historical filename for compatibility; policy `phase1-local-monitoring/1.3.1` and report fields explicitly exclude sustained-load qualification. Use `--browser windows-edge-visible` for the separately qualified visible-browser configuration.

Read `preflight.json` first, then `qualification.json` and, if measurement completed, `measurement.json`. A failed precheck means no workload started. Warmup, exact event accounting, source hashes, access controls, every displayed reading, latency, application-memory and Linux swap requirements, and cleanup remain unchanged. The local host-warning/stop policy is explicit below. Former 30-minute runs and endurance qualification belong to Phase 6.

## Read local memory warnings and emergency stops

Physical RAM is the laptop's immediate working memory. Commit headroom is how much more memory Windows can promise to back with RAM and paging files; it is measured separately. The new collector records both, plus Windows paging activity, browser/WSL usage and all existing Linux/container measurements.

The 2 GiB available-RAM / 30-second condition remains visible as a capacity warning. It does not establish failure of a local functional milestone, and a local pass never establishes full host-capacity or production readiness. The original complete Q-12 capacity claim remains unpassed until Phase 6 qualification.

`benchmarks/monitoring-host.json` fixes the provisional local emergency guard: available RAM below 512 MiB or commit headroom below 1 GiB for a conservative 10-second interval aborts the temporary test. Missing/invalid counters or sample gaps also stop it. These are explicit local operational guardrails, not certified safe Windows minimums. The conservative calculation includes uncertainty between one-second readings. A brief low sample can recover; sustained or uncertain critical pressure cannot be silently removed from the result.

On an emergency, the controller requests closure of its fresh automated browser runner and cancels its identified Linux worker. If that temporary browser tree does not close normally, only that owned tree can be terminated. Existing exact-project cleanup stops the fixture services, preserves available traces/source outputs as incomplete, and removes its temporary containers/volumes. It does not restart Ubuntu, change user apps, or run Docker-wide deletion.

In a completed report, inspect `local_host.passed`, `capacity_warning`, minimum physical/commit capacity, and `host_capacity_qualified`. A local monitoring resource pass can coexist with a capacity warning and `host_capacity_qualified: false`. A previous overall failed report is never rewritten: only explicitly compatible functional components may be referenced by a new acceptance assessment.
