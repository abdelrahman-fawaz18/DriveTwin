# Stationary charging acceptance

Phase 4 qualifies one automatic diversion for one registered synthetic bus at rest in an isolated simulator. The approved profile is separate from normal monitoring and operator charging, which keep automatic dispatch off. [Decision and limits](../adr/0063-qualify-bounded-automatic-charging.md) · [Machine-readable results](stationary-charging-closeout.json)

| Gate | Qualified outcome |
|---|---|
| P4-01. Divert, charge, and resume | Registered operator and automatic missions completed. The automatic run charged at Depot A, reached 87.5 kWh, resumed, and arrived. The independent observer tied measured effects to the durable command. |
| P4-02. Reject infeasible stations | The advisory and fixture checks exclude incompatible, unreachable, stale, and reserve-violating stations. The blocked case holds the parked bus. A physical outage after reservation invalidates the automatic proposal before the route is released. |
| P4-03. Bounded replanning | The parked outage scenario switched once and did not flip back. Offline replay tested full bays, waiting assumptions, late reports, and sustained improvement. The moving custom policy remains outside the qualified forecast domain. |
| P4-04. Compare policies under matched conditions | All three arms received the same physical station state at departure and ran with the same outage schedules, road, bus, and reserve cutoff. Native rescue was disabled. Search timing and charge targets remain policy choices. |
| P4-05. Account for every mission | The nominal, adverse, parked-history, and matched-departure records retain all outcomes, including incomplete and refused missions. The excess-load case remains a measured reserve violation and incomplete mission. |
| P4-06. Bound automatic authority | A permitted command was granted once. Override, late thermal and local limits, stale observations, both types of expiry, and loss of the chosen charger stopped the route release. Interrupted and discarded grants never became confirmed effects. |

The automatic mission measured about **35.15 kWh** delivered, **52.25 kWh** minimum remaining, and **1,086.8 simulated seconds** to completion. All six matched-departure comparison runs finished above the 35 kWh reserve. Stationfinder reached a different final charge, so completion times do not establish an equal-energy ranking. [Comparison and figures](../setup/registered-charging-and-dynamic-comparison.md)

One charging profile owns actuation for a given run. The operator veto and final grant are ordered by the same database lock. A late protective observation or veto prevents a not-yet-granted route. The thermal block used here was injected. The thermal model, interaction of distinct live interventions, deployment hardening, moving custom replanning, real queue behavior, and physical vehicle control are outside this qualification.

Use the [automatic run guide](../setup/automatic-charging-qualification.md) to reproduce the evidence. The closeout verifier checks current source fingerprints against the new reports and verifies the earlier D2 evidence against the exact historical Git revision. It keeps raw traces and credentials out of the versioned summary.

The subsequent thermal studies audit the laboratory signals, separate complete drive profiles, and evaluate the frozen model on reserved sessions. [Thermal closeout](thermal-acceptance.md)
