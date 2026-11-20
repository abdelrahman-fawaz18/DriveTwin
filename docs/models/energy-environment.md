# Environmental and vehicle factors

The route-aware model accepts explicit physical inputs and records their provenance. Default vehicle coefficients are declared development assumptions. They are not manufacturer measurements. The [model card](route-aware-energy.md) describes the energy balance; this guide describes the additional inputs.

## Short explanation

A bus needs more energy when it carries more weight uphill, drives into a headwind, or runs a heater for longer. The model calculates these contributions separately. Current battery charge and equipment power initialize the forecast. Future weather, passenger changes, and equipment demand come from a selected forecast or an explicit operating scenario.

Each factor has a small independent check. Whole-trip accuracy is a separate study because accurate individual equations do not guarantee accurate predictions of traffic, stops, or future equipment use.

## Input treatment

| Input | Implemented treatment | Assumption or rejection |
|---|---|---|
| Wind | Convert meteorological direction into east/north velocity; resolve relative air movement against every road heading | Reference-height wind applies directly. Urban shelter, gust statistics, and a crosswind-dependent drag coefficient are not inferred. |
| Temperature and pressure | Dry-air density is pressure divided by `287.05 × temperature` in SI units | Both values are required; otherwise use the declared vehicle density. |
| Issued weather | Select a complete, unambiguous time/location series available at the forecast cutoff | Reject missing coverage, expired issue age, overlapping cells, and future observations presented as a forecast. |
| Time mapping | Explicit working-time origin, real-life-time origin, and seconds-per-second correspondence | Execution speed does not establish the weather scenario's time mapping. |
| Current equipment | Use accepted aggregate electrical power during the near horizon | Components already included in that total cannot be added again. |
| Forward equipment | Use a declared aggregate or the equipment profile below | Missing forward demand makes a longer forecast unavailable. |
| Loaded mass | Use declared profile mass or a scoped mass input | Reject vehicle-limit violations and mass changes while moving. |
| Battery temperature | Hold the current temperature for profile-curve queries | No battery thermal evolution is inferred. Missing required temperature or an out-of-domain curve query is unavailable. |
| Accessible capacity | A temperature curve sets inaccessible stored energy relative to nominal capacity | Stored energy and the source charge scale remain intact. |
| Battery power | Apply discharge and regenerative acceptance limits, including temperature/SOC curves | Unserved demand makes the movement plan infeasible; rejected recovery remains visible. |
| Conversion efficiency | Constant declared efficiency or a power-dependent interpolation curve | Interpolation stays inside its declared domain. |

## Equipment profile

The equipment model uses a steady cabin heat balance. It estimates the power needed to hold a setpoint; it does not simulate cabin warm-up.

```text
conductance = envelope conductance + door conductance × door-open fraction
required heating = conductance × (setpoint − outside temperature)
                   − occupant heat − absorbed solar heat
```

A positive balance requires heating; a negative balance requires cooling. Divide thermal demand by the relevant coefficient of performance (COP), then apply the equipment's electrical power limit. COP is the amount of heat moved or supplied per unit of electricity. A COP of 2 means 2 kW of thermal output for 1 kW of electrical input.

The returned total includes base electrical demand, ventilation, and climate equipment. Unserved heating/cooling is reported separately when the equipment reaches its limit. This is a comfort limitation, distinct from the battery's ability to supply electrical demand. Occupant heat, absorbed solar heat, conductance, setpoint, and COP must be provided by the scenario/profile. No universal coefficient is assigned to a weather label.

### Equipment-load calibration

For a representative vehicle profile, derive electrical equipment demand from logged bus telemetry or published measurements for a comparable vehicle. Record the vehicle model, equipment configuration, ambient conditions, and whether each value is an average, a peak, or a time series. Use rated electrical power to bound the estimate. The escarpment study's 10 kW remains a declared synthetic input. Any data-backed profile gets its own version and evaluation.

Measurements on a 12 m electric bus support a cold-weather heating case around 24 kW at −10 °C outdoors and a 17 °C cabin target for the studied equipment. The same study estimated roughly 12.5 kW of compressor electrical power at full cooling load for its reference unit. These values describe distinct operating conditions and component boundaries. They provide comparison points for a simulation sensitivity study. [Göhlich et al., Design of urban electric bus systems](https://www.cambridge.org/core/journals/design-science/article/design-of-urban-electric-bus-systems/1C0E4AA05F6E1FBF8A545E13F6A8D2DE)

## Error assessment

The residual evaluator retains the original forecast input. After readings arrive, it integrates only the completed working-time prefix of that original motion plan and compares its predicted net consumption with the observed stored-energy change.

```text
signed error = predicted net consumption − observed net consumption
```

Positive error means overprediction. The comparison requires the same source epoch/session and route revision, good readings, known absence of charging throughout the interval, the original cutoff observation, consistent capacity scale, and sufficiently complete ordered samples. Data received after assessment time cannot participate. The comparison uses equal elapsed working time; different actual traffic progress is part of its error.

Three nonoverlapping intervals with material errors in the same direction can flag persistent mismatch. The flag changes no parameter and does not promote a model. Charging, a source restart, a route change, or gaps prevent a valid comparison across that boundary.

## Verification

`python scripts/route_aware_energy/qualify_factors.py --output evidence/energy-v2/factors-NEW.json` runs nine constant-speed arithmetic cases without starting SUMO or services. Each changes one factor: wind, density, mass, rolling resistance, equipment demand, or efficiency. Closed-form work provides the comparison. Focused unit checks additionally cover cold capacity, HVAC limits, provider causality, and residual rejection paths. The [verification record](../verification/energy-route-aware.md) retains the results and their limits.
