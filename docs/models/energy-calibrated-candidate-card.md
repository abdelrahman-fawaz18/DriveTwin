# Synthetic calibrated energy candidate

Bundle: `synthetic-energy-candidate/1.1.0`. Release: `energy-validation-release/1.0.0`. Historical status: frozen held-out criteria passed within the synthetic known-route scope. Generic worker `model_qualified=false` labels retain their original meaning; product explanations and service/display work subsequently closed under [P2-E1](../verification/energy-advisory-acceptance.md). Current geographic forecasting uses the separate [route-aware model](route-aware-energy.md).

## Purpose and supported study

Estimate remaining net energy and final charge from a 120-second observation prefix on the known hypothetical Montreal core route. The forward plan is a historical training motion template, anchored at current speed/odometer and re-timed to the training-derived remaining distance. Hold current observed equipment demand constant. This is a research candidate, not live route/traffic forecasting, automatic dispatch or a real-bus specification.

The study design reserves 24 complete missions across nominal, high load, stop-heavy, cold/high equipment demand, capacity mismatch and low-charge/high-equipment regimes. Twelve training and six validation missions were actually collected; the six reserved held-out outcomes have now been generated and scored once. One training realization was repeated exactly. All cases use one independently run bus on the same known route, with synthetic driver variation, measurement noise/quantization/bias and assumed vehicle/thermal inputs. There is no interacting traffic population, battery aging/chemistry or measured real-vehicle operating envelope.

## Fit, assumptions and provenance

The 36-pair bounded grid fits rolling resistance and propulsion efficiency to equal whole-training-trip net-energy errors. The selected effective values are **0.010 / 0.90**; training MSE is about 0.1307 kWh squared. Leaving out individual trips sometimes selects 0.008 / 0.86, showing a tradeoff. Neither result identifies the actual tire/drivetrain constants. Geometry, model discrepancies and fixed mass/loss assumptions can be absorbed into these effective numbers.

Capacity remains independently declared at 350 kWh and loaded mass at 18,000 kg while actual recipes vary. The fit uses noisy observations and observed equipment power; it does not recover private capacity from energy/charge ratios or read actual capacity/fault/seed/future-motion truth into the predictor. The exact source, collection inventory, frozen template and artifact digests are bound by the [release manifest](energy-validation-release.json). Raw inputs and model/template files remain private; a small verified same-disk checkpoint preserves the three critical frozen artifacts.

The existing `observed-plan-advisory/1.0.0` calculation uses `longitudinal-energy-estimate/1.1.0`. No worker wire format or numerical equation changes. The separate sensitivity method is `training-joint-grid-envelope/1.0.0`. Existing worker point records intentionally retain their unqualified/not-established uncertainty labels; the offline envelope is a separate artifact. P2-D1 subsequently composed explicit calibrated-release and uncertainty lineage into persistent, API and UI records, with final explanations and compatibility covered by the advisory closeout. The original worker flags were not changed.

## Validation results, not final accuracy

One independent forecast per complete validation mission; endpoint is the last sampled physical state before exit. All six candidate and six reference forecasts were available and reproduced exactly in the restricted worker.

| Metric | Validation result |
|---|---:|
| Candidate energy mean absolute error | 0.2791 kWh |
| Reference energy mean absolute error | 2.4716 kWh |
| Maximum candidate absolute energy error | 0.6259 kWh |
| Final charge mean / maximum absolute error | 0.3569 / 0.6667 percentage points |
| Empirical joint energy/charge envelope coverage | 6 of 6 whole missions |
| Maximum energy / charge envelope width | 5.4186 kWh / 3.7428 points |
| Maximum relative fixed-plan energy refinement change | 0.00001627 |

These validation results were used to select the uncertainty method. They remain separate from the held-out report below and do not establish real-bus accuracy. The independent parameter box failed its width limits and remains rejected; point forecasts and every error/coverage/width target stayed unchanged when selecting joint training-compatible pairs.

| Validation regime | Independent trips | Energy error (kWh) | Charge error (points) | Joint covered |
|---|---:|---:|---:|---|
| nominal | 1 | +0.242 | +0.022 | Yes |
| high load | 1 | -0.626 | +0.451 | Yes |
| stop heavy | 1 | -0.195 | +0.238 | Yes |
| cold auxiliary | 1 | +0.244 | +0.198 | Yes |
| capacity mismatch | 1 | +0.262 | +0.667 | Yes |
| low charge auxiliary | 1 | +0.106 | +0.566 | Yes |

Signed error means prediction minus physical outcome. Negative energy error underestimates required energy; positive final-charge error overestimates charge remaining. The high-load case exposes that risk while mass stays independently assumed. One case per regime is a deliberately small validation study, with zero unavailable forecasts here.

## Independent held-out result

The unchanged frozen release passed all criteria on six complete reserved realizations. No fitting, template/uncertainty selection or threshold change used these outcomes. All twelve candidate/reference worker outputs were sealed before scoring; no forecasts were unavailable.

| Held-out metric | Candidate | Recent-consumption reference |
|---|---:|---:|
| Energy mean absolute error | 0.2850 kWh | 2.3840 kWh |
| Final charge mean absolute error | 0.1790 points | 0.6313 points |
| Maximum absolute energy error | 0.6300 kWh | 3.0176 kWh |
| Maximum absolute charge error | 0.2523 points | 1.0169 points |

All six joint outcomes are covered by the selected empirical scenarios. Maximum widths are 5.4201 kWh / 3.7436 charge points, within the unchanged caps. Fixed-plan refinement also passed. There is one case in each deliberately selected regime; this is not a population coverage guarantee, unseen-route qualification or physical calibration. The endpoint remains the last sampled state of a complete mission.

See [C1 per-regime results](../verification/energy-heldout.json), [sealed evaluation references](energy-held-out-release.json) and [inspection/reproduction guide](../setup/held-out-energy-evaluation.md). The local one-time attempt is consumed and must not be regenerated to select a better outcome.

## Uncertainty and acceptance

Retain joint parameter pairs close to the best training objective and every leave-one-trip-out selection. Vary declared mass/capacity, recovery, equipment and charge assumptions, plus an equipment/stop-time allowance. Sampled scenario extrema are not a rigorous enclosure or probability guarantee. Six deliberately varied cases cannot establish population interval coverage.

D-18 is frozen only for this known-route synthetic advisory study: 1/2-kWh mean/maximum energy error, 0.5/1-point mean/maximum charge error, no worse energy MAE than the reference, at least five of six joint covered cases, width <=6 kWh / 4 points, all forecasts available and fixed-plan refinement <=1%. The [decision](../adr/0042-energy-calibration-and-validation-freeze.md) explains the engineering rationale. Thermal and automatic-dispatch evaluations have separate [thermal](../verification/thermal-acceptance.md) and [charging](../verification/stationary-charging-acceptance.md) evidence. Physical validation is outside the release.

## Release and rollback

The six reserved complete missions passed the frozen criteria in P2-C1. Persistent records, scoped input access, dashboard explanations, station assessments, unavailable states, and restoration passed the final advisory review. The calibrated candidate remains scoped to its known-route simulated study. [Advisory closeout](../verification/energy-advisory-acceptance.md)

Candidate bundles retain their versioned parameters, protocol, and evaluation fingerprints. A rollback selects a previously verified bundle. Observations and prior outcomes retain their original identities. A retuned candidate requires an independent evaluation decision and new evidence.

## Product integration

Saved forecast records include the candidate, reference, input observations, and empirical scenario bounds. The Energy page suppresses current values when the source or required inputs are stale, missing, mismatched, or outside the supported cutoff. The route forecast profile evaluates the known simulated route at 120 simulation seconds. Station approach forecasting and charging authority use their separate qualified configuration. [Product guide](../setup/persistent-energy-forecasts.md) · [Charging closeout](../verification/stationary-charging-acceptance.md)
