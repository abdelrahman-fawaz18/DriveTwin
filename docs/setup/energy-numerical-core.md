# Energy model numerical checks

Historical known-route energy procedure. Its original model, workload and evaluation identities remain scoped to the frozen study; pending statements below describe that checkpoint. Use the [route-aware operating guide](route-aware-energy.md) for current geographic forecasting and the [final advisory closeout](../verification/energy-advisory-acceptance.md) for the completed legacy product scope. Inspection of retained results does not authorize rerunning a consumed evaluation.

This is a small offline mathematical check. It starts no bus, browser, broker or application database. The development image uses the project's locked Python runtime and dependencies; its normal checks also cover the existing monitoring code.

## Run shared checks

Open Windows PowerShell in `<checkout>` and run:

```powershell
python scripts/dev.py check
```

The project launcher executes Linux tools in Ubuntu through WSL from Windows PowerShell. Expected: Python/dashboard tests, strict types, contracts, formatting/build and privacy checks pass. The report is under ignored `.runtime/dev-checks/`. This builds the current `evdt-development-checks:local` image, including the new energy package and reviewed analytic cases.

## Run the analytical profiles

Choose a new private evidence name; existing names are refused. Create the evidence parent once:

```powershell
New-Item -ItemType Directory -Force evidence/p2 | Out-Null
python scripts/linux.py --timeout 120 -- docker run --rm --network none --read-only --memory 512m --cpus 2 --mount type=bind,src=<checkout>/evidence/p2,dst=/app/evidence/p2 evdt-development-checks:local python scripts/known_route_energy/qualify_energy_core.py --evidence-dir evidence/p2/YOUR-NEW-CORE-CHECK
```

`--network none` disables the check container's network. `--read-only` protects its packaged source; the sole evidence mount permits private result writing. Memory and CPU options bound this small calculation. `--rm` removes the temporary check container when it ends. No persistent Docker volume is created.

Expected calculation time is a few seconds; launcher/container startup adds several seconds. The command prints `Energy core passed: True; cases: 9` and a report path. The report must contain `passed: true`, nine distinct passing cases and unchanged before/after input/code fingerprints. An error or nonzero exit is a failed check. Preserve its evidence before changing code; do not edit expected answers to make a failure disappear.

The nine profiles are in `qualification/energy/core-cases.json`: zero demand, stationary equipment, acceleration/conversion loss, rolling resistance, air drag, regeneration power bound, full pack, electrical power shortfall and later recovery after an earlier shortfall. Independent unit tests add grade losses, convergence, repeatability and invalid/bounded-work cases.

## Understand one result

For the stationary-equipment case, 500 watts over 3,600 seconds is 1,800,000 joules, or 0.5 kWh. Starting with 1 kWh leaves 0.5 kWh. Distance remains zero. This checks that equipment demand is counted once and is not omitted when the vehicle is stopped.

For a braking case, potential electrical recovery can exceed the allowed recovery power or available battery room. The result records those rejected joules. For an insufficient-energy/power case, the result records unserved demand and an infeasible assumed energy plan; it does not claim the vehicle actually completed that motion.

These checks establish numerical behavior only. The [model card](../models/energy-model-card.md) lists the assumptions and remaining validation. API/UI integration and operational-runtime isolation are not exercised here.
