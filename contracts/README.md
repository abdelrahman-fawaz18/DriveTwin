# Scalar telemetry contract registries

Phase 3 starts with a separate versioned speed limit request and execution result for the local simulator command. Python checks shape, bounds, trusted target and issuer, and two validity windows. The effect observer reads speed measurements independently. [Request schema](commands/speed-limit-1.0.0.schema.json) · [Result schema](commands/execution-result-1.0.0.schema.json) · [Command guide](../docs/setup/local-speed-limit-command.md)

There are two explicitly selected families. The original qualification family below remains the default for its existing consumers. The [monitoring profile](monitoring.profile.json) selects current [simulation envelope 3.0.0](telemetry/3.0.0.schema.json), [catalog schema 1.2.0](monitoring.catalog-1.2.schema.json) and [20-signal catalog 1.2.0](../catalog/monitoring-signals.json). Historical [2.1.0](telemetry/2.1.0.schema.json)/[catalog 1.1.0](../catalog/monitoring-plant-1.1.0.json) and [2.0.0](telemetry/2.0.0.schema.json)/[catalog 1.0.0](../catalog/monitoring-motion-1.0.0.json) remain readable. Neither family silently admits the other; unknown pairs are rejected. Live ingress additionally requires registered current-format context.

Monitoring [persistence receipts](receipt.schema.json) bind run/asset/source/event identity to exact wire bytes. A receipt reports a committed disposition, not actuation authority. [ADR-0010](../docs/adr/0010-operational-uplink.md) explains delivery. Current-format [run manifests](run-manifest.schema.json) and [source clock declarations](source-clock.schema.json) use producer-only registration and immutable audit; [ADR-0012](../docs/adr/0012-run-registry-and-observation-state.md) defines server context checks. Required top-level `manifest_sha256` is authoritative in 3.0.0; annotations remain informational. Good samples are numeric; missing/invalid are null with explicit reason, and only invalid may retain a finite raw value.

The current 60 cross-language simulation cases include old/current catalogs, quality states, malformed combinations and admission-profile bounds. They do not claim TypeScript parity for receipt/metadata HTTP formats, which have Python schema/real-service checks, or full Phase 1 workload acceptance. Historical artifacts are guarded by [monitoring fingerprints](../qualification/monitoring/immutable.json); original qualification fixtures have a separate immutable guard.

## Original qualification family

The [profile](profile.json) selects the current and previous supported envelope schemas, shared portable constraints, catalog schema, and decoding limits. Both Python and TypeScript load these files. The receiver's public entry point remains `evdt.contracts.Contract`; TypeScript's implementation is in `packages/contracts-ts`.

| Item | Purpose |
|---|---|
| [1.0.0 envelope](telemetry/1.0.0.schema.json) | Original immutable qualification envelope |
| [1.1.0 envelope](telemetry/1.1.0.schema.json) | Same required fields, with optional informational annotations |
| [Portable constraints](portable.schema.json) | Canonical UTC timestamp shape, UUID spelling, safe sequence counter, strict identifier boundaries |
| [Catalog schema](catalog.schema.json) | Registered asset/source lists and scalar unit/range definitions |
| [Signal catalog](../catalog/reference-signals.json) | Actual initial assets and measurement definitions |

Validation requires the selected envelope **and** portable schema **and** catalog/context checks. The profile also requires strict UTF-8 JSON, unique object keys, bounded nesting/payload size, valid Unicode scalar values, and finite/safe integral numbers. The portable profile is an explicitly narrower supported subset of the original envelope schema. Do not interpret the untouched original schema alone as the complete current admission policy.

The current reader supports 1.0.0 and 1.1.0 within this profile. The frozen original reader rejects 1.1.0 directly. Explicit pre-publication conversion to 1.0.0 removes only optional annotations and changes the version field; unknown versions are rejected. Never use conversion to rewrite history or silently change already transmitted event content. Choose one encoding before first delivery and retain its bytes for retries.

`annotations` is optional, allows at most eight lowercase-named string entries, and limits each value to 256 Unicode code points. It does not define commands, permissions, units, measurement quality, or required operational state.

Original envelope 1.0.0 and the frozen reader were verified byte-for-byte against their earlier qualification fingerprints. No stored data was rewritten. See [the explained guide](../docs/setup/contract-verification.md), [ADR-0006](../docs/adr/0006-portable-contract-profile.md), and [P0-04 acceptance](../docs/verification/README.md).

This is the qualification-only scalar/good-quality/synthetic profile. Fleet/component standards mapping, other modes, estimates, events, waveforms, commands, operational identity/authorization, retention, and a production schema registry remain separate work.

## Registered signal extension

The [monitoring profile](monitoring.profile.json) now explicitly maps per-asset operational tuples. The initial source keeps envelope 3.0.0/catalog 1.2.0/source-run 2.0.0. The second modeled asset uses [envelope 3.1.0](telemetry/3.1.0.schema.json), catalog 1.3.0 and [source-run 2.1.0](run-manifest-2.1.schema.json). Prior files remain byte-frozen; the convenience default remains the initial tuple. Trusted policy chooses the actual asset's versions. Schema/catalog and recipe dispatch must select an explicitly supported entry from the registry.

The held-back scalar is adapter/catalog metadata; shared storage and browser paths use registered definitions. Replay package 1.0.0 supports both reviewed tuples. Sixty-eight simulation outcomes matched across Python/TypeScript, including all prior 60 and eight new extension cases. Metadata HTTP validation is separately exercised in Python/real services; no metadata TypeScript-parity claim follows. See [ADR-0016](../docs/adr/0016-sensor-and-asset-extension.md), [the guide](../docs/setup/sensor-and-asset-extension.md) and [acceptance record](../docs/verification/signal-and-asset-extension.json).
