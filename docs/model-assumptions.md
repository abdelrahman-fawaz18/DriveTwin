# Initial vehicle, route, and model assumptions

Status: explicit starting hypotheses, not calibrated bus specifications. Machine-readable values live in [bus parameters](../simulation/montreal/bus-parameters.json), [mission](../simulation/montreal/mission.json), and [stations](../simulation/montreal/stations.json).

An assumed parameter is chosen for an experiment; a sourced parameter comes from an identified reference; a verified parameter has supporting validation in its stated domain. This initial generic bus uses assumed values. No manufacturer, operator, employer or researcher is represented as approving them.

| Item | Initial value / scope | Why and validation boundary |
|---|---|---|
| Geometry | 12 m length, 2.55 m width, 3.2 m height | Generic urban bus hypothesis; length/width feed movement, other physical effects await P2 |
| Mass | 13,500 kg curb + 4,500 kg load = 18,000 kg | Assumed 60 occupants at 75 kg; not measured bus mass; P2 sensitivity/calibration |
| Battery | 350 kWh usable, initial SOC 0.8 | Initial usable energy 280 kWh = 1,008,000,000 J. No chemistry, aging, nominal/usable conversion or model accuracy claim |
| Motion | 50 km/h cap, also respecting imported road limits; acceleration 1 m/s², comfortable braking 2 m/s² | Deterministic movement setup; physical handling/safety not validated |
| Driver | tau 1 s, sigma 0 | Explicit following-model settings; no random driver variation in this baseline |
| Auxiliaries/weather | 8 kW baseline, 293.15 K ambient | Unverified energy/thermal inputs for later tests, not activated energy physics by P0 |
| Stop | One hypothetical 10 s dwell | Testable service interruption; not an imported passenger schedule or public stop promise |
| Warning | SOC 0.2 informational threshold | Does not authorize diversion or establish an appropriate operating reserve; D-12 before P4 |
| Charging | Two hypothetical points, 150/100 kW limits and assumed availability | No real station inventory; road access/approach/connector/queue/charging model validation required before P4 |

SOC means state of charge, represented as a fraction from zero to one. Wh means watt-hour; 1 Wh equals 3,600 joules. Keep source units and conversions explicit. Do not call a synthetic sensor estimate a real battery measurement.

## Montreal geography and mission

The reviewed OSM subset uses a declared bounding rectangle: latitude 45.505–45.520 and longitude −73.580–−73.560, plus complete referenced road geometry/turn-relation members. It is a dated snapshot, not live traffic or an official route. Download/import provenance and attribution are in [the data card](data-sources.md).

The project-owned mission connects four fixed geographic anchors on bus-permitted edges of the imported graph. The frozen route contains 106 ordinary edges; their lengths sum to approximately 4,268.4 m. SUMO's observed driving distance additionally includes intersection connectors; it is a different quantity. No actual transit schedule is imported. Keep route edge IDs tied to this map/import version; a replacement map needs a new route/qualification.

Source geographic coordinates use WGS84 longitude/latitude in degrees. SUMO projects them into local meter-based coordinates for movement. Record the actual projection/offset and test degrees-to-meters-to-degrees conversion. A 1 cm mathematical round-trip check does **not** establish 1 cm GPS/sensor accuracy.

The importer has known warnings about turn restrictions, junction simplification, geometry and signal/stop mapping. [Import review](../simulation/montreal/import-review.json) freezes every reviewed message and its limited acceptance. New messages fail qualification until reviewed. Current bus-class graph access is not proof of legal turns, clearance, real bus maneuverability, roadwork status or safe physical operation. Resolve/independently validate relevant constraints before representing the route as operationally suitable.

## Scenario and measurement separation

The original Montreal fixture tests movement, graph connections, stopping, coordinate conversion, continuous simulation/odometer state and exact repeatability. Its accelerated result makes no real-time pacing claim. P1-A connected four live motion channels to the backend; P1-B1 adds the assumed full measurement plant and a separately paced delivery check.

The original 20 workload example values remain rate/size examples, not a measurement source. P1-B1 generates the same 20 semantic IDs from actual simulated movement and the explicitly assumed [plant parameters](../simulation/montreal/plant.json). Voltage is constant, motor channels are equivalent aggregate proxies, thermal parameters are uncalibrated, and brake pressure is a deceleration-linked proxy. The ideal coordinate accuracy channel does not establish real GPS accuracy. Quality/provenance and source models remain visible. The reserved coolant-inlet signal stays outside the initial 20 for the extension exercise. See the [measurement guide](setup/measurement-plant.md) and [ADR-0011](adr/0011-initial-simulated-plant.md).

SUMO owns battery energy; P1-B1 enables its initial mechanism for coherent monitoring. Separate twin estimates consume declared observations. Harness truth, seeds, future demand and injected-fault labels are isolated. P1-B1 selects provisional efficiency/loss and simple thermal parameters; calibration/physical accuracy, uncertainty, sensor noise and charging dynamics remain unvalidated. Use whole-mission training/validation/held-out splits and parameter mismatch to avoid testing a model against its own hidden truth.

Model-error tolerances (D-18) must be frozen using units-based domain analysis before held-out evaluation in P2/P5. P0-06 deliberately does not invent a battery/range accuracy percentage without relevant validation data.

## Second asset and coolant inlet observation

The [second bus scenario](../simulation/montreal-bus-002/bus-parameters.json) uses an independently stepped instance of the same SUMO/dependent-plant implementation. Assumed loaded mass is 15,000 kg, usable capacity 300 kWh, initial charge fraction 0.65, voltage 600 V, auxiliary demand 6,500 W and ambient temperature 283.15 K. Speed/acceleration, stop duration and thermal parameters also differ. Its own energy state, source clock, queue, credentials and map assignment must remain isolated. Separate processes do not model interacting fleet traffic or qualify another adapter implementation.

The held-back `coolant.inlet.temperature` observation belongs only to this asset's catalog 1.3.0. Its [adapter configuration](../simulation/montreal-bus-002/sensor-extension.json) describes an externally conditioned supply boundary: initial 300.15 K, target ambient plus 10 K, response time 60 s. The exact first-order response is evaluated each completed physical step and sampled every two seconds. It does not feed back into the original pack/motor thermal equations or alter SUMO's battery accounting. The separate [thermal studies](verification/thermal-acceptance.md) evaluate cooling dynamics, fault detection, and intervention in their declared synthetic domains. Physical calibration is outside the local release. Missing/invalid intervals are source-side acceptance fixtures, not evidence of real cooling faults. See [ADR-0016](adr/0016-sensor-and-asset-extension.md), the [extension guide](setup/sensor-and-asset-extension.md) and [P1 status](monitoring-status.md) for measured scope.

## Frozen synthetic energy model

The candidate keeps independently declared 350-kWh capacity and 18,000-kg mass while actual synthetic recipes vary, using observed auxiliary power and a frozen historical motion assumption from training. The effective fitted rolling/propulsion pair 0.010 / 0.90 is not a measurement of physical constants; nearby paired alternatives fit similarly. The selected sensitivity range is empirical, not a probabilistic or guaranteed physical bound. Scoped energy/charge D-18 targets are frozen in the [release manifest](models/energy-validation-release.json); thermal and command-effect evaluations have their own release evidence, and physical validation is outside the local scope. The six reserved outcomes have now passed their unchanged frozen criteria in P2-C1; no fitting or assumption adjustment used them. The [advisory closeout](verification/energy-advisory-acceptance.md) records its product integration and supported domain.
