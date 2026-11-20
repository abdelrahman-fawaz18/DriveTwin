# ADR-0040: Observation-based advisory forecasts

Date: November 4, 2026. Status: selected for P2-A2; results are recorded separately in the verification summary.

## Decision and boundaries

Extend the independent Python energy package with two pure calculation methods and a small observation selector. A pure calculation takes explicit inputs and returns a result without reading files, databases, a simulator or a network. Existing monitoring remains unchanged. No service, dependency, wire format, database migration, actuator or automatic policy is introduced.

The reference method extrapolates recent measured net consumption. The supplied-plan method seeds the existing numerical core with the latest reported charge and an explicitly assumed future motion plan. Both remain advisory and uncalibrated. They provide no confidence interval: `uncertainty_method` is `not_established` and `model_qualified` is false. P2-B through P2-E remain required.

## Observation boundary

`observation_point` reads four coincident good-quality quantities from an already admitted reviewed 3.0.0, 3.1.0 or 3.2.0 observation: pack energy in joules, cumulative distance in meters, state of charge as a fraction, and forward speed in meters per second. Configurable `SignalBindings` maps signal identifiers to those quantities; adding unrelated sensors does not require changing the calculation. Wrong units, missing/duplicate required signals, invalid/nonfinite/out-of-range values and unsupported provenance produce an unavailable result with a reason code.

The three reviewed envelope schemas differ only in schema/catalog identity; their selected-field structure is identical, protected by a compatibility test. Unknown versions remain unavailable. This selector is not a second complete schema validator or an authentication mechanism. Upstream admission and later service access restrictions remain responsible for those duties. The library ignores unrelated measurements, annotations, manifests and hidden-state metadata. Four coincident required quantities are a deliberate initial restriction; asynchronous sensors need an explicitly versioned alignment adapter and tests before support is claimed.

Each window must stay within one mode, run, asset, source, session, source epoch and clock domain. Event identifiers must be unique; time and sequence must increase; cumulative distance cannot regress; time gaps, total duration and point count must satisfy the supplied policy. No sorting, deduplication, filtering or interpolation silently repairs a bad window. Allowed gaps and minimum windows are declared policy inputs, not universally qualified defaults.

Only simulation-clock inputs are supported initially. Recorded analysis uses an explicit frozen analysis time; a live snapshot requires a caller-declared running source. Future observations and latest-input age beyond the declared maximum are rejected. Simulation seconds are never compared with UTC. These checks do not authenticate a caller's live clock or exclude wall-clock transport staleness; trusted clock/connection state and access denial are later integration responsibilities.

External charging must be explicitly declared absent. Unknown or present charging produces unavailable output because charging would confound net battery-difference consumption. This caller declaration is an assumption, not an independently measured charging-state guarantee.

## Reference arithmetic and reserve

For a usable recent window:

```text
net_consumption_kWh = (first_energy_J - last_energy_J) / 3,600,000
energy_per_meter = net_consumption_kWh / traveled_distance_m
energy_per_second = net_consumption_kWh / elapsed_simulation_s
estimated_current_kWh = declared_capacity_kWh * latest_reported_SOC
required_trip_kWh = energy_per_meter * declared_remaining_distance_m
reserve_kWh = declared_capacity_kWh * explicitly_supplied_reserve_SOC
```

Net consumption already includes equipment demand and regeneration; neither is added again. Zero/negative net consumption, insufficient distance or insufficient history are unavailable for this method, not infinite range. The current estimate uses declared capacity and observed charge; it never derives the simulator's actual capacity from energy divided by charge. Capacity mismatch remains an explicit evaluation requirement.

The conditional distance/time to reserve divide the estimated energy above reserve by recent net energy per meter/second. They assume the recent driving pattern, including stopping and equipment demand, continues. They are not a maximum driving range or a route/traffic prediction. A zero current charge is valid; it is not confused with missing data. Unserved energy stays explicit when a trip would exhaust the estimate.

The reserve is mandatory, with no hidden operational percentage. Synthetic case percentages are test assumptions, not an approved dispatch policy. A feasibility flag is a mathematical result under assumptions, never authority to command a vehicle.

## Supplied plan and numerical version

The planned method requires declared estimator parameters, matching declared capacity, motion continuous from the latest observed speed, a matching mission distance and bounded integration work. The plan is a forward assumption; it must not be secretly populated from actual future traffic or motion. It returns results for that finite plan and does not extrapolate a maximum range or time beyond it.

Numerical model `longitudinal-energy-estimate/1.1.0` adds minimum estimated pack energy throughout the numerical trajectory. A reserve breach remains visible even if later braking or downhill recovery raises the arrival charge. The minimum includes the initial state and every integration boundary; it has the numerical step's resolution, not certified continuous-time accuracy. Earlier unserved demand also remains infeasible. Existing equations and accounting tolerance are unchanged. Preserve the 1.0.0 record and rerun the nine existing analytic profiles against 1.1.0.

## Results, identity and verification

Frozen result records carry model versions, request/mission/assumption identifiers, clock origin, input age, policy identifier, observation references and the numerical integration step where applicable. A deterministic SHA-256 identity covers normalized observations, complete supplied policy/bindings, declared parameters, assumed plan, numerical step, request and model versions. A normalized observation digest is not an original wire-byte digest. Preserve original-wire verification separately when reusing recorded packets. The qualification record fingerprints the complete public case file and source inputs; a digest alone cannot reconstruct them.

Unavailable results contain a stable reason and no numeric prediction, observation references or forecast identity. Runtime configuration errors are rejected during typed input construction. Persistent forecast storage, request authorization, complete API schemas and visible UI unavailable states remain P2-D1 work.

Verification uses independent hand-calculable cases, focused rejection/identity/reserve tests, all existing shared checks and the prior nine numerical profiles. Optional frozen Phase 1 packet analysis first requires its accepted reconciliation result and verifies each recorded canonical packet digest. It uses no new bus run or historical regeneration. That demonstrates accepted-format consumption, not held-out accuracy, causal live operation or runtime truth isolation. Whole-mission partitions, calibration, uncertainty coverage, D-18 target selection and mission accuracy remain open.
