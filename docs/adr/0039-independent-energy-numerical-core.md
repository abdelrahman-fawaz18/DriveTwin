# ADR-0039: Independent advisory energy calculations

Date: November 4, 2026. Status: selected for the numerical energy study; measured numerical results belong in the verification record.

## Responsibility and scope

Add `evdt.energy` as a separate domain package in the existing Python codebase. It accepts explicit immutable parameter and motion-plan records and returns an estimated energy ledger. It imports no simulator, registry, database, transport, or file/network client. Existing monitoring, wire formats, database migrations, and plant energy ownership remain unchanged.

SUMO remains the actual simulated battery-energy owner. The independent estimator never updates that battery or raw observations. SUMO can continue moving at zero battery unless separate behavior controls intervene; motion completion alone therefore cannot prove an energy-feasible mission. [Official electric-vehicle behavior](https://sumo.dlr.de/docs/Models/Electric.html#vehicle-behavior)

This increment is P2-A1: numerical implementation and analytical verification. It does not establish calibrated forecasts, a supported real-bus operating envelope, uncertainty coverage, runtime access isolation, usable route/range/time prediction, or an operational forecast API. Those remain explicit subsequent steps.

## Inputs and model

All vehicle numbers are declared estimator assumptions, supplied by the caller. Required fields name their units: usable capacity in kWh, loaded/equivalent rotating mass in kg, frontal area in square meters, air density, resistance coefficients, drive/recovery efficiencies, electrical recovery/discharge power bounds, and gravity. There is no bus ID, sensor hardware, or hidden plant-parameter lookup in the numerical code.

Each planned interval supplies duration, starting/ending forward speed, grade as rise/horizontal run, and battery-side auxiliary power. Speed is linear inside an interval and continuous across interval boundaries. Constant mass, no wind, and constant grade/equipment demand per interval are explicit simplifications. Geometry and parameters do not prove the bus can safely or physically follow that plan.

For speed `v`, acceleration `a` and grade angle `theta = atan(grade_ratio)`:

```text
F_wheel = (mass + equivalent_rotating_mass) * a
          + mass * gravity * sin(theta)
          + rolling_coefficient * mass * gravity * cos(theta)
          + 0.5 * air_density * drag_coefficient * frontal_area * v^2
P_wheel = F_wheel * v
requested draw = max(P_wheel, 0) / drive_efficiency + auxiliary_power
potential electrical regeneration = max(-P_wheel, 0) * recovery_efficiency
```

Recovery is limited by its declared electrical-power bound before supplying equipment/pack demand. Net positive demand is limited by declared battery discharge power and remaining estimated energy. Net negative demand cannot fill the pack above its declared capacity. Every unavailable requested joule and every rejected recovery joule stays in the result.

## Numerical and accounting policy

Use midpoint-held power with configurable maximum step duration, default 0.2 seconds. Split an interval into equal substeps so the final interval boundary is exact. A configurable 100,000-step default budget is checked before integration. Reject invalid/nonfinite/unrepresentable values, noncontinuous plans and oversized work; do not silently coerce them into an estimate.

The battery ledger is:

```text
initial + potential_regeneration + unserved_demand
  = final + requested_draw + power_rejected + capacity_rejected
```

The floating-point balance tolerance is `1e-6 J + 1e-10 * max(initial, requested_draw, potential_regeneration)`. This numerical tolerance is not a physical accuracy target and does not close D-18. Estimated pack energy must stay in `[0, declared_capacity]` independently of that tolerance.

The first substep with unserved demand is retained even if later downhill/braking recovery increases final charge. Its start time is a numerical-step marker, not an exact certified depletion time. Planned distance/duration refer to the complete assumed trajectory and cannot be advertised as achievable travel after a shortfall. `energy_plan_feasible` means only that this assumed calculation had no unserved demand; it is not authorization to operate a vehicle.

## Verification and next boundaries

Verify zero demand, stationary equipment load, kinetic-energy work, rolling/air/grade terms, conversion losses, regeneration bounds, empty/full packs, electrical limits, earlier shortfall followed by recovery, numerical refinement and repeatability against independent analytical expectations. Version the case inputs and save exact input/code fingerprints before and after each qualification.

Add new model code to existing strict typing and offline checks. Reuse Phase 1 evidence only after checking its recorded files; do not weaken its source/image guards. No new framework, sensor protocol, storage engine or application service is introduced here.

Next: define observation-based reference forecasts and mission inputs, isolate the operational runtime from hidden truth, design whole-mission training/validation/held-out partitions, then freeze D-18 targets on validation evidence before final held-out evaluation. Keep a failing/unvalidated forecast advisory or unavailable. Later forecast storage/API/UI must preserve input lineage, age, assumptions and model version separately from observations.
