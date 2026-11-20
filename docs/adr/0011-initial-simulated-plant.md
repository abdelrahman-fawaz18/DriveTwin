# ADR-0011: coherent initial bus measurement plant

Date: November 3, 2026. Status: implementation decision; measured results are recorded separately.

## Scope and authority

P1-B1 extends the four motion readings to the 20 initial scalar signals. It implements an explicitly assumed plant for monitoring development, not a calibrated real bus or a range/remaining-time decision service. Complete run registration, paused-clock/multi-source policies and sensor quality/failure modes remain P1-B2 work.

Preserve ADR-0008: SUMO alone advances movement and battery energy at the mission step. Equip the synthetic bus with SUMO's battery device and Energy emission model. Configure mass/load, drag/rolling terms, drive/regenerative efficiencies and constant auxiliary demand explicitly. Read battery charge level and actual completed-step consumption through TraCI; Python never subtracts energy from a second battery state. Wh is converted to J by 3600; completed-step J divided by interval s gives W. Verify the selected version through integration tests and record its observed behavior. [SUMO electric mechanism](https://sumo.dlr.de/docs/Models/Electric.html), [vehicle variable semantics](https://sumo.dlr.de/docs/TraCI/Vehicle_Value_Retrieval.html).

Non-HVAC auxiliaries plus HVAC equal the constant power configured once in SUMO. HVAC means heating, ventilation, and air conditioning. Report total auxiliary power and its HVAC subset; consumers must not add that subset again. No chargers are active. Capacity bounds are checked, and depleted energy stops or invalidates this initial source. Complete charging/depletion behavior is still a P2/P4 gate.

The selected installed version initializes charge on the insertion tick without applying its already available emission-rate query to the battery device. Explicitly check first-tick time, configured initial energy and zero device step consumption; initialize dependent thermal state there. From the next tick onward, use `device.battery.energyConsumed` as the actual net completed-step Wh, derive pack power from its duration, and enforce a 0.01 J per-step state/energy residual limit. Record insertion separately. The independent emission-rate query showed motion-dependent differences and is retained only as a diagnostic maximum difference; its exact internal cause is not asserted here. Do not widen the tolerance or silently substitute that query for battery accounting.

## Dependent measurements

Use an idealized constant-voltage pack so current = net pack power / voltage. Positive power/current means discharge; negative means net regeneration. SOC = usable energy / capacity. Pack power is the last completed simulation-step rate, not a one-second average; report this semantic in the dictionary. Each step checks energy change against the rate and interval; explicitly account for saturation at capacity boundaries.

Reconstruct equivalent mechanical motor power from drive electrical power (pack power minus total auxiliaries) using the same assumed drive/regenerative efficiencies. Motor angular speed follows road speed, effective wheel radius and final-drive ratio. Equivalent torque = mechanical power / angular speed above a declared small-speed threshold; report zero below that threshold and document this low-speed approximation. These are synthetic aggregate motor channels, not access to a modeled rotor or independent torque sensor.

Allocate the reconstructed drivetrain loss budget between pack and motor heat with a declared fraction. Advance two independent lumped thermal states against ambient using the exact constant-input first-order solution at each SUMO step. A lumped model treats each component as having one uniform temperature. Temperature is an observation from this assumed plant; it is not the twin's future prediction. No calibrated thermal accuracy, coolant circuit, temperature-based protection or extra energy drain is implied.

Heading and acceleration come from movement. Horizontal accuracy is zero for this ideal coordinate sensor, explicitly not a real GPS accuracy claim. Brake pressure is an assumed deceleration-linked proxy with a declared full-scale deceleration/pressure; it is not inferred hydraulic pressure or an independently validated braking subsystem. Keep the reserved coolant-inlet signal out of the initial catalog.

## Extensibility, compatibility and provenance

Keep source stepping, dependent plant calculations and scalar sampling in separate modules. Signal-specific mappings belong to the adapter/sensor definitions; central history/current storage remains generic. Sampling periods are configured as integral multiples of the fixed simulation step; deterministic timing is based on simulation ticks, independent of wall-time pacing.

Freeze the original four-signal catalog as 1.0.0 and add current catalog 1.1.0. Preserve envelope 2.0.0; add compatible simulation envelope 2.1.0 for the additive catalog. The monitoring reader explicitly selects registered catalog versions, never rewrites historical bodies and rejects unknown versions. Qualification 1.0.0/1.1.0 and its immutable original fixtures remain unchanged. Runtime sample quality remains good-only for this slice; missing/invalid/noise semantics require the next explicit extension.

Save an immutable run manifest before first sampling with input/config/catalog/code fingerprints, model/version/assumption labels, actual SUMO/TraCI identity, time step, cadence and pacing mode. Each current source event references its manifest through informational annotations. This is reproducible audit provenance, not authorization or a completed server-side run registry; the first artifact remains in private source/evidence output.

## Required evidence

- Unit checks: conversion/sign conventions, auxiliary decomposition, thermal analytic behavior, invalid configuration and nonfinite inputs, sampling cadence and low-speed approximation.
- Real SUMO runs: exact repeatability on fixed inputs, energy/rate reconciliation at each step, capacity/SOC/current/power consistency, stopped auxiliary draw, deceleration/regeneration and all 20 registered signals.
- Real uplink: every expected scalar persisted with original bytes and manifest reference, history/current coverage, existing outage/restart and access-control negatives.
- Shared Python/TypeScript outcomes for old/current catalogs and unknown versions; frozen original fixtures unchanged. Preserve failed attempts and exact scope limits.
