# Replay offline charging recommendations

The advisor answers whether a simulated bus can use each station and which eligible station adds the least estimated time. In the example replay, `west-c` is closer when `depot-a` is down. The advisor recommends `opportunity-b` because its faster charging more than offsets the longer drive. The comparison uses the same eligible stations for both the time-based advisor and the nearest-station reference. [Charging policy](../charging-policy.md)

Each decision uses a stationary departure observation, an explicit position on the mission route, and station updates available at that simulation time. The route index distinguishes separate visits to the same road. If the bus still owes its scheduled stop, the detour must return to that stop. After service, the comparison uses the mission finish. Both paths use the same return point. The road estimate sums time at mapped speeds on external road segments. The frozen energy model separately uses its qualified approach-time calculation. [Forecast model](station-arrival-forecast.md)

The 35 kWh reserve applies to the lower end of the estimated minimum energy through the expected wait. A free bay with no queue has zero wait in this replay. A full station must have space in its queue and an estimate of at most 45 simulated seconds. The 87.5 kWh charging target uses a declared budget for the rest of the mission and must fit the bus's charge limit. The score adds detour time, queue time, and estimated charge time at constant net power. The bus's charge acceptance, charger efficiency, and equipment draw reduce net power. The continuation minimum, remaining mission budget, and charger acceptance are explicit scenario inputs for this offline stage.

The replay considers a station update once its availability time arrives. Freshness uses the observation time, so delayed data can arrive already stale. When a later-delivered update describes an older observation, the newest available observation still wins. A voluntary switch requires a gain of at least 30 seconds for 20 simulated seconds, with decision samples at most 10 seconds apart. The 60-second cooldown starts when the current station is selected. Losing eligibility cancels the current choice immediately. If every candidate is blocked, the record says `no_feasible_station` and clears the recommendation. These are versioned offline policy settings.

Run the example from PowerShell in `<checkout>`. The launcher uses the existing Ubuntu simulator image to read the archived network and the frozen Phase 4A release. The project is mounted read-only and the evidence folder receives the decision record. Choose a fresh output filename. [Launcher guide](linux-launcher.md)

```powershell
python scripts/linux.py --timeout 90 -- docker run --rm --network none --read-only --memory 384m --mount type=bind,source=<checkout>,target=/workspace,readonly --mount type=bind,source=<checkout>/evidence/p4,target=/output -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 /app/.venv/bin/python -m scripts.charging.run_charging_advisor --scenario qualification/charging/advisory-scenario-1.0.0.json --output /output/my-advisor-replay.json
```

Expect ten decisions. At 60 simulated seconds the advisor switches to `depot-a` after a sustained improvement. At 70 it receives the outage observed at 65 and immediately recommends `opportunity-b`. At 80 every station is blocked. At 90 a new update makes `depot-a` eligible with a 20-second wait. The record shows status ages, exclusion reasons, the two route estimates, energy ranges, and each eligible station's score components. [Example scenario](../../qualification/charging/advisory-scenario-1.0.0.json)

Run the regression checks in the same image with the archived network mounted read-only.

```powershell
python scripts/linux.py --timeout 120 -- docker run --rm --network none --read-only --memory 384m --mount type=bind,source=<checkout>,target=/workspace,readonly -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 /app/.venv/bin/python -B -m unittest discover -s tests/charging -v
```

The checks cover the replay sequence, mandatory-stop rejoin, freshness and update order, incompatible connectors, unavailable departure data, reserve crossing while waiting, and switching boundaries. Phase 4C will measure charging and mission outcomes in matched simulator scenarios. Runtime forecast qualification and dispatch authority remain separate gates.
