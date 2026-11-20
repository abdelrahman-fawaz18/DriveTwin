# Route-aware operating scenarios

An operating scenario supplies explicit assumptions for roads or stops ahead. The current source still owns the assigned route, present position, and battery state. A scenario cannot move the bus, create an accepted route revision, or command charging.

## Short explanation

A construction zone can lower the assumed speed on one road. A declared surface profile can increase rolling resistance there. Passengers can change the expected mass after a particular stop. The model applies these changes at their stated location; it does not apply one local observation to every road in the trip.

## Supported inputs

| Scenario | Input | Application |
|---|---|---|
| Typical traffic | Road-class profile expanded into explicit road occurrences | Speed factor reduces that road's speed cap. It describes expected traffic, not a measured legal limit. |
| Local traffic or construction | Time-bounded conditions for a road occurrence | Movement planning uses the most restrictive overlapping speed factor. Other roads retain their selected conditions. |
| Surface, wet-road, snow, or tire study | Explicit rolling multiplier with provenance | Multiplies rolling work on the selected road. The implementation does not derive this multiplier from rain or snow labels. |
| Road closure | Closed assigned occurrence | Returns unavailable until a confirmed detour changes the assigned route. The forecast does not invent a detour. |
| Local weather | Issued weather series projected into that road's condition intervals | Wind, temperature, and pressure apply to that occurrence. Missing or ambiguous coverage is rejected. |
| Additional stop delay | Additional working seconds at a future scheduled stop | Extends dwell and equipment consumption. A current stop uses the source's remaining dwell time. |
| Passenger/load change | Declared mass on departure from an occurrence-aware stop | Changes mass at rest and retains it on subsequent roads. Maximum vehicle mass remains enforced. |
| Doors and occupancy | Door-open fraction during dwell; occupant count on departure | Feeds the declared forward equipment model. Current observed aggregate demand retains precedence over the near horizon. |
| Solar load | Declared absorbed thermal power | Enters the cabin heat balance; it is not electrical solar generation. |

`RouteScenario` holds these inputs. `RoadConditions` selects a complete condition series for one route occurrence; it replaces that occurrence's global condition series. `StopEffect` refers to both occurrence index and stop identity, so repeated visits remain distinct. Stop effects follow assigned route order. Passenger count supplies cabin heat only: loaded mass must be supplied separately to avoid an undocumented passenger-weight conversion.

All scenario records include provenance and availability time. They are part of the forecast input fingerprint. They must have been available at the forecast cutoff and refer to assigned roads/stops. Equipment models cannot be combined with an unpartitioned extra auxiliary load. The original telemetry schema and twenty measurements are unchanged.

## Interpretation

These scenarios make conditional estimates reproducible. They do not establish real-world coefficients for wet pavement, snow, urban wind shelter, traffic, or cabin heat loss. Provider-specific adapters require verified units, coverage, and meaning before activation. A declared scenario remains labeled as such.

The original route-aware development input format gains an optional scenario record before candidate freeze. Earlier development request bytes remain in their original evidence folders. The legacy released forecast and its frozen artifacts are unchanged. The new candidate's final compatibility fingerprint will be recorded at study freeze.

Focused checks cover localized speed/rolling/wind, road-class expansion, closure rejection, additional dwell, departure mass, completed boarding events, door/solar equipment demand, missing temperature, ambiguous assignments, and source-provided current dwell. Whole-trip accuracy remains part of the independent model study.
