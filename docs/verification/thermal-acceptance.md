# Drivetrain thermal prediction and cooling acceptance

Phase 5 is complete in its qualified scope. The physical evidence is winding-temperature prediction on one laboratory testbench. The bus advisory, pump effect, and charging conflicts were evaluated with synthetic observations and a simulated command boundary. [Thermal model card](../models/drivetrain-thermal-model-card.md) · [Reproduction guide](../setup/thermal-reproduction.md)

| Gate | Status | Evidence |
|---|---|---|
| P5-01. Numerical thermal behavior | Pass | Eight numerical tests cover units, heating and cooling, steady state, isolation, heat exchange, and convergence as steps get shorter. Incremental and batch updates agree. [Thermal tests](../../tests/thermal/test_thermal_network.py) · [Bench development](bench-thermal-development.json) |
| P5-02. Prediction accuracy and uncertainty | Pass | Thirteen reserved bench profiles passed the predeclared error and interval limits. The 60-second synthetic forecast separately passed its frozen error and coverage limits before predictive control ran. High-current bias, broad bench interval, and the two prediction domains are recorded. [Bench results](bench-thermal.json) · [Forecast gate](thermal-intervention.json) |
| P5-03. Fault detection and ambiguity | Pass | Zero alerts in 8.27 healthy hours, zero misses in eight eligible cooling cases, and 12 of 12 qualifying crossings warned in advance. Minimum lead was 78 seconds. Ambiguous cases and no-data outcomes remain explicit. [Advisory results](synthetic-advisory.json) |
| P5-04. Measured intervention and tradeoffs | Pass | Each controller met the 1 K isolated cooling target in all eight eligible cases. The matched comparison reports heat exposure, pump and total energy, delay, and all 72 completed mission runs. [Intervention results](thermal-intervention.json) |
| P5-05. Bad sensor evidence | Pass | Abrupt bias was suspect, slow bias and a missing corroborating probe could be ambiguous, and unavailable motor readings reset persistence. Neither controller granted cooling in any of the tested biased, missing, stale, single-probe, or healthy cases. [Advisory results](synthetic-advisory.json) · [Intervention results](thermal-intervention.json) |
| P5-06. Timing and authority | Pass | Positive, negative, and exact-boundary checks cover freshness, expiry before first effect, local protection, operator veto, charging conflicts, and grant journal reopen. The five independent RTAMT checks cover advisory persistence. [Timing comparison](synthetic-advisory-development.json) · [Coordination checks](thermal-intervention.json) |
| P5-07. State estimation | Pass | Compared input-only and measurement-corrected predictions with noise and parameter mismatch. Correction improved the synthetic coupled-state estimate. The disconnected case shows what the winding reading cannot infer. Bench release stays one-state and open loop after startup. [Observer study](bench-thermal-development.json) · [Decision](../adr/0065-qualify-offline-winding-prediction.md) |

The closeout rechecked all three frozen source and evidence chains against their original Git revisions, development reports, and consumed attempt records. It verified 13 bench profiles, 48 advisory scenarios, and the fingerprints of all 72 intervention traces. The final focused rerun passed 33 thermal tests and three charging admission regressions. Earlier unsuccessful development runs remain part of the local evidence. [Read-only evidence verifier](../../scripts/thermal/verify_thermal_closeout.py)

## Qualification still open

| Area | Open work |
|---|---|
| Phase 6 integrated service behavior | Exercise telemetry freshness, scoped command authority, charging and thermal interaction, and recovery with services and adapters connected. Use an explicitly scoped simulation profile and independent effect observations. |
| Phase 6 platform qualification | Apply the existing mandatory workload, data-integrity, access, restart, extension, and resource gates. Retain the Phase 1 memory failures, short-run results, and deferred sustained-load scope. |
| Later real asset applicability | Validate calibration and protection on a physical subsystem in the hardware phase. A real bus claim would also need independently measured vehicle signals, thermal parameters, pump behavior, and vehicle-specific protective limits. |

The integrated release review reuses compatible model evidence and adds scoped monitoring, fault, recovery, restart, and browser checks. [Release evidence](../release/verification.md)
