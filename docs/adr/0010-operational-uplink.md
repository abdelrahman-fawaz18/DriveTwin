# ADR-0010: operational observation identity and durable delivery

Date: November 3, 2026. Status: P1-A implementation; measured checkpoint results are recorded separately in `docs/monitoring-status.md`.

## Scope

Begin P1 with four actual simulated-motion readings: latitude, longitude, speed and traveled distance. The source advances the reviewed Montreal SUMO mission at a declared 0.2-second step and samples at 1 Hz. It does not fabricate the other planned readings. Battery/thermal plant behavior, the full dashboard, extended history queries, restoration and W0/W1 acceptance remain later P1 work.

## Contract family and identity

Keep qualification profile/schemas and the frozen original reader unchanged. Add simulation envelope 2.0.0 and catalog 1.0.0 in a separately selected monitoring profile. The shared Python/TypeScript validator takes a profile snapshot; default callers retain the original qualification behavior. A new major format is not silently converted into a qualification or replay message.

Topic structure is `evdt/simulation/{run}/{asset}/{source}/observations`; persistence receipts use the same identity prefix with `/receipts`. Broker permissions bind credentials to allowed topics. The envelope's identity must also match the topic, configured run and catalog.

The adapter assigns an event UUID, persistent source epoch, explicit session UUID and increasing sequence number before transmission. A restart of the sender does not re-encode or re-identify pending events. A restarted producer explicitly starts a new persistent epoch/session. One authorized producer per asset/source is the current deployment assumption; distributed producer leases are not implemented. Losing the durable identity store requires explicit new-run/reprovisioning handling, not silently restarting the same source epoch.

## Durable queue and receipt

Use SQLite on a Linux-local Docker volume for the vehicle-side queue. SQLite is an embedded database, so a vehicle does not need an additional database-server process. Queue insertion and sequence allocation commit together. Pending payload bytes are immutable and include the event identity; retries publish those exact bytes.

Use rollback-journal `DELETE` mode with `synchronous=EXTRA`, verify those settings, and serialize writers with transactions and a bounded busy timeout. This initial low-rate queue does not need WAL reader/write concurrency. SQLite documents extra directory synchronization for rollback-journal durability and a version-dependent WAL reset issue; WAL adoption requires an explicitly qualified library build and measured need. [Synchronization behavior](https://www.sqlite.org/pragma.html#pragma_synchronous), [WAL considerations](https://www.sqlite.org/wal.html)

A receipt includes run/asset/source/event identity, the SHA-256 fingerprint of the original encoded bytes, and a terminal accepted/rejected disposition. The adapter removes an event only when all these fields match a pending row; terminal detail and lifetime disposition counts update in the same SQLite transaction. Receipt detail is bounded, while lifetime counters survive detail pruning. Invalid/wrong-identity/wrong-hash receipts cannot retire data.

At the encoded queue cap, this checkpoint backpressures the simulated producer and preserves pending events. It does not yet implement P1's complete physical-source overflow/drop ledger or prove the declared 30-minute/disk-full resource guarantee. The byte limit is not a claim that SQLite file/journal/metadata overhead has been fully qualified.

PostgreSQL stores observation history, session bindings, current-value updates and the terminal receipt transactionally. The receiver publishes the receipt only after commit. Incoming broker delivery is acknowledged afterward. If commit succeeds and the receipt is lost, the sender still holds the event and retries it; the receiver returns its recorded disposition. Never wait for a publish callback inside the network receive callback. [Paho client API](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html)

Exact retry identity uses the original wire-byte fingerprint, not cross-language JSON reserialization. Same identity with different bytes is a conflict. Unsupported sample data with a valid, registered identity receives a terminal rejection; malformed or unbound identities are quarantined without an unsafe receipt.

## Ordering, freshness and authority

Current values advance by `(simulation time, source epoch, stream sequence)` within the configured run/asset/signal. A late event can join history without regressing the latest state. Resetting simulation time requires a new run. Source authorization and single-producer ownership are prerequisites; a self-reported epoch is not an authorization mechanism.

Receiver-side future-time tolerance is explicitly 50 ms for this aligned local simulation setup. The shared static schema validator does not pretend to implement wall-clock receiver policy. Current responses expose unknown before first observation, known values with age, stale after the catalog deadline, and non-good quality separately. Full paused-run semantics, multi-source arbitration and cross-host clock qualification remain P1 work.

Use authenticated broker accounts with run/asset/source topic permissions, an ingest database role unable to rewrite observation history, and a separate read-only database role. API bearer credentials have explicit asset scopes. Credentials and generated ACL/password files stay ignored. Only the read API is published to host loopback; broker/database remain private. This local fixture does not qualify TLS or remote deployment.

There are no command routes, command topics, or executor in this slice. Unsupported replay messages are rejected by this simulation profile. Complete replay features and their negative tests remain an explicit P1 gate.
