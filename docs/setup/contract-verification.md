# Shared data-rule verification: Python and TypeScript

P0-04 checks that the two implementation languages agree on what data is valid. [Acceptance](../verification/README.md) passed for the declared scalar qualification profile: **339 shared cases**, including 116 expected acceptances and 223 expected rejections, with no discrepancy. The real data-flow and browser paths were requalified after the receiver changed.

## What is a contract?

A **data contract** defines the agreement between a sender and a receiver: required fields, types, units, identifiers, versions, and error behavior. For example, this sample excerpt means a battery state of charge of 71%:

```json
{"signal_id": "battery.pack.soc", "value": 0.71, "unit": "1"}
```

This is one sample inside a larger envelope, not a complete message. `1` is the unit for a dimensionless fraction. A value written as text (`"0.71"`), a fraction outside 0–1, or a `%` unit is rejected by this profile. Sensor-specific unit conversion belongs in a declared adapter, not an implicit guess in the receiver.

**JSON**, JavaScript Object Notation, describes the text format. **JSON Schema** describes rules for JSON data. A schema's **dialect** identifies the edition of that rule language; we use Draft 2020-12. A **profile** selects the subset and additional constraints our application actually supports. A JSON document can be syntactically valid but outside the application profile. [JSON standard](https://www.rfc-editor.org/info/rfc8259/), [Ajv schema dialect support](https://ajv.js.org/json-schema.html)

The checks have three layers:

| Layer | Question | Example failure |
|---|---|---|
| Decode and portability | Can both implementations read the bytes safely and unambiguously? | Duplicate JSON keys or an unsafe large counter |
| Envelope/profile schema | Are required fields, types, version, identity spelling, and time format valid? | Missing event ID or unsupported schema version |
| Catalog/context | Is the source registered, and does the signal use its declared unit/range in the correct run/topic? | Battery SOC sent in kelvin or a different run namespace |

The [profile registry](../../contracts/profile.json), [portable schema](../../contracts/portable.schema.json), [envelope versions](../../contracts/README.md), [catalog schema](../../contracts/catalog.schema.json), and [actual catalog](../../catalog/reference-signals.json) are shared inputs. Signal names and ranges are not independently copied into the Python and TypeScript receiver logic.

## The two implementations

The actual Python ingestion entry point, [contracts.py](../../src/evdt/contracts.py), delegates to [contract_profile.py](../../src/evdt/contract_profile.py). It uses the already-locked Python `jsonschema` library and explicit format checks. Existing ingestion imports and transactional storage behavior remain intact.

**TypeScript** adds development-time type checking to JavaScript. The compiler checks our implementation and translates it to JavaScript. That does not make incoming device/API data trustworthy: a sender can still supply the wrong value while the program runs. **Ajv** performs runtime JSON Schema validation in [the TypeScript package](../../packages/contracts-ts/src/index.ts); **ajv-formats** supplies date-time/UUID format validation. Validation does not coerce strings into numbers, insert defaults, or remove unknown fields. [Ajv options](https://ajv.js.org/options.html), [format checks](https://ajv.js.org/packages/ajv-formats.html)

The package compiles with strict TypeScript settings, including checks for unchecked indexed access and optional-property handling. Public validated values remain `unknown`; generating the complete dashboard's domain model types is not claimed by this step. The validator has no filesystem/browser dependency: the caller supplies a bundle of trusted schemas/profile/catalog. The test loader reads that bundle from this repository.

**jsonc-parser** is Microsoft's JSON scanner/parser. Although it supports comments in other applications, our configuration rejects comments and trailing commas. Its parsed tree lets us detect duplicate keys before normal `JSON.parse` would discard that information. A **parser** turns text into structured data; its tree retains the structure of object properties. [Parser documentation](https://github.com/microsoft/node-jsonc-parser)

Exact package versions are locked in [package-lock.json](../../packages/contracts-ts/package-lock.json): Ajv 8.20.0, ajv-formats 3.0.1, jsonc-parser 3.3.1, and TypeScript 6.0.3. Node.js 24.18.0 ran the TypeScript side on Windows. Python 3.12.16/jsonschema 4.26.0 ran the real receiver in Ubuntu's container runtime. The Python helper explicitly imports `/work/src/evdt/contracts.py` from the mounted current source; it does not accidentally exercise the older module baked into its reusable tooling image.

The TypeScript library runs in Node.js for cross-language contract verification. The browser regression separately checks that the updated receiver serves the expected data.

## Why the portable limits matter

The [numeric diagnostic](../verification/README.md) measured a concrete difference: Python read `9007199254740993` exactly, while JavaScript read `9007199254740992`. Therefore a schema saying only “integer” is insufficient for a shared counter.

JavaScript's normal number representation is **binary64**, a 64-bit floating-point format. Fractions use finite precision; sufficiently large consecutive whole numbers cannot all be represented exactly. The profile rejects integral values outside ±9,007,199,254,740,991, and sequence counters additionally must be nonnegative. This follows the JSON standard's interoperable exact-integer range. It does not promise arbitrary-precision decimal arithmetic or identical Python/TypeScript serialization. [RFC 8259 numerical interoperability](https://www.rfc-editor.org/info/rfc8259/)

The profile also defines:

| Rule | Purpose |
|---|---|
| Maximum 65,536 bytes | Bounds each incoming message; byte limits differ from character counts |
| Maximum 32 nested containers | Prevents excessive nesting from overwhelming parser/validator recursion |
| UTF-8, without a leading BOM | One declared character encoding; UTF-16 and malformed byte sequences are rejected |
| Unique JSON object keys | Avoids ambiguous messages such as two different `value` fields |
| Valid Unicode scalar values | Rejects incomplete character encodings; accented text and complete emoji remain supported |
| Finite numbers and safe integral values | Rejects overflow/infinity and precision-unsafe counters |
| Canonical UTC time shape | Uppercase `T`, trailing `Z`, real calendar date, ordinary seconds, and at most six fractional digits |
| Hyphenated UUIDs and strict identifier ends | Rejects prefixed/extra-character IDs and trailing-newline ambiguity |

**UTF-8** is an encoding that maps Unicode text to bytes. A **BOM**, byte-order mark, is an optional file marker some editors add; this wire profile rejects it. A **UUID**, Universally Unique Identifier, is the structured identifier used for events, runs, and sessions. Checking UUID spelling does not prove uniqueness; database constraints and event identity rules handle repeated delivery separately.

UTC is the shared time reference already used in our evidence. Timestamp shape follows a restricted **RFC 3339** profile: six fractional digits match microsecond database precision; leap-second spellings, lowercase separators, offsets, and year zero are outside this qualification profile. This is a documented subset, not a claim to accept every representation permitted by the broader standard. Future clock/freshness/energy policies remain separate decisions.

The original envelope schema is unchanged. **The new portable admission profile intentionally narrows some values the earlier broad schema/reader accepted.** Compatibility means the supported common subset, not every historical schema-valid number, encoding, or timestamp. The change is explicit in ADR-0006 and was followed by service regression checks. No stored observation was converted or rewritten.

## Version evolution and the old reader

The current envelope is **1.1.0** and the previous supported envelope is **1.0.0**. Version numbers identify the agreement; a number alone does not make an older program understand a new version.

Version 1.1.0 adds only optional informational `annotations`: at most eight named string entries, each at most 256 Unicode code points. Required measurement fields and units retain their meanings. Unknown extra top-level fields and unsupported minor/major versions are rejected.

The [frozen historical Python reader](../../qualification/contracts/legacy_contract_1_0.py) is an exact copy of the previously qualified implementation, retained solely as a test baseline. It is not a second active implementation. Its source and the original 1.0.0 schema were checked against the earlier fingerprints.

| Producer data | Current reader | Frozen 1.0 reader |
|---|---|---|
| Portable 1.0.0 observation | Accepts | Accepts |
| Portable 1.1.0 observation | Accepts | Rejects directly |
| 1.1.0 explicitly encoded for the old reader | Accepts the resulting 1.0 envelope | Accepts |
| Unknown version | Rejects | No automatic migration |

Both languages provide an explicit compatibility adapter. It validates input, copies it, removes only optional annotations, sets the target envelope version to 1.0.0, and validates the result. The frozen reader actually consumed output bytes from **both** adapters. Acceptance also compared every remaining field with the original input.

Choose the target encoding **before first publication** and retain those bytes for retries. Do not transform an already persisted event and resend its identifier with different content; that can correctly produce an identity conflict. The adapter does not rewrite database history, infer unknown version meanings, or grant command permissions. It is not evidence of direct forward compatibility by the original strict reader.

## What the 339 cases cover

[The deterministic vector generator](../../qualification/contracts/build_cases.py) defines explicit expected outcomes. Comparing languages alone would be insufficient because both could share a bug. Each language must also match the independent expected acceptance/rejection and reason.

The corpus includes valid current/previous messages; missing fields; wrong roots/types/units; registered-source and topic/run mismatches; malformed dates/UUIDs; numeric boundaries and overflow; duplicate keys/signals; unsupported versions; Unicode/encoding cases; exact/over byte limits; nesting boundaries; invalid catalogs; configuration snapshots; optional annotations; and explicit version adaptation. A further 200 seeded examples exercise valid and invalid SOC ranges.

A held-back coolant signal first fails as unregistered, then succeeds after only a catalog addition. Both validators work without a sensor-specific core edit. Catalogs are validated and copied at construction; mutating the caller's catalog afterward does not silently change an active instance. This is not a hot-reload or authorization mechanism. Managed catalog registration/reload remains future integration work.

## Reproduce from Windows

Run from PowerShell in the repository root with the Ubuntu launcher, Docker Engine, and Node.js 24 available.

1. Run the language comparison with a new output directory:

   ```powershell
   python scripts/platform/qualify_contracts.py --evidence-dir evidence/p0/my-contract-check
   ```

   This installs the exact npm lock, compiles TypeScript, generates the fixed corpus, runs TypeScript, then runs the current Python receiver and frozen-reader compatibility checks inside an ordinary-user, network-disabled container. Reports include the compiled-code fingerprint, packages, actual Python import path, outcomes, and discrepancies. Nonzero exit or `passed: false` is failure.

2. Inspect `qualification.json`, `cases.json`, `python.json`, `typescript.json`, and `commands.log`. Success requires 339 results per language, zero discrepancies, and the declared legacy compatibility outcomes. This is reproducible evidence on tested versions, not a mathematical proof over all possible inputs.
3. If receiver/schema code changed, rerun the actual data-flow check into a new report:

   ```powershell
   python scripts/linux.py --timeout 900 -- python3 scripts/platform/dataflow.py qualify --report evidence/p0/my-dataflow-regression.json
   python scripts/platform/accept_dataflow.py --dataflow-report evidence/p0/my-dataflow-regression.json --report evidence/p0/my-p0-02-acceptance.json
   ```

   The second command checks the engine and SUMO source fingerprints against their existing qualification.
4. Requalify browser access against that specific passed receiver baseline:

   ```powershell
   python scripts/platform/qualify_browser.py --dataflow-report evidence/p0/my-dataflow-regression.json --evidence-dir evidence/p0/my-browser-regression
   python scripts/platform/accept_browser.py --evidence-dir evidence/p0/my-browser-regression --prerequisite evidence/p0/my-p0-02-acceptance.json --report evidence/p0/my-p0-03-acceptance.json
   ```

   The browser helper now accepts explicit prerequisite paths and defaults to this project's current qualified P0-04 receiver baseline. Earlier evidence remains immutable; it is not silently treated as current after source changes.

The [P0-04 acceptance exporter](../../scripts/platform/accept_contracts.py) reconciles the final language evidence with the recorded P0-02/P0-03 regression reports, verifies current source/artifact fingerprints, checks the frozen baseline and unchanged old schema, verifies required-field preservation, and saves compiled-code evidence. Its regression report paths identify this recorded qualification checkpoint; a future checkpoint should update those inputs through review and run the affected checks.

## Results and limits

The [final contract report](../verification/README.md) passed all 339 cases. The [data-flow regression](../verification/README.md) passed its existing 20 unit tests, real broker/database/query checks, two application starts, persistence, and normal cleanup. The [browser regression](../verification/README.md) passed visible Edge and automated Chromium with no skipped/flaky/unexpected results. [P0-04 acceptance](../verification/README.md) verified 63 source fingerprints and the retained report/corpus/compiled artifacts.

This profile covers numeric, good-quality, synthetic qualification telemetry. Geographic/GPS records, other modes, derived estimates, bad-quality/missing values, events, waveforms, command formats, VSS mapping, live dashboard integration, security, and a production registry are not claimed. Broader standards/catalog/architecture decisions remain in P0-05 and later phases. Python/TypeScript validation parity does not imply identical cross-language hashing or serialization. No new system service, global runtime installation, agent switch, or data/history migration was needed.
