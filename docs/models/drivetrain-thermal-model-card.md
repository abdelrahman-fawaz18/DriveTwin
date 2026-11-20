# Drivetrain thermal model card

**Retention update:** original model artifacts and consumed evaluation identities remain, while completed historical raw recordings and superseded study input copies are retired under [ADR 0075](../adr/0075-minimal-historical-retention.md). Recorded numerical outcomes retain their original scopes; inspection requiring retired payloads is unavailable. See the [issue/fix guide](../setup/known-issues-and-fixes.md).


A thermal model accounts for heat entering, accumulating in, and leaving a component. For example, a sustained motor load adds heat to the winding, the coil of wire inside the motor. The winding temperature rises until enough heat leaves to balance it. The model represents heat capacity in joules per kelvin and heat transfer in watts per kelvin. A 1 K change is the same size as a 1 °C change.

## What each release supports

| Release | Inputs and output | Qualified use |
|---|---|---|
| Bench winding | Every 0.5 seconds, use measured ambient temperature, coolant temperature, shaft speed, and the sum of squares of two motor-axis currents. Use the measured winding temperature once at startup. Predict later winding temperatures. | Offline winding prediction on the Paderborn testbench with the recorded inputs. [Frozen release](../../qualification/thermal/bench-winding-release-1.0.0.json) |
| Synthetic bus advisory | Every simulated second, compare motor and optional housing readings with a healthy model prediction. Output a state and reason. A separate evaluator knows when a fault was injected. | Cooling-loss advisories, sensor suspicion, ambiguity, and data unavailability in the tested synthetic cases. [Frozen release](../../qualification/thermal/synthetic-advisory-release-1.0.0.json) |
| Synthetic bus intervention | With fresh, agreeing probes and local permission, apply a fixed virtual pump boost using either a temperature threshold or a 60-second forecast. The simulator independently records temperature, power, and progress. | One bounded boost per simulated mission and charging conflict checks at a single simulated command boundary. [Frozen release](../../qualification/thermal/synthetic-intervention-release-1.0.0.json) |

## Bench prediction and uncertainty

The dataset contains 69 recorded sessions split into 43 for fitting, 13 for model selection, and 13 reserved for final evaluation. Entire sessions stay together, including three groups of likely repeated driving patterns. The fitting process estimates effective heating and cooling rates for this testbench. The two-temperature candidate improved validation error by 0.11 K, below the 0.15 K selection margin, so the release uses one winding temperature. After startup it predicts from the inputs and its previous prediction. This is **open-loop prediction**. [Data audit](../setup/motor-data-audit.md) · [Signal and split contract](../setup/motor-profile-split.md) · [Model and calibration](../setup/bench-thermal-qualification.md)

Across the 13 reserved profiles, the mean absolute error averaged equally across profiles was **3.19 K** and the worst profile mean was **7.26 K**. The validation-derived **±17.70 K** band covered **99.99%** on a profile-average basis. That band is broad, and average high-current underprediction was **5.30 K**. Use the detailed regime and input-range results when judging whether this release applies to a new recording. The band describes observed testbench performance. A protection setpoint would require separate safety evidence. [Measured errors and limits](../verification/bench-thermal.json)

## Which temperatures can be inferred

**Observability** asks whether available measurements contain enough information to estimate a hidden state. In the synthetic two-temperature experiment, housing heat affects the measured winding through thermal coupling. A **Kalman filter** corrects the estimate using each noisy winding reading and the estimated uncertainty. We scored each next prediction before providing the next reading to the filter. With a deliberately mismatched model, the synthetic mean hidden-body error fell from **4.13 K** open loop to **0.24 K** with correction. Disconnecting heat exchange removed the body information from the winding measurement. The measured bench release uses one winding state, and the bus catalog provides an aggregate motor temperature and pack current. A qualified bus winding or housing estimator would require synchronized motor-specific measurements and validation against independent temperatures. [State-estimation decision](../adr/0065-qualify-offline-winding-prediction.md) · [Synthetic observer results](../verification/bench-thermal-development.json)

## Monitor states and tested faults

A **residual** is the measured temperature minus the healthy prediction. A motor residual at or above 3.5 K sustained for 20 seconds can raise an advisory. To report `cooling_consistent`, synchronized housing evidence must also persist for 20 seconds, reach a 2.5 K residual, and keep the motor residual within 3.8 K above it. A motor residual at or below 2 K for 30 seconds clears the active deviation. A jump of at least 3 K between eligible consecutive motor readings latches sensor suspicion for that session. A **latch** keeps the state until a session reset. [Frozen rule and timing tests](../setup/synthetic-thermal-fault-qualification.md)

| Condition | Meaning and result |
|---|---|
| Reference is warming up or no persistent deviation is present | `warming_up` during the 240-second startup window or `normal` after it with sufficient data. |
| Both probes corroborate persistent extra heat | `cooling_consistent` indicates a pattern consistent with cooling degradation. Confirmation of a physical failure needs independent evidence. |
| A persistent deviation has one probe or conflicting evidence | `ambiguous`. This is the tested outcome for single-probe cooling loss and slow motor bias. |
| A sudden motor reading offset appears | `sensor_suspect`, including the tested combination of cooling loss and step bias. |
| Primary evidence is missing, invalid, stale, repeated, or out of order | `data_unavailable` and a fresh persistence window is required. An advisory sample may be at most five simulated seconds old. A cooling handoff requires both readings from the current tick and a frame under two real seconds old. |

The advisory trial covered 48 reserved scenarios, including 16 healthy runs. It observed zero alerts over 8.27 healthy hours, caught all eight cooling cases eligible for two-probe corroboration, and warned before all 12 qualifying hot crossings. The shortest warning lead was 78 simulated seconds. These results apply to the tested injections and sensor conditions. Gradual or correlated errors outside those cases and real component failure diagnosis require separate evidence. [Fault protocol](../../qualification/thermal/synthetic-fault-protocol-1.0.0.json) · [Advisory results](../verification/synthetic-advisory.json)

## Intervention and promotion boundaries

The comparison uses the same scenario inputs and local load protection for all three strategies. The forecast uses only recent observed motor temperatures. Its 60-second error averaged **1.30 K** and its empirical ±3.5 K band covered **99.31%** before predictive actuation. The single simulated grant adds cooling for up to 240 simulated seconds and stops on local protection, lost required evidence, or veto. The pump effect and the full mission tradeoffs are measured separately. The comparison reports pump and total energy separately, along with heat exposure, delay, and all mission outcomes. [Comparison and reproduction](../setup/synthetic-thermal-intervention.md)

Phase 6 needs a separately qualified integrated adapter and command-authority path before making a live thermal-and-charging claim. Real bus use also requires independent winding or housing reference measurements, motor-specific current, coolant flow and pump response where applicable, vehicle-specific calibration, and hardware protection evidence. Treat the testbench parameters, the assumed bus parameters, and the simulated control limits as three distinct sources of evidence. [Phase 5 acceptance and remaining gates](../verification/thermal-acceptance.md)
