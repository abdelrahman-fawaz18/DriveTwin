# Recorded browser case contract

`branched-trip-demo/1.1.0` is an allowlisted semantic export. It preserves numerical readings and working-time relationships while removing runtime identities, original packets, credentials, machine information, and complete model job inputs. It is separate from the unchanged telemetry and authenticated replay contracts. [Manifest schema](../../contracts/demo/1.1.0.schema.json) · [Runtime validator and player](../../apps/dashboard/src/features/demo/demoModel.ts)

The manifest references exactly five same-directory JSON files by byte length and SHA-256: `catalog`, `map`, `common`, `charge`, and `continue`. Each file is at most 2,000,000 bytes; the combined data budget is 16 MiB and 10,000 observation frames. The browser bounds streamed downloads before parsing, verifies every fingerprint, validates both outcomes, and preloads the local map backdrop before enabling Start. A malformed or missing file leaves the clock stopped and offers Retry. Content hashes detect changed bytes; this is not a signed authenticity protocol.

| File | Fields and semantics |
|---|---|
| Catalog | Twenty ordered `{id, unit, freshness, provenance}` definitions. Values retain their original stored units; display conversions are explicit. |
| Map | Scoped GeoJSON with real OSM road geometry, passenger stops, endpoints, and simulation charging bays. Names and coordinates are allowlisted; station identities are stable public IDs. |
| Common | `frames`, `forecasts`, `progress`, `transfers`, `events`, and optional recorded `comparisons`, through working second 30. No command effect or transfer is included. |
| Charge / continue | The same collections after the checkpoint and an independently measured `summary`. Exactly these two outcomes are supported. |

A frame is `[working_s, values, qualities?]`. Values follow catalog order. Omitting qualities means every value is good; an explicit missing, invalid, or unknown value is null. Frames remain in strictly increasing working-time order. They are not averaged, retimed, or described as original packet bytes. Charts retain gaps and the original samples; export preserves the stored units and quality.

Version 1.1 requires the original numerical energy-ledger fields in every populated forecast summary. Ledger values, assumptions, and the displayed kWh conversions must agree exactly; the browser does not reconstruct missing physical terms. The decision checkpoint is an integer from 1 to 900 working seconds, currently 30. The original 1.0 contract and 20-second candidate remain readable and unchanged.

Each forecast records its model, public input and route reference, input `cutoff`, first observed presentation `available` time, trigger, status, reason, and allowlisted summary and assumptions. Availability is established after the API response by a source snapshot; this conservative observation time can be later than publication. It must never precede the input cutoff. The private audit maps public references back to original fences and input hashes. Only forecasts available by the playhead appear. Shared introductory forecasts come from the continuing recording; controller scheduling metadata is not part of the equal physical-prefix claim.

Comparison records preserve the first observed availability, interval start/end, predicted and observed net battery energy in joules, and signed error. Their observed interval must end before availability. Shared records must be available by the checkpoint; branch records must be available after it. The player filters comparisons by availability, like forecasts, without recalculating the predictor.

Progress records carry working time, observed remaining meters, and passenger-stop state. Transfer records carry observation availability, recorded stage, and cumulative measured energy delivered to the battery in kWh. Events retain their actual working times and order. The boundary state at the declared checkpoint precedes the choice: an applied command at that timestamp belongs to the selected suffix, never the common history.

Summaries contain actual trip duration, final charge, full-resolution minimum charge, energy delivered, first reserve crossing in the one-second observations, and completed status. The minimum uses the retained 0.2-second plant trace, so it can differ slightly from the minimum shown by a one-second chart. Reserve crossing timing is explicitly limited to the observation cadence.

The exporter requires exact common scalar observations and full-resolution plant steps, equal route/stop context at the checkpoint, the reviewed bay recommended by that time in both recordings, every remaining passenger stop observed as served, one guarded approval, the command/effect sequence, independently observed transfer totals, reconciled plant energy, successful trip endings, backups, cleanup, and equal source inventories. It rejects unsuitable pairs rather than adjusting tolerances or shifting events. [Exporter](../../scripts/demo/export_case.py)

Source compatibility uses the durable `source` inventory in each successful `operator-result.json`. The guarded worker consumes `launch.json` as a single-use startup admission; it is neither recreated nor required as historical evidence. Missing, malformed, or different durable source inventories remain rejection conditions.

The public data and both outcomes can be downloaded by a visitor. Causal filtering prevents accidental future display; it is not a confidentiality boundary. The private source audit, original identities, recordings, and rejected attempts remain ignored. A functional test fixture is never an accepted release case. Packaging rejects a fixture receipt and binds the actual-case browser report to both the case manifest and exact application build.
