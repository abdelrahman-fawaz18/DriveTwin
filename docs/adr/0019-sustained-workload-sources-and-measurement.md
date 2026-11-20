# ADR-0019: Sustained workload sources and measurement preparation

Status: selected for P1-E2; Phase 1 wall duration amended by [ADR-0022](0022-monitoring-duration.md). Each checkpoint must publish its own measured scope.

## E2 checkpoints

- E2-A: extended continuously stepped mission, explicit ten-asset format/catalog, trusted policies/maps/credentials, two repeatable long simulated prefixes, short paced source/read/browser rehearsal, and unit-tested measurement rules. This prepares sources; it does not pass full E2 or a benchmark duration.
- E2-B: actual source/enqueue/publish-attempt/receive/persistence/projection/browser collectors, authenticated clock exchanges, complete event/attempt correlation, host/container/swap sampling and short controlled rehearsals. Missing measurements must fail, not be removed.
- E2-C: full 60-second warmup plus W0's 1,800 measured wall seconds, then full warmup plus W1's 1,800 measured wall seconds at ten assets under the user-approved policy 1.1.0. Require every other frozen metric relevant to those runs; preserve failures. E3 owns W2-volume historical queries/restore. Do not merge a seeded history fixture into a live-source benchmark claim.

These are implementation/learning checkpoints inside E2. ADR-0022 records the user-approved duration revision; all other thresholds remain unchanged. The first source checkpoint must pass before downstream measurements and full-duration runs use it.

## Continuous source and format choices

The original Montreal route starts and ends on the same directed edge. A new independently versioned scenario concatenates five copies, omitting the duplicate shared boundary edge at each join. It retains the fixed 0.2-second scheduler, bus/plant parameters, reviewed network import and one initial scheduled stop. The vehicle keeps driving; distance, SUMO-owned energy and dependent thermal state never reset at lap boundaries. Route length comes from the actual imported network; no rounded base-length multiplication is treated as a measurement. Original scenarios remain unchanged. This is fictional development routing, not a real transit-service or traffic calibration claim. [SUMO routes](https://sumo.dlr.de/docs/Definition_of_Vehicles,_Vehicle_Types,_and_Routes.html), [battery ownership/units](https://sumo.dlr.de/docs/Models/Electric.html).

Catalog 1.4.0 explicitly admits bus-001 through bus-010, all with the same 20 initial signal definitions. Register envelope 3.2.0 and source-run 2.2.0 as one explicit new tuple; retain every earlier artifact/default/tuple. These metadata versions introduce asset scope, not new physics or permissive fallback. Ten sources are separate SUMO processes with independent identities, sessions, clocks, queues and state using the same assumed parameter set. They are not ten validated bus designs or interacting fleet traffic.

The D2 shared-code proof remains historical and immutable. E2's separate guard checks 52 unchanged files and five precisely enumerated new profile entries, plus one explicitly reviewed policy-parser change. It reconstructs the old profile from the recorded Git object, checks its baseline hash and verifies the exact old/new policy-parser hashes in `qualification/workload/core-changes.json`. Do not replace D2 hashes or disable guards. Full original monitoring/replay regression uses the ordinary compatible runtime suite, while new tests exercise the new tuple. No source physics, queue, UI, SQL migration or runtime dependency changes in E2-A.

The ten-source trusted startup policy exceeds the old 64 KiB generic message limit. Give only `validate_policy` a separate 1 MiB portable-JSON bound; keep duplicate-key, UTF-8, depth, numeric and ownership validation. Telemetry, source registration and clock message limits remain unchanged. Tests require a valid policy above 64 KiB to pass only this trusted-file path, reject it as a wire message, and reject oversized/duplicate-key policy files. There is no public policy-upload endpoint added. This is a bounded administrative configuration change, not a relaxed telemetry contract.

Current extension/recovery/outage/source commands select the exact reviewed baseline through `current_scope.py`, based on the explicit registered tuple. Unknown extra registry/core changes still fail. `extension_scope.py` retains the original strict D2 comparison without modification; it is a historical comparison, not a request to overwrite its hashes after an approved later change. Every new qualification records current source fingerprints and its actual selected guard result.

## Qualification and resource scope

Run 1,860 and 3,660 simulated-second/sample prefixes (including the planned 60-sample warmup), each twice. Require exact counts/20 signals, continuous multi-lap motion, battery/thermal evolution, per-step energy ledger, electrical/SOC/heading consistency and identical numeric trace hashes. These accelerated runs test mission length and numerical repeatability; they do not test real-time delivery.

The first ten-asset service rehearsal uses 30 paced events per asset in batches of two to keep preparation usable on the current host. Require separate containers and recipes, exact original packet bytes, all queues drained, all 20 current/history readings per asset, scoped reads/maps and generic existing desktop/mobile rendering in both qualified browsers. This deliberately does not claim ten simultaneous sources or W1 throughput. Full concurrency/duration/resource gates remain E2-B/C requirements.

## Measurement rules

An event identity links each original observation to every later stage. Use composite run/asset/source/event identities; delivery-attempt counts are separate from unique-event dispositions. Generated unique events must equal accepted, quarantined, dropped or pending, with disjoint disposition sets. A missing/extra browser event invalidates latency statistics. Batched scalar samples inherit the event's latency and do not count as independent transport measurements.

Use nearest-rank percentiles over the complete event set. A clock exchange brackets server receive/send timestamps between client send/receive timestamps, bounding server-minus-client offset. Keep both pre/post bounds; reject inconsistent intervals or uncertainty above 50 ms. Add uncertainty to reported percentile limits. Collectors must also watch intermediate clock changes before these helpers can qualify real runs. Never subtract unrelated monotonic origins. The pure rule library is not evidence that the actual collectors/browser timing are implemented or clocks synchronized.

Warmup and measured events have separate identity sets/counters; elapsed wall durations remain unchanged. The source prefix check does not itself define the future collector's exact measurement window. Browser DOM means the rendered element tree; final-state compatibility screenshots do not establish per-event availability or human-visible screen latency. Full live browser correlation, authenticated clock/resource collectors and full W0/W1 remain open after E2-A.
