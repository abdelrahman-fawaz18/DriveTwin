# ADR-0071: Route-aware Energy Forecast

Date: November 12, 2026. Status: implemented as an opt-in development version. The one-bus connected check and independent accuracy evaluation on three reserved synthetic routes passed. [Status record](../verification/energy-route-aware.md)

This record preserves the original November 12 timing and scope decision. The current live geographic profile uses a 120-working-second period; later dashboard and moving-charge work has separate verification. See the [current capability matrix](../release/capabilities.md) and [operating guide](../setup/route-aware-energy.md). The original 30-second results below retain their recorded scope.

## Decision

Implement a separate version of the Energy Forecast using the ordered remaining route and current accepted state. Preserve the original known-route, 120-working-second model and its consumed evaluation. OpenStreetMap remains the road source. Versioned terrain data enriches route geometry with elevation and data-quality information.

Separate route preparation, movement assumptions, numerical energy calculation, scheduling, and persistence. Use the existing application and deployment tools. Keep the numerical worker isolated from databases, simulator state, future outcomes, and credentials. SUMO remains the battery-state authority.

The first forecast runs when required inputs are ready. Periodic updates are due every 30 working seconds; confirmed route changes and material condition changes can request earlier updates. One active worker and bounded pending work limit resource use. Durable scheduling claims, generation checks, and restricted append-only writes prevent duplicate or obsolete results from becoming current.

The version 2.1 trusted input selects the newest complete reading/context pair within one forecast period of the current source clock. The current authenticated context must confirm the same source, registration, route, remaining stops, and noncharging state. All real-life freshness checks still apply. Forecast age uses the actual source clock; input delivery lag never changes a reading's timestamp. This prevents asynchronous observation delivery from starving forecasts at accelerated trip speeds. [Input admission](../design/energy-forecast-inputs.md)

Vehicle and environmental factors enter through versioned declared profiles or accepted observations with source, units, time, and coverage. Unsupported factors remain explicit. Advanced uncertainty calibration and dashboard redesign are deferred. The current Energy page receives only the changes required to display the new version and lifecycle correctly. Charging control retains its existing qualification and model.

## Verification

The [specification](../design/energy-forecast-v2.md) defines checkpoints EF-01 through EF-11, independent route-family evaluation, analytical references, factor comparisons, and acceptance criteria. Mathematical verification, service integration, forecast accuracy, and real-world data coverage have separate results.

Use small offline tests before bounded sequential one-bus checks. Preserve host safeguards. A host-memory stop leaves that check pending with a reproducible external procedure. It does not weaken the gate or invalidate independent completed checks.

## Consequences

The new model can accept different routes without a route-specific speed template. Its accuracy still depends on vehicle parameters and the quality of future movement/environment assumptions. More frequent calculation cannot reveal an unobserved future incident. New route, environment, and scheduling interfaces require explicit compatibility and access-control verification before release activation.
