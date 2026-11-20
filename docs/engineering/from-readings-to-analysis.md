# From stored readings to forecasts and decisions

A current reading, a historical summary and a forecast have different sources. The bus supplies observations. PostgreSQL stores and summarizes them. The Energy Forecast calculates a future under explicit assumptions. A command requires separate authority.

## Observation storage

The Vehicle Adapter saves original packets in SQLite before the Sender publishes them. Ingestion commits the packet, per-signal history, current state and storage receipt together in PostgreSQL. The Monitoring API reads those records with scoped permissions. History aggregation groups past readings for a bounded query; it does not replace original packets or manufacture future values. [Delivery trace](engineering-walkthrough.md#durable-delivery-through-an-outage) · [History queries](../../src/evdt/monitoring/history_queries.py)

## Route-aware forecast

The trusted input builder joins authenticated route/progress context with matching admitted observations and an approved assignment. The assignment supplies prepared roads and elevation, vehicle ratings, update policy and declared forward conditions. Current state and future assumptions retain distinct provenance.

The controller commits a durable job claim and sends one bounded JSON request to an isolated worker. That worker plans movement and integrates battery energy; it has no database credentials, simulator access or future outcomes. The writer accepts a result only for the matching claim and input generation. Immutable results retain their input and model fingerprints. The Monitoring API rechecks current validity separately from serving saved history. [Forecast trace](engineering-walkthrough.md#forecast-input-provenance) · [Model and interfaces](../models/route-aware-energy.md)

For example, 20% charge with a declared 350 kWh capacity initializes 70 kWh. A forecast of 25 kWh net use would leave 45 kWh at arrival. That arithmetic alone does not establish whether charge dipped below a 35 kWh reserve earlier in the journey; the route-aware ledger tracks that minimum explicitly.

## Advice and actuation

The moving-bus advisor calculates candidate driving legs using the route-aware worker and combines them with station and charging assumptions. Advice cannot divert the bus. An operator approval or separately enabled automatic profile must pass current offer, scope, station and deadline checks before a durable one-use grant reaches the executor. Acknowledgment records an attempt; later charging and movement readings establish effect. [Charging trace](engineering-walkthrough.md#charging-approval-through-observed-effect)

The original template-based forecast, concurrent worker harness and stationary Charging Advisor remain historical or separately configured paths. Their database records and evaluations retain their original meanings. [Legacy energy example](legacy-energy-example.md) · [Stationary charging](../verification/stationary-charging-acceptance.md)
