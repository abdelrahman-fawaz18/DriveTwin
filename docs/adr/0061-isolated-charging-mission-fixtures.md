# ADR 0061. Verify charging effects in isolated mission fixtures

Use the reviewed Phase 4A network, station catalog, energy forecast, and Phase 4B recommendations in an isolated SUMO experiment. Measure charging, resumption, and mission arrival from simulator observations. Retain every attempt. Report the native Stationfinder outcome separately when its own target or access to station information differs. [Mission fixture guide](../setup/simulated-charging-missions.md)

Require an explicit isolated operator fixture approval to exercise a diversion and retain a separate automatic approval flag. Validate observed input time, simulation expiry, wall lease, selected station eligibility, local protections, operator override, and lineage before fixture actuation. Preserve the Phase 3 authenticated service and registered run boundary as the integration target for durable charging admission, reservation, and effect records. The current fixture qualifies only its local guard and simulator effects.

Phase 4 entry into automatic operation requires a charging-aware registered source, runtime forecast qualification with trusted departure and station inputs, the durable authenticated charging command path, and explicit automatic dispatch approval. Phase 5 entry follows the Phase 4 acceptance results. [Project gates](../charging-policy.md)
