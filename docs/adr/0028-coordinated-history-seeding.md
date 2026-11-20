# ADR-0028: Coordinated source-backed historical database fixture

Date: November 4, 2026. Status: selected; pilot and complete W2 database/checkpoint passed. Separate full W2 query/browser/restore checks also passed; see docs/verification/p1-history-acceptance.json.

## Problem and boundary

The history acceptance fixture W2 contains ten assets, 7,200 one-second events per asset and 20 readings per event: 72,000 original events and 1,440,000 scalar history rows. It represents two simulated hours. Sequential accelerated execution is permitted for this history fixture; it does not pass simultaneous live delivery, wall-time latency, a real-time soak or calibrated physical-model accuracy.

Each source must execute its own SUMO motion and dependent battery/thermal plant continuously under one recording identity. Preserve each original encoded packet, its fingerprint, registered recipe and complete physical-step trace. Do not repeat a shorter packet sequence, reset simulated distance or synthesize database rows by copying another asset's identifiers.

## Registration and loading

Register each actual recipe before generating that source's first observed packet. Registration binds the source identity, format, sensor definitions and code/input fingerprints. For this explicitly offline fixture, declare the completed simulated clock after generation; do not describe it as a live heartbeat stream. Late historical samples then enter the existing receiver repository's normal `store` operation, using their original bytes and the cached existing validator. Preserve source ownership, clock admission, original-payload hashes, receipt idempotence and normal transaction/projection behavior. No alternate schema, direct history inserts or weakened registration-time rule is introduced.

Use bounded one-source simulator and loader containers. Report complete source/container exits and absence of OOM kills. A pilot with fewer samples/assets must be labeled a pilot and cannot pass W2. Refuse nonempty output directories, preserve failed/incomplete attempts, retain exact source fingerprints and clean only the unique fixture's services/volumes. Raw evidence, credentials and any backup stay ignored and private.

Independently stream every stored original packet back from the database and compare its bytes, identity, sequence, time and fingerprint with source evidence. Reconcile all scalar rows with original packet samples, not just total counts; reconcile registered recipe bytes, current values and receipts. Check duplicate retries without changed durable state. A complete database fixture does not by itself pass query timing, full browser downloads or restore.

## Subsequent gates

Freeze the query case list before collecting timings. Q-05 retains five signals, one historical hour, at most 5,000 total plotted original points and p95 at most two seconds, with documented cold/warm cache conditions. Independently check extrema/endpoints/continuity and summaries against originals. Verify actual multipage JSON/CSV browser downloads at W2 volume.

Q-10 retains its 900-second limit for independent-cluster restore and representative reads at W2 volume. A new explicitly versioned history checkpoint may cover the database, source originals/provenance, configuration and query/integrity inventories. Preserve the existing development-backup format and its two pending queue requirements unchanged; a history checkpoint does not claim vehicle-queue or physical-plant resumption. Require fresh credentials, original-target/nonempty-target refusals, complete data/schema/permissions/sequence inventories and matching application/query results. Define and qualify that format before claiming restore acceptance.
