# ADR-0073. Keep moving-charge calculation separate from actuation

**Status** Accepted for development.

The moving-bus advisor starts with the same authenticated observation and route assignment as the current Energy Forecast. Station sites, bay access, equipment limits, and active station status have their own provenance. Simulated occupancy and outages belong to a versioned run scenario. Public-map charging records supply locations for review. A site requires a reviewed bus route, compatible connector, and suitable stopping position before it enters the calculation as an offer.

The planning request and response use their own versions and fingerprints. The numerical worker may calculate a provisional option and fallback. It has no command credentials, network access, or mounted project files. Its purpose-built result is separate from the published live forecast. Scheduling must retain the existing single-worker limit, reconcile interrupted jobs, and prioritize regular forecast work.

An operator approval or an explicitly enabled automatic policy may request a diversion. The Command API owns the durable authorization and must revalidate the current plan, station, authority, progress, and deadlines before issuing permission to apply. The executor verifies the current simulator lane and stop sequence. Acknowledgment records an attempted action. Later observed route, charging, and battery data establish its effect. The legacy stationary authorization and evidence retain their original scope.

An expired, incomplete, or unavailable forecast or station offer cannot authorize a change. A route-change claim with uncertain effects stays unavailable until the observed plan can be reconciled. Keep the monitoring, replay, advisory, operator, executor, and observation roles separate.

[Implementation design and qualification gates](../design/moving-bus-charging.md)
