# ADR-0023: Content-bound validation cache for catalog read views

Status: selected after measuring a repeated read-path cost; live qualification pending.

The snapshot read path constructs a complete monitoring contract family every time it selects an asset's catalog. A small offline measurement recorded about 0.20 seconds per construction. Ten live views requesting snapshots every 250 ms repeatedly compile the same immutable schemas, consuming capacity that should serve observations. Connection admission bounds database sockets but does not remove that repeated CPU work.

A cache keeps a previously checked result for reuse. Select a bounded, process-local cache for the read view's validated catalog only. Its key includes the exact profile bytes, selected catalog bytes, selected catalog version and fingerprints of every schema used by the existing contract validator. Read and check the original registered catalog fingerprint on every call. Changed content creates a different key and must be validated again. Return a deep copy so a caller cannot mutate definitions shared with later readers. Bound the cache to eight entries.

Continue using the existing Contract validator for first validation; do not introduce a permissive alternate validator. Check the registered version and dependency fingerprints around validation. Preserve original scope, signal ownership, units/ranges and reader errors. Observation/recipe/clock admission, durable writes, ordering, wire formats and immutable schema/catalog bytes remain unchanged. No cache contains a live observation, credential, hidden model state or future data.

Keep historical guards and add an exact reviewed successor for the asset-catalog view change. Required checks include cold/warm equivalent definitions, returned-copy isolation, changed original-byte rejection, profile/schema cache invalidation, invalid changed schemas, all existing contract outcomes and affected real-service/browser regressions. Profiling improvement is distinct from passing the 60-second warmup plus 1,800-second W0/W1 benchmarks. All quality thresholds remain unchanged.
