# Historical known-route energy study

This reference describes the frozen known-route predictor and its saved product example. It is retained for interpreting legacy forecast records, evaluation claims and stationary charging qualifications. The current geographic predictor plans over route geometry and elevation without route-specific training. [Current model](../models/route-aware-energy.md)

## Frozen study

The original candidate used a historical speed/distance template from training, anchored at the observed speed and odometer. Its study separated 12 training, 6 validation and 6 reserved complete missions. Training fitted effective rolling resistance and propulsion efficiency; validation selected settings and scenario ranges; the final six outcomes were scored once after freezing the candidate and criteria.

Mean absolute remaining-energy error was 0.285 kWh, compared with 2.384 kWh for the recent-consumption reference. These results apply to the declared synthetic known-route domain and 120-working-second cutoff. They do not establish new-map accuracy or probability coverage for the route-aware model. [Candidate card](../models/energy-calibrated-candidate-card.md) · [Reserved result](../verification/energy-heldout.json)

Consumed claims, original fitted/template artifacts and source fingerprints remain part of the retained study. Use the [read-only inspection procedure](../setup/held-out-energy-evaluation.md) rather than rerunning or retuning a consumed evaluation.

## Saved forecast example

The successful product integration example used 61 observation sets from working seconds 60 through 120. At the cutoff it reported 79.9% charge, 490.838 m traveled, 6.741 m/s speed and 8 kW equipment demand. The independently declared capacity was 350 kWh. Initialization therefore used `0.799 × 350 = 279.650 kWh`; it did not infer capacity from the simulator's raw energy/charge ratio.

The assumed flat-road remainder covered 4.643 km and 770.190 working seconds. It used the frozen training template, 18,000 kg loaded mass, rolling coefficient 0.010 and propulsion efficiency 90%, with the other recorded candidate parameters.

| Energy account | Recorded value |
|---|---:|
| Propulsion draw | 8.731 kWh |
| Equipment draw | 1.712 kWh |
| Total requested draw | 10.442 kWh |
| Potential braking recovery after conversion losses | 2.793 kWh |
| Recovery rejected by the power limit | 0.012 kWh |
| Recovery used or stored | 2.781 kWh |
| Net use | 7.662 kWh |

Rounded accounts need not subtract exactly. At full precision, final energy was `279.650 − 7.661603 = 271.988397 kWh`, or 77.711% of the declared capacity. The modeled minimum remained above the 10%, 35 kWh reserve. The scenario range was 5.330–10.212 kWh and 75.753–79.131% across selected assumptions. It was not an independently calibrated probability interval.

The recent-consumption cards instead extrapolated the preceding minute: approximately 1.03856 kWh/km and 20.88 kW. Those conditional rates produced 235.567 km and 703.017 minutes to the reserve. They did not describe the actual remaining route or promise that the pattern would persist.

The product stored model, input and assumption references, displayed the saved values, and withheld current guidance after stale input or disconnection while keeping history. The worked example came from a retained training recording; the separate reserved trips supplied accuracy evidence. [Integration closeout](../verification/energy-advisory-closeout.json) · [Legacy Energy page](../setup/advisory-mission-assessments.md)

## Units and shared interpretation

Energy is an amount: 10 kW of equipment power for half an hour uses 5 kWh. State of charge is a percentage whose energy conversion depends on the declared capacity. Reserve is a configured energy buffer. A forecast must keep the minimum along the route as well as arrival charge; later recovery cannot undo an earlier shortfall.

These concepts also apply to the route-aware model, but its movement planner, factors, lifecycle and result contract have their own [model card](../models/route-aware-energy.md) and [qualification record](../verification/energy-route-aware.md). Historical station approaches retain their own [station-arrival study](../setup/station-arrival-forecast.md).
