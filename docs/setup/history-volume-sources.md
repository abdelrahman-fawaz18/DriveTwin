# Generating actual source data for the history fixture

W2 is the history-volume target: ten assets, 7,200 observation events per asset and 20 scalar readings per event. A scalar is one numeric measurement, such as battery temperature. The total is 72,000 events / 1,440,000 scalar rows. Generating data and storing that complete volume are separate checks. This guide explains the one-bus source check. The coordinated complete W2 database and its separate query/export/restore checks subsequently passed; they have their own records below.

## Why a separate route

The source advances SUMO, the road-traffic simulator, in 0.2-second steps. The dependent plant calculates the associated battery, drivetrain and thermal values. One observation bundles the 20 sampled values every simulation second. Acceleration here means executing those simulation steps faster than wall time; it does not skip them or repeat earlier packets.

`simulation/montreal-history` is a separately named hypothetical ten-lap route over the already reviewed Montreal network. It derives from the original closed loop, sharing its boundary edge without inserting a reset at a lap boundary. Original scenarios remain unchanged. Its 12,000-second simulation deadline leaves room for the required 7,200 samples. The source stops this fixture at that prefix; it does not claim to finish all ten laps or represent an STM route.

The frozen `history-volume/1.0.0` protocol is in `benchmarks/history.json`. It retains the existing `3.2.0` observation schema, `1.4.0` catalog and `source-run/2.2.0` recipe. The catalog is the measurement dictionary; the recipe records the exact source inputs and execution settings. The fixture selects this fixed 20-signal cohort so results remain comparable. This test cohort does not impose a production limit on adding sensors.

## What the fixture writes

The history source invokes the existing `SumoVehicleSource` with `paced: false`. It writes each actual frame as an explicitly labeled history-fixture packet, using the existing contract validator. It has no broker connection and no persistence-receipt claim. It does not alter the production adapter or outbox limits.

`originals.jsonl` uses JSON Lines: one complete record per line, so packet processing does not require loading the complete packet history at once. Each record contains the event identity, SHA-256 fingerprint and Base64-encoded original packet bytes. Base64 is a text encoding of bytes; decoding restores the exact original bytes and formatting. The original packet contains the actual sample time, values, units, quality, source/session identity and recipe fingerprint.

The source also saves its recipe, SUMO network/route artifacts, physical-step trace and summary. An independent audit reads those artifacts and checks every packet value against the corresponding stepped plant value. It checks identities, original byte fingerprints, stream fingerprint, sequence, one-second cadence, complete signal coverage, physical-step continuity, counts and nondecreasing traveled distance. Recomputing an outer packet hash after altering its value still fails the comparison with the physical trace. These integrity checks are not real-bus calibration.

This cohort uses the baseline sensor configuration. A future deliberately noisy or missing-sensor dataset needs a separately recorded expected sensor transformation; this direct physical-value audit must not be weakened to accept unexplained differences. The production sensor configuration hooks remain available.

All outputs remain ignored under `evidence/`. The qualifier owns one uniquely labeled temporary container, bounds it to 512 MiB and two CPUs, checks its exit/OOM state and removes only that owned container. OOM means out of memory: the program exceeded its memory allocation. A partial or failed attempt remains unpassed and its files remain available for inspection. A separate free-disk preflight preserves the existing host reserve plus four GiB of temporary headroom.

## Reproduce the source qualification

Run the history fixture commands from `<checkout>` in Windows PowerShell.

1. Check the implementation and protocol:

   ```powershell
   python scripts/dev.py check
   ```

2. For a small pilot, choose a new output directory:

   ```powershell
   python scripts/linux.py --timeout 2700 -- python3 scripts/monitoring/qualify_history_source.py --samples 600 --evidence-dir evidence/p1/CHOOSE-A-NEW-PILOT
   ```

3. For one complete two-hour simulated prefix:

   ```powershell
   python scripts/linux.py --timeout 2700 -- python3 scripts/monitoring/qualify_history_source.py --samples 7200 --evidence-dir evidence/p1/CHOOSE-A-NEW-FULL-PREFIX
   ```

   `--samples` selects simulation observations; it does not select a wall-time benchmark duration. `--asset bus-002` selects another identity from the frozen ten-asset cohort. The launcher timeout is an upper execution bound, not a declared measurement window. The qualifier builds the pinned simulator image, records exact source/image fingerprints, runs the source without external networking, audits originals and cleans up.
4. Read `qualification.json`. Require the top-level pass, passing source and independent audit, all 7,200 events / 144,000 readings, no OOM, zero exit status, empty owned-container cleanup and matching source fingerprints. Inspect the nested source summary for actual simulation interval, memory peak and elapsed wall time. Earlier reports remain separate; never merge partial prefixes into a passing source execution.

## Measured scope and next work

The final audited one-bus qualification produced observations from simulation seconds 1 through 7,200, with 35,999 physical intervals after SUMO's initial insertion step. Every original sample matched the physical trace, and traveled distance never reset. Peak container memory was 288,665,600 bytes, about 275 MiB; elapsed wall time was about 59.4 seconds while separate code checks also ran. This is a source-component result, not a live workload or query-latency result. See [the source record](../verification/history-source.json).

The coordinated ten-source database and sealed checkpoint subsequently passed with 72,000 original events / 1,440,000 scalar rows, complete independent source/database checks and unchanged full inventories across retries. See the [volume record](../verification/history-volume.json) and [loading/checkpoint guide](history-database-checkpoint.md). Frozen cold/warm five-signal queries, actual multipage browser JSON/CSV exports and timed isolated W2 restore subsequently passed separately; see the [acceptance record](../verification/history-acceptance.json). Identical source parameters must be labeled identical; independent executions do not establish fleet diversity or calibration. Keep all earlier failed/incomplete attempts.

Phase 1 live W0/W1 still use exactly 60 warmup seconds plus 1,800 measured wall seconds. Two simulated hours of history and a one-hour historical query do not launch a one-hour live benchmark. See [ADR-0024](../adr/0024-bounded-history-and-volume-qualification.md).
