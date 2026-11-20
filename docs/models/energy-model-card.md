# Energy model card: numerical foundation

Historical numerical component for the original supplied-plan forecast and stationary studies. Current geographic forecasting uses the separate [route-aware model](route-aware-energy.md).

Model: `longitudinal-energy-estimate/1.1.0`. Status: uncalibrated numerical foundation, not a qualified route forecast. Version 1.1.0 adds minimum estimated energy throughout the numerical trajectory; the original [1.0.0 verification record](../verification/energy-core.json) remains historical.

## Purpose

Estimate battery energy required by an explicitly supplied motion plan. The component is an independent calculation in `evdt.energy.model`; it neither drives the bus nor reads SUMO's hidden state. The current caller must supply every vehicle parameter. Mathematical test profiles are examples, not bus specifications or measured data.

Power is the rate of using energy. One watt applied for one second is one joule; one kWh is 3,600,000 joules. For example, stationary equipment drawing 500 W for one hour consumes 0.5 kWh even though driving distance is zero.

## Interface and outputs

`EnergyParameters` stores declared capacity, mass, resistance/geometry, efficiencies and power bounds. `DrivingInterval` stores a linear speed transition, duration, grade and auxiliary power. Both are immutable: once created, their fields cannot be reassigned normally. Adjacent speeds must join exactly.

`estimate_energy(parameters, tuple_of_intervals, initial_soc, step_s=0.2, max_steps=100000)` returns a frozen `EnergyEstimate`. SOC means state of charge, a fraction from zero to one. Results include estimated final energy/SOC, minimum energy, requested draw, potential regeneration, rejected recovery, unserved demand, numerical balance residual, planned distance/duration and model version. Field suffixes identify units. The minimum includes the initial state and every integration boundary; it is resolved at the numerical step size.

Positive requested battery demand consumes energy. Regeneration recovers some mechanical braking energy as electricity. Conversion losses and declared power limits reduce recovery; equipment can consume recovered electricity before it reaches a full battery. The result records excess recovery and unmet demand alongside the bounded battery state.

A plan with an earlier shortfall stays infeasible even if later braking raises the final charge. The returned planned duration/distance still describe the assumed plan, not how far the depleted bus would actually travel. The first-shortfall field marks the beginning of a numerical substep; its uncertainty includes that substep and the unvalidated plan/model assumptions.

## Equations, assumptions and limits

The exact equations and accounting tolerance are in [ADR-0039](../adr/0039-independent-energy-numerical-core.md). Work comes from acceleration, grade, rolling resistance and air resistance; battery demand additionally includes equipment and conversion losses. The integrator samples midpoint power and preserves the full battery ledger.

Assumptions: forward motion, constant loaded mass and air conditions per forecast, no wind, and linear speed/constant grade/equipment demand in each interval. There is no battery voltage/temperature/aging/chemistry model, charging-station curve, tire/grip/brake safety model, dynamic traffic predictor or calibrated operating envelope. Energy-feasible does not mean physically safe or achievable motion.

The declared capacity is an estimator input, not permission to inspect the simulator's actual capacity. The candidate evaluation includes capacity and parameter mismatch. SUMO owns the simulated battery and exposes its own charging/consumption behavior; the predictor must remain a separate estimate. [SUMO electric model](https://sumo.dlr.de/docs/Models/Electric.html)

## Validation state

Analytical cases, unit tests, and a closed-form acceleration and air-drag integral verify the numerical kernel. The separate calibrated candidate passed training, validation, and its reserved known-route evaluation. [Numerical record](../verification/energy-core.json) · [Candidate card](energy-calibrated-candidate-card.md)

The isolated Energy Worker passed its direct-access checks. Persistent forecast records, scoped authorization, dashboard integration, and station assessments passed the advisory closeout. Each product record retains its declared model and domain. [Worker checks](../verification/energy-boundary.json) · [Advisory closeout](../verification/energy-advisory-acceptance.md)

The [advisory model card](energy-advisory-model-card.md) describes observation-based initialization, explicit reserves, and the supplied-plan wrapper. SUMO owns the simulated battery state used to evaluate outcomes. Charging approach forecasts have their own stationary-departure evaluation and authority configuration. [Charging closeout](../verification/stationary-charging-acceptance.md)
