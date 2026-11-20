# ADR-0006: portable scalar contracts and explicit version adaptation

Date: November 3, 2026 (America/Toronto)

Status: P0-04 passed for the numeric qualification profile. [Acceptance](../verification/README.md) records 339 agreeing cases, frozen-reader compatibility, and requalified P0-02/P0-03 service paths. This selects a bounded part of D-08; full vehicle-signal/asset/capability/standards profiles remain open.

## Decision

Use the same versioned JSON Schema 2020-12 documents, catalog, and explicit portable admission profile in Python and TypeScript. Python keeps its existing public receiver entry point and delegates to a profile module. The private TypeScript package provides runtime validation without filesystem/browser coupling, strict compiler checks, and an unknown-valued public result that callers validate before use.

Pin Ajv 8.20.0, ajv-formats 3.0.1, jsonc-parser 3.3.1, and TypeScript 6.0.3 in a separate npm lock. Reuse the existing Python dependency lock and Python 3.12.16/jsonschema 4.26.0 tooling. Both schemas and catalog/context semantics must pass. Disable data coercion/default insertion/unknown-field removal. Trusted schemas are local, not downloaded from message contents.

## Portable admission is an explicit narrowing

The measured [numeric diagnostic](../verification/README.md) demonstrates loss of an exact large integer on the JavaScript side. The profile bounds integral values to the shared safe range, requires finite numbers and strict UTF-8 without BOM, rejects duplicate keys/unpaired surrogates, caps size/nesting, and restricts time/UUID/identifier spelling. Calendar/format validation remains enabled in both languages. See [the profile](../../contracts/profile.json) and [guide](../setup/contract-verification.md).

The original 1.0.0 schema is **unchanged**. The first portable admission profile deliberately accepts a narrower subset than the original broad qualification reader, including numeric/timestamp/encoding restrictions. This is a recorded P0 compatibility change, not a claim that every previously schema-valid value remains admissible. There is no deployed production migration or stored-data rewrite. Future profile changes require another explicit compatibility decision and qualification; do not silently edit the meaning of a published schema or unit.

## Version behavior

Keep immutable envelope 1.0.0 and add 1.1.0 with only optional informational annotations. The current reader accepts both under the portable profile. Unknown versions fail closed. The original strict reader rejects 1.1 directly; a schema version number alone is not a negotiated upgrade protocol.

Provide explicit pre-publication annotation-only adaptation to 1.0.0. The historical Python reader was frozen byte-for-byte from its previously qualified source, verified against its prior fingerprint, and read actual output from both adapters. All fields other than version/optional annotations were compared with original input. Select encoding before first transmission; preserve encoded bytes for retries. No automatic adaptation is performed during ingestion, no history is rewritten, and transformed content must not masquerade as an unchanged duplicate.

This satisfies current/previous-minor tests through the documented supported subset and explicit forward-compatibility path. It does not claim direct acceptance of new-version messages by old binaries or general automatic major-version migration.

## Extensibility and validation evidence

The same corpus tests 116 expected valid and 223 expected rejected messages in each implementation. It covers schema/catalog/context checks, parser/Unicode/numeric boundaries, configuration validity, sensor registration through catalog changes alone, version negotiation limits, and adapter behavior. Expected results are explicit; agreement alone is not treated as correctness. Two hundred seeded range examples supplement named edge cases. Catalogs are copied at construction; arbitrary hot reload is not implemented.

Because receiver behavior changed, the actual broker/store/query fixture and both Windows browsers were rerun. Normal cleanup passed. Engine and independent SUMO code were unchanged and their existing evidence reused after source-fingerprint verification. Browser prerequisite selection is explicit so outdated code fingerprints cannot be silently accepted as current.

Package metadata declares MIT for Ajv, ajv-formats, and jsonc-parser, and Apache-2.0 for TypeScript. The [rights inventory](../rights/README.md) records the source-release review. Original DriveTwin work now uses the [MIT license](../../LICENSE).

## Remaining boundaries

P0-04 is complete within the initial scalar profile. D-08 still needs vehicle-standard mappings, component/capability detail, other message kinds and modes, and production profile/catalog governance. D-06 still needs project-wide tooling/CI decisions. No full dashboard type generation, TypeScript/Python canonical-hash equivalence, operational authorization, live-streaming, physical calibration, workload, or full Phase 0 claim follows.
