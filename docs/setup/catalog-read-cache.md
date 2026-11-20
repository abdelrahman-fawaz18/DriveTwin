# Reusing validated signal definitions

The signal catalog is the dictionary that says what a reading means: its name, unit, allowed range and component. For example, `motion.speed` uses meters per second. A live value changes every second; its definition normally stays the same for the whole registered run.

Previously, every dashboard snapshot rebuilt the complete family of catalog/schema validators. A validator checks that data follows its declared rules. Offline profiling measured about 0.20 seconds just to construct that family. Repeating it for ten views every quarter second wastes work even when all definitions are identical.

## What the cache keeps

A cache stores a previously checked result so it can be reused. This one stores only validated static catalog definitions in the current application process. It never stores live observations, credentials, freshness decisions, future model values or permission decisions. The existing database connection budget is separate: that controls how many requests can open database connections at the same time.

Every read still selects the asset's registered version and checks the exact original catalog fingerprint. SHA-256 is the fingerprint used here; changing even whitespace changes it. The cache key includes the exact profile/catalog content, selected version and fingerprints of all schemas used to check it. Changed inputs cannot silently reuse the previous result. On a cache miss, the existing Contract validator checks the definitions. A dependency change during validation is refused.

The cache holds at most eight entries. All ten buses using the same catalog can share one validated entry. Each response receives a deep copy: a separate copy of every nested definition. If a caller changes its copy, it cannot change a later reader's unit, source list or signal range. Adding a supported catalog version follows the existing reviewed metadata path; it does not require an asset-specific cache branch.

## Reproduce the checks

Run the commands from `<checkout>` in Windows PowerShell.

1. Run `python scripts/dev.py check`. It builds the pinned development image and checks the implementation, tests, contracts, types, formatting and publication boundaries.
2. After that succeeds, profile the component with:

   ```powershell
   python scripts/linux.py -- docker run --rm --network none --memory 256m --entrypoint python evdt-development-checks:local scripts/monitoring/profile_catalog.py
   ```

   `scripts/linux.py` runs the command inside Ubuntu; `docker run` creates an isolated temporary process from the already built image. `--rm` removes that temporary container after it exits, `--network none` prevents external connections, and `--memory 256m` bounds its memory. The command does not start a database or bus simulation.
3. Read the JSON report. `original_family_construction` measures the repeated work removed from the old read path. `new_view_first_call_s` includes first validation; `new_view_warm` measures repeated complete catalog read views, including fingerprint checks and copies. `definitions_identical` must be true. The output includes counts and source fingerprints.
4. These component timings measure validator construction and reuse. Live display latency, event accounting, and host resources have their own [release evidence](../release/verification.md).

The cache tests check repeated reads and equivalent definitions, changed catalog bytes, profile/schema invalidation, invalid changed schemas, version/scope rejection, a dependency changing during validation and cross-reader mutation isolation. The runtime guard keeps historical review manifests unchanged and records the exact catalog-view change separately in `qualification/workload/catalog-view-core.json`.

Connection admission preserves both existing calling styles. A `with connection()` block automatically commits or rolls back and closes the driver connection. A direct caller can instead use `conn = connection()`, execute/commit explicitly and close in a `finally` block. Both use a real driver connection. Its admission ticket returns the capacity once when the connection closes; calling close twice cannot create an extra slot. Tests exercise direct callers as well as concurrent context blocks and connection/transaction/close failures.

The implementation is in `src/evdt/monitoring/asset_catalog.py`. `for_policy` owns selection, fingerprint verification and per-response copies; `validated_catalog` owns bounded reuse after the original validator succeeds. Observation, source-clock and recipe admission still use their existing paths. See [ADR-0023](../adr/0023-catalog-view-validation-cache.md).
