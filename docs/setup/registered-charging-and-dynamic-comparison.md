# Verify registered charging and compare dynamic outages

The charging service now links an operator request to a measurable change in the simulated bus. The simulator first registers its run with the monitoring registry. Think of registration as an identity card for this experiment: it binds a particular bus and run to the reviewed model, station catalog, and road network. The service uses fresh, separately authenticated bus and station observations to recommend a charger. Only the operator can accept that recommendation.

The executor is the software that changes the simulated route. Before the service gives it the command, PostgreSQL saves a **reservation**, meaning that command may have reached the executor and cannot safely be sent again. If the service restarts, the reservation is still there. The command stays `reserved_unknown` until the measurement reviewer confirms charging, the battery target, motion, and arrival. The observer establishes the effect from the simulator trace. The test harness exercises separate role credentials and independently reconciles the trace; a deployed system would separate those duties across processes. These are dedicated charging observations, separate from regular dashboard telemetry.

The approach check compares the measured arrival and lowest approach energy with the existing, frozen forecast intervals. The domain is a stationary synthetic bus on a reviewed route. The remaining mission energy, auxiliary draw, post-charge budget, empty bays, and zero waiting time are declared assumptions for this fixture. They do not qualify a forecast for traffic, moving departures, or an actual bus.

## Follow the departure outage decision

In the matched departure test, the parked bus reported **54.000 kWh** at **60.2 simulated seconds**. The test supplied a **34 kWh** minimum for continuing along the original route to exercise the charging decision. That input was below the **35 kWh** reserve. The separate no-charge reference actually stayed above reserve, with a minimum of **48.307 kWh**. The 34 kWh input belongs to the test setup. This decision used a separate forecast for the drive to each charger, built for a bus starting from rest. [Charging policy](../charging-policy.md) · [Station forecast](station-arrival-forecast.md)

The advisor checked each station's status and plug, a permitted road path to the charger and back to the mission, and whether the forecast stayed above reserve. For Opportunity B, it estimated **1.334 kWh** for motion and **0.547 kWh** for equipment over the **246.3-second** approach. Subtracting the unrounded estimates from 54 kWh gives **52.118 kWh** at arrival. The low end of its estimated minimum across the approach was **50.830 kWh**, including the model's 1.2 kWh allowance. That cleared the 35 kWh reserve.

| Station | Distance to charger | Low estimate of minimum energy | Net charging power | Estimated added time |
|---|---:|---:|---:|---:|
| Depot A | 1,128 m | 50.946 kWh | | Excluded. Power outage. |
| Opportunity B | 1,206 m | 50.830 kWh | 87.00 kW | 24.40 min |
| West C | 69 m | 52.595 kWh | 63.25 kW | 31.89 min |

The test target was **87.5 kWh**, or 25% of the 350 kWh battery. The advisor also checked that subtracting the test's **15 kWh** allowance for the rest of the mission would leave at least the reserve. Opportunity B needed about `87.5 − 52.118 = 35.382 kWh` of battery gain. Its net power was `100 × 0.95 − 8 = 87 kW` after charger losses and equipment demand. Dividing the energy needed by that power gives **24.40 minutes**. West C needed less energy at arrival, but `75 × 0.95 − 8 = 63.25 kW` gave it a longer charging time.

The full score adds any extra driving time and expected waiting time to charge time. The two eligible bays were empty. The road calculation compared travel through each station with the original route to the same rejoin point and counted zero extra driving time for both. The forecasts still include the drive to the charger. The estimated score is separate from the measured delay over the whole mission.

The test runner used the recommendation in an operator-style route request. SUMO then measured **35.213 kWh** delivered at Opportunity B, a battery level of **87.503 kWh** at the target, and completed the mission. Native Stationfinder also charged at Opportunity B. It delivered **33.451 kWh**, reached a peak of **85.615 kWh**, and completed the mission. The simulator gave both policies the same charger power at departure. Their search and charging logic produced different charge outcomes, so the recorded mission times apply to their respective outcomes. [Matched comparison evidence](../verification/stationary-charging-closeout.json)

DriveTwin makes its recommendation outside SUMO from bus and station observations, records the reasons, and hands a diversion to the command path for authorization. Stationfinder works inside SUMO. It uses its configured battery thresholds and a weighted station score that can include travel, waiting, and charging time. It can reroute a simulated vehicle directly. The matched test used a 20% search threshold and turned off Stationfinder's optional remaining-route sufficiency check to exercise a charging stop. [SUMO Stationfinder decision logic](https://sumo.dlr.de/docs/Simulation/Stationfinder.html#decision-logic-for-charging)

## Reproduce the operator and interruption checks

Open PowerShell in `<checkout>`. Use the existing Ubuntu Docker Engine, pinned simulator image, and original Phase 4A network artifact. Choose a new output folder for each run.

```powershell
python scripts/linux.py --timeout 250 -- python3 scripts/charging/qualify_registered_charging.py --output evidence/p4/my-registered-charging
```

Expect `mission: True` and `interrupted: True`, typically within two minutes on a warm Engine. The first test observes a diversion, charge, resumption, and mission arrival. The second discards the handoff before the route can change. Both restart the command service and verify that it refuses to hand out the reserved command again. The top-level `qualification.json` requires both cases and unchanged source fingerprints. Each case contains its cleanup result. The runner removes its own test containers, volumes, and networks. If a check fails, read the case `qualification.json` and private `commands.log`; keep those results and use a new directory for any reviewed rerun.

## Reproduce the comparison

SUMO (Simulation of Urban Mobility) simulates the bus and its charging stations. Its TraCI interface lets a test script change station power while the simulation runs. Zero power is a physical outage that all three policies encounter. The runner checks the new power directly from SUMO. For the first case, the preferred station loses power, briefly recovers, and loses power again before departure. For the second, it fails after departure and later recovers. No policy sees future outage times. [SUMO station control](https://eclipse.dev/sumo/docs/TraCI/Change_ChargingStation_State.html)

```powershell
python scripts/linux.py --timeout 260 -- docker run --rm --network none --read-only --memory 512m --tmpfs /tmp:size=128m,mode=1777 --mount type=bind,source=<checkout>,target=/workspace,readonly --mount type=bind,source=<checkout>/evidence/p4,target=/output -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 /app/.venv/bin/python -B -m scripts.charging.run_dynamic_charging_comparison --output /output/my-dynamic-comparison
```

Expect six outcome lines and a `qualification.json`, usually within two minutes on a warm Engine. The JSON includes a no-charge reference, added mission time, minimum battery energy, delivered energy, every completion and failure, and changes to the preferred station. The comparison separates a recommendation change before departure from a destination change once driving. It also separates the station selected by software from the station that actually delivered energy.

The two custom arms use the same eligibility rules. The advisor ranks the estimated extra time; the nearest baseline ranks distance. Stationfinder is SUMO's built-in charger selector. It uses the same physical station power and local safety limits but its own search timing and departure charge target. It does not consume DriveTwin's status feed. The custom arms dispatch only at departure. In the in-service outage they can wait for physical power to return, but this check does not qualify moving-bus replanning. No arm can tow, teleport, or receive an injected battery refill. [SUMO Stationfinder settings](https://eclipse.dev/sumo/docs/Simulation/Stationfinder.html)

In the measured comparison, all six missions completed with zero reserve violations, zero depletions, and zero rescue interventions. The no-charge reference finished at 594.8 simulated seconds. The table rounds to one decimal place. [Recorded evidence](../verification/registered-charging-and-dynamic.json)

| Outage | Policy | Actual charger | Added time (s) | Energy delivered (kWh) | Reached 87.5 kWh |
|---|---|---|---:|---:|---|
| Before departure | Advisor | Opportunity B | 1,242.2 | 35.2 | Yes |
| Before departure | Nearest feasible | West C | 1,308.0 | 33.6 | Yes |
| Before departure | Stationfinder | Opportunity B | 1,260.0 | 33.5 | No |
| After departure | Advisor | Depot A | 1,038.6 | 34.8 | Yes |
| After departure | Nearest feasible | West C | 1,308.0 | 33.6 | Yes |
| After departure | Stationfinder | Depot A | 851.0 | 27.4 | No |

The advisor changed its recommendation once before departure. Its in-service run first received charge at 750.2 seconds, one simulator step after power returned. In that same case Stationfinder reported one destination change and SUMO emitted `cannot replace reached stop`. The measured energy came from Depot A and the bus completed the mission. A reported destination change by itself does not establish a physical diversion or charge.

## Stationary charging acceptance

| Requirement | Evidence at P4-D2 |
|---|---|
| P4-01. Divert, charge, and resume with verified effects | Passed for one registered operator-run fixture. |
| P4-02. Reject infeasible stations | Passed in the advisory and local protection cases. The registered path also rejects stale or future station snapshots. |
| P4-03. Bounded replanning | One switch under a parked outage, with no switch back. The offline advisor covers occupancy inputs. Moving custom replanning is outside the measured domain. |
| P4-04. Matched comparison | Same physical power schedule and reserve cutoff in both scenarios. Each policy retains its search timing and target. |
| P4-05. Report mission outcomes | All six dynamic outcomes plus the prior adverse cases are retained. |
| P4-06. Automatic safety and precedence | Pending explicit approval and bounded automatic verification. |

This table records the status at P4-D2. P4-D3 subsequently qualified the approved one-bus automatic profile, rechecked the default operator configuration, and added the [common-departure comparison](automatic-charging-qualification.md#run-the-matched-departure-comparison). The [closeout](../verification/stationary-charging-closeout.json) records the final gate. The operator configuration in this guide still refuses automatic dispatch.
