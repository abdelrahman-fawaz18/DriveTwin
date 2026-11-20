# Reference data and movement checks

P0-06 checks specifications, source records and an isolated movement scenario. It does not run the full nominal workload or two-hour soak. Those require the later product adapter/dashboard and their performance gates. The current Phase 1 nominal workload is 30 measured minutes plus 60 seconds of warmup under [ADR-0022](../adr/0022-monitoring-duration.md); the two-hour live soak remains a later phase.

Run the Windows commands below in PowerShell from `<checkout>`. Ubuntu commands use the existing qualified launcher; no global agent-setting change is needed.

## 1. Verify the local public map

```powershell
python scripts/fetch_map.py
```

Expected: the existing snapshot's content fingerprint matches its receipt and no network request occurs. The script refuses a changed snapshot. The artifact and attribution are in `data/public/montreal-core-v1/`; raw downloads/account metadata are ignored. See [data card](../data-sources.md).

If creating a separately reviewed new snapshot, the explicit acquisition option is `--refresh`, but an existing snapshot is preserved. Plan a new version/output first; do not delete the accepted input to obtain a silent update. The GET query has time/size bounds. `--downloaded-response` accepts a known cached response only from ignored `.runtime/`. Source receipts record actual endpoint/date/query/transformations. Public service failure is not evidence that local simulation failed.

## 2. Check the workload specification

```powershell
python scripts/benchmark_spec.py --output evidence/p0/NEW-workload-spec.json
```

Replace `NEW` with a new descriptive run name. The command refuses overwriting previous evidence. It counts messages separately from scalar readings, measures the encoded planning example, and checks that nominal encoded spool capacity is arithmetically possible. Expected: `passed: true` and `performance_targets_measured: false`. The planning payload is intentionally not a message accepted by the qualification-only receiver.

Read [workload/metric rules](../workloads-and-metrics.md): rate/duration/clock, nearest-rank percentiles, missing-data failure, clock uncertainty, accounting, storage/retention and memory/swap budgets. A correct packet-size calculation does not prove a queue survives a crash or a database meets latency targets.

## 3. Run source and metric-failure tests

```powershell
python scripts/dev.py check
```

This builds from public pins/locks and checks style, unit/foundation tests, strict TypeScript compilation and the 339 shared contract cases. New tests cover expected workload totals, invalid/missing metric inputs, clock-error admission, account-tag stripping, malformed map input and project path containment. The new jsonschema optional dependency set retains format checking; it is not a switch to permissive parsing. Logs/results stay under ignored `.runtime/dev-checks/`.

## 4. Qualify the Montreal mission

```powershell
python scripts/linux.py --timeout 1200 -- python3 scripts/platform/qualify_montreal.py --evidence-dir evidence/p0/NEW-montreal
```

The outer launcher allows 1,200 real seconds; the controller and simulator have their own bounded deadlines. Use a new output directory. The controller builds the already-pinned SUMO tools and starts an unprivileged, network-isolated, read-only container with only scenario/script/map inputs and a writable evidence directory. Memory is capped at 512 MiB and CPU capacity at two cores. It does not mount private account settings or start the backend.

Each run freshly imports the same OSM snapshot, verifies the reviewed warning message/count set (diagnostic order may vary), checks bus-class connections and starts SUMO through TraCI. It observes departure, route progress, a 10-second stop and arrival; rejects collision/relocation, time/odometer discontinuity and excessive position jumps; tests geographic round trips; then compares the two canonical numeric trajectories. The position check allows 2 m additional lane/junction geometry offset per step; it is not physical GPS accuracy.

Expected: `passed: True`, two completed runs, identical trajectory fingerprints, no owned container left and unchanged input fingerprints. Detailed results include actual projection/offset, messages and trajectories. Import warnings retain the limitations in [model assumptions](../model-assumptions.md); they are not dismissed as operationally safe.

The preparatory `--prepare` option generates a candidate mission from fixed anchors in a new evidence folder. It does not replace the committed mission. Review any changed map/route/stop and its assumptions before promoting it and rerunning qualification.

## 5. Dependency/service regression and rights review

If dependencies or the receiver change, rerun the affected actual [data-flow](dataflow-fixture.md), [SUMO](sumo-verification.md), [browser](browser-verification.md) and [contract](contract-verification.md) checks. Aggregate acceptance selects explicit current prerequisites and refuses changed fingerprints. Never edit an old report to make new source appear tested.

`scripts/collect_inventory.py` exports declared Python terms in the locked Linux environment; `--npm packages/contracts-ts` or `--npm qualification/browser` reads the corresponding exact npm lock. `--output` selects the inventory file. The [rights inventory](../rights/README.md) distinguishes declarations from binary distribution review. Raw diagnostic records and private files remain ignored; source/license notices and reviewed public data belong in Git.

## Pass boundary

P0-06 passes only when the reviewed data/rights record, explicit parameter assumptions, workload/metric policies, executable checks, two qualified scenario runs and affected regressions agree with the recorded source. Passing completes the engineering foundation and permits P1 monitoring implementation. It does not establish product readiness, model accuracy, operational route legality, authorized command behavior, hosted CI success or permission to publish.
