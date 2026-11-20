# ADR-0016: prove a held-back signal and independent second simulated asset

Date: November 3, 2026. Status: implemented and locally qualified; measured acceptance is recorded in [D2 evidence](../verification/signal-and-asset-extension.json).

P1-D2 closes the scalar-extension exercise and adds a genuine second parameterized bus model. The previous controlled bus-002 producer tested protocols and browser states; it did not step another vehicle plant. This checkpoint must operate two independently stepped SUMO processes, each with its own source identity, battery state, dependent thermal state, immutable recipe, queue, transport principal, timing and map assignment. Different load, capacity, voltage, speed/acceleration and thermal assumptions must produce different physical traces; an identity-only relabel is insufficient. Both use the reviewed Montreal road network. Separate processes do not model interacting fleet traffic or prove fleet capacity. All physical constants remain assumed, not manufacturer specifications or calibrated bus accuracy.

## Honest extension boundary

The current system has explicit historical format readers but several operational paths still assume catalog 1.2.0. First prepare generic per-asset, policy-bound catalog/format selection. Preserve old artifacts byte-for-byte; accept only explicitly registered format pairs, never arbitrary future versions. HTTP discovery must make mixed catalogs explicit and expose an asset-specific catalog. Snapshot/history/exports must use the selected asset's original registered definitions. Replay's format checks follow the same reviewed metadata registry. Source publishers select their declared format; their durable queues cannot silently change identity/version.

Record the preparatory commit and freeze shared ingestion/storage/read/API/contract-engine/frontend/migration code before introducing the held-back signal. The extension can add adapter/model code, catalog/schema/profile metadata, scenario configuration and qualification code. Acceptance compares the frozen shared files and records the exact boundary. Do not claim that the pre-D2 system already supported every version without changes. Future core changes require a reviewed new extension baseline and requalification.

## Held-back observation

Add `coolant.inlet.temperature`, in kelvin, only to the second asset's new catalog. Initially model a separately conditioned coolant supply boundary with a first-order response to an assumed ambient-relative setpoint. This is a declared external boundary, not a closed coolant circuit, independently calibrated sensor, inferred fault label, or competing battery-energy integrator. It does not change the existing pack/motor equations. State the initial temperature, setpoint offset and response time explicitly; verify the analytic step response, finite bounds and reproducibility. The optional adapter extension owns this signal; shared storage and UI must not name it.

Use an independent two-second cadence and source-side missing/invalid intervals for acceptance. Preserve quality, diagnostic raw values and original timestamps. Future circuit coupling and calibration require a distinct physical-model decision and validation. No control or maintenance-diagnostic accuracy claim follows.

## Acceptance

Both actual sources must deliver original packets through authenticated MQTT, persist all expected samples and retain separate clocks/queues/recipes/maps. Verify exact catalog membership, source ownership, unit/version mismatches and forbidden cross-asset reads/publications. In both real browsers, prove the added signal's live/current/history/export/replay behavior, quality gaps, units, sensor location/model evidence and absence from the first asset. Switching assets must clear previous queries and positions. Prove frozen shared-code fingerprints, historical compatibility, independent numerical repeatability/energy checks for each parameter set and owned cleanup. Run the existing monitoring/replay regressions after shared preparation. Full workloads, backup/restore, hardware, independent adapter implementation, physical accuracy and command safety retain later gates.
