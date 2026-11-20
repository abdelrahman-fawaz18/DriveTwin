# Energy advisory status

The advisory energy milestone is complete for its declared simulated route and input domain. The final review closed the numerical, evaluation, storage, read API, dashboard, and station-assessment items. [Advisory closeout](verification/energy-advisory-acceptance.md) · [Model card](models/energy-advisory-model-card.md)

## Completed checkpoints

| Checkpoint | Delivered scope | Evidence |
|---|---|---|
| P2-A1/A2 | Numerical energy ledger, recent-consumption reference, assumed-plan estimates, reserve and input-validity checks | [Numerical core](verification/energy-core.json) and [advisory check](verification/energy-advisory.json) |
| P2-B1 | Whole-mission partitions and isolated one-request worker | [Worker and partition design](setup/energy-worker-and-mission-partitions.md) |
| P2-B2 | Twelve training and six validation trips, fitted candidate, and frozen evaluation limits | [Calibration](verification/energy-calibration.json) |
| P2-C1 | Six reserved final-test trips scored once against the frozen release | [Held-out result](verification/energy-heldout.json) |
| P2-D1 | Immutable forecast storage, restricted input and writer roles, and Energy page | [Product integration](verification/energy-product.json) |
| P2-E1 | Forecast explanations, assumed-plan station assessments, browser unavailable/recovery states, and restore checks | [Final closeout](verification/energy-advisory-closeout.json) |

## Reproduction and model scope

The input, model, evaluation protocol, and reserved claims retain their recorded fingerprints. Use inspection modes to verify the sealed studies. The persistent Energy Forecast binds the source, route, cutoff, and assumptions to each result. It checks current availability separately from the historical record. [Forecast setup](setup/persistent-energy-forecasts.md) · [Historical recorded example](engineering/legacy-energy-example.md)

Station approach accuracy and charging authority have their own later evaluation and control configuration. The Charging Advisor uses the qualified stationary-departure model. [Station forecast](setup/station-arrival-forecast.md) · [Charging closeout](verification/stationary-charging-acceptance.md)
