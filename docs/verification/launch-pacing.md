# Launcher trip pacing

The launcher accepts six time scales: 0.5×, 1×, 2×, 5×, 10×, and 20×. The default is 1×. Trip selection, service selection, and pacing become fixed when a session starts. Working duration is unchanged; real-life driving estimates divide that duration by the selected scale. Saving and restoring a session preserves its scale. Earlier recordings default to 1×.

## Verification

Deterministic clock checks cover all six rates, one-working-second sensor ticks, elapsed-time conversion, pauses, and a host falling behind. Invalid values, Boolean values, synchronized workload overrides, and unpaced/scaled combinations are rejected. Forecast lifecycle tests retain a single worker, newest-input scheduling, real-life deadlines, and working-time freshness rejection at high speed.

Browser checks cover all ten trip cards at all six speeds, selected-trip and mobile estimates, the default, start payload, disabled controls during a run, and restoring an active session. All mutation requests in that check are intercepted; it starts no vehicle.

Connected verification uses `python scripts/geographic/qualify_pacing.py`. It runs six sequential one-bus previews on the retained Lachine–Sainte-Anne-de-Bellevue route, with Energy Forecast and replay enabled. Each source produces 80–1,800 working seconds, targeting 90 real-life driving seconds at 1× and faster. The 0.5× preview allows 160 real-life driving seconds so it can observe a second forecast with the 60-working-second cadence. Startup, host admission, and cleanup are additional. The procedure stops after a failure or blocked admission and never retries automatically.

Each attempt checks exact observation identities and wire fingerprints, every signal's stored history, continuous working-time cadence, at least two current forecast generations, worker failures, recording backup, and cleanup. The first sixty physical observation frames must match across all rates. The result records achieved pacing separately from requested pacing. This is a bounded integration check; it does not replace complete-trip model evaluation or fleet endurance.

## Recorded results

Source revision `f9d0007` passed the shared engineering checks (`bad65c100a0044689a3c0214208a89c6`), focused pacing/lifecycle tests, browser checks, and staged privacy and secret scans.

The first connected attempt, `live-journey-20261113T203247Z-499349`, stopped during the 1× check when Windows available memory remained below 2 GiB for more than 30 real-life seconds. The recorded minimum was 1.37 GiB. It produced 19 observation packets and two completed forecast jobs without a worker failure. One current forecast generation was observed. The attempt remains failed/incomplete and does not qualify a pacing rate. The remaining five rates did not run.

The partial database backup, owned-resource cleanup, and temporary sleep-request release passed. An independent Docker check found no remaining containers. The batch result is retained as `pacing-20261113T203246Z-a339e2`.

The external batch `pacing-20261113T203845Z-f12561` produced these results:

| Rate | Working seconds | Real-life driving seconds | Stored readings | Result |
|---|---:|---:|---:|---|
| 1× | 90 | 90.10 | 1,800 | Passed; nine current forecast generations observed |
| 0.5× | 60 | 120.09 | 1,200 | Passed; six current forecast generations observed |
| 2× | 180 | 90.08 | 3,600 | Passed; three current forecast generations observed |
| 5× | 450 | 90.09 | 9,000 | Failed forecast availability/startup checks |
| 10× and 20× | — | — | — | Not attempted after the 5× failure |

The 5× attempt completed four forecast jobs without worker failures. Exact observation accounting, all twenty signal histories, backup, and cleanup passed. The Windows resource guard reported no failure. The input endpoint required a scalar packet at the newest source-clock step; that packet was usually still in transit. Seventy-two sampled forecast responses reported `current_bound_observation_not_ready`. Only one current generation was observed, and the separate startup poll missed it. The attempt remains failed/incomplete. An independent inspection found no remaining containers, networks, or volumes for its project. All four attempts have the same 441-file source fingerprint, reconciled against the retained engineering-check inventory for revision `c98eda6`.

The correction adds authenticated current-plan proof and selects the newest complete, fresh packet within one forecast period of the actual clock. Startup uses the same freshness-checked forecast response as the pacing recorder. The [input contract](../design/energy-forecast-inputs.md) defines the unchanged identity, route, charging, and age checks. Fifty focused tests and 91 subtests passed. The disposable PostgreSQL check `input-delivery-e42c45342e45` passed nine cases covering delivery lag, catch-up, context binding, changed plans, charging, pause, stale inputs, and missing head context. It used the Monitoring API's read role and production migrations, started no bus, and verified cleanup. Two earlier database-fixture setup failures remain recorded; neither started a bus.

The database check is reproducible with `python scripts/linux.py -- .runtime/energy-v2/venv/bin/python qualification/energy/input_delivery_postgres.py`.

The corrected source passed the shared engineering checks in `089e49a909d24516932cc60458daf99c`, including unit, lint, strict TypeScript, contract, privacy, and secret checks.

## Corrected connected results

Batch `pacing-20261113T212600Z-4b5a46` passed all six bounded checks on source revision `e72b9cd`. Review reconciled each result with its source report, startup check, controller result, forecast samples, backup, and current source fingerprint. No additional bus run was started during review.

| Selected rate | Working seconds | Real-life driving seconds | Achieved rate | Stored readings | Current forecast generations observed | Result |
|---|---:|---:|---:|---:|---:|---|
| 0.5× | 60 | 120.07 | 0.500× | 1,200 | 6 | Passed |
| 1× | 90 | 90.08 | 0.999× | 1,800 | 9 | Passed |
| 2× | 180 | 90.08 | 1.998× | 3,600 | 12 | Passed |
| 5× | 450 | 90.10 | 4.994× | 9,000 | 20 | Passed |
| 10× | 900 | 90.11 | 9.988× | 18,000 | 18 | Passed |
| 20× | 1,800 | 117.55 | 15.313× | 36,000 | 7 | Passed integrity and forecast checks; host ran below the requested pace |

All 69,600 readings retained their exact event identities and wire fingerprints, twenty-signal coverage, and one-working-second cadence. The first sixty physical observation frames matched across rates. All 123 completed forecast jobs reported no worker failures. Every sampled current forecast met the unchanged 60-working-second and 90-real-life-second age limits. All six runtime host guards, database backups, and temporary sleep-request releases passed. Independent Docker inspection found no remaining containers, networks, or volumes for any of the six projects.

Higher pacing reduced current-forecast availability. At 10×, 27 of 80 polled views contained a current forecast; at 20×, 7 of 102 did. These samples include startup and finish and are not a steady-state availability measure. Unavailable views retained their reasons, including current-context delivery, changed plans, and exceeded working-time age. Historical results remained recorded. The accepted scope covers data preservation, bounded scheduling, and honest freshness behavior; it does not guarantee continuous current guidance or achievement of the selected rate on every host. Use 1×–5× for close observation of forecast updates.

The speed selector and corrected input path are accepted for this bounded one-bus integration scope. Complete-trip accuracy retains the geographic study's scope; ten-bus endurance remains deferred. Earlier failures and partial results retain their original outcomes.
