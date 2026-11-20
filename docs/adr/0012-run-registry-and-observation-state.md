# ADR-0012: registered sources, clock declarations and quality states

Date: November 3, 2026. Status: implementation decision; actual acceptance is recorded separately.

## Authority and immutable provenance

A trusted deployment policy fixes a run ID, simulation mode, allowed asset/source identities, nonoverlapping signal ownership, current catalog/schema, input/code fingerprints, cadence/freshness and heartbeat deadlines. Freeze its raw bytes in PostgreSQL at provisioning. Changing policy under an existing run fails; create a new run for changed inputs. A producer cannot grant itself signal ownership.

A private registration service uses producer credentials and a dedicated database role; the read API retains its read-only database role. Before observations, register the exact source manifest bytes and their SHA-256, identity/epoch/session, time floor and declared sampling metadata. Registration validates them against the provisioned policy. Same bytes are idempotent; conflicting bytes/epoch/session or unauthorized ownership fail. Recipes expose reproducibility metadata and hashes, not hidden seeds, injected-fault schedules or future plant truth. Full source files remain separately versioned inputs; this is not remote attestation or a complete artifact archive.

Observation 3.0.0 requires a manifest fingerprint and permits explicit quality/null-value semantics. This is a breaking format change; preserve 2.0.0/1.0.0 and 2.1.0/1.1.0 as historical readers, without rewriting bodies. Current live ingress requires registered 3.0.0. Catalog 1.2.0 preserves existing signal IDs/units and adds quality declarations. Historical formats remain byte-frozen.

## Sessions, history and conflicts

One configured owner per run/asset/signal. A source's first registered epoch is 1; replacement requires the next epoch, a new session and a declared simulation-time floor at or above all prior declared/accepted time. Retire the previous epoch at that floor. Pending old-epoch observations may join history at or below its retirement boundary; they cannot regress current state. The existing plant cannot restore physical state after restart, so its restart still requires a new run; controlled protocol tests exercise authorized session continuation separately.

Bind every current packet to registered run/asset/source/epoch/session/manifest/catalog/schema. Reject time reset, ahead-of-declared-clock time and crossed sequence/time ordering. Out-of-order delivery is allowed when neighboring sequence values are time-consistent. Exact persisted-byte retries return their recorded result without duplicate history. Serialize registration, clock changes and ingress per source using a transaction-scoped database lock. Preserve committed history and receipts atomically; roles cannot rewrite them.

The selected lock lifetime follows PostgreSQL's transaction-level advisory-lock behavior; application code uses the same source key across these paths. [PostgreSQL locking reference](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS).

## Clock and freshness

Producer clock declarations are metadata, not vehicle commands. They have immutable event identity, consecutive revision, timestamp and `running`, `paused` or `finished` state. Repeated exact declarations do not refresh liveness. Reject conflicting/stale revisions, expired/future timestamps, backward simulation time, advancement while paused, retired sessions and transitions out of a finished session. A new authorized epoch can continue after its predecessor ends.

The driver reports a real local pause without stepping the plant and emits fresh paused heartbeats. Resume continues the same simulation instant and adjusts wall pacing. Observation simulation time must not exceed the last authenticated declared clock. Wall UTC, simulation time and monotonic elapsed pacing remain separate. Clock deadlines use UTC on the qualified aligned local host; distributed clock accuracy is not qualified here.

Expose source state, sample quality and freshness separately. Unknown means no observation. Good numeric zero remains valid. Missing/invalid have null current value and explicit reason; invalid may preserve finite raw input for audit. A newer invalid/missing reading replaces current quality, while history retains earlier good readings. Never present an old good value as the new reading.

For a healthy deliberate pause, a previously eligible reading is `paused`; already stale simulation data stays stale. Expired paused heartbeats become disconnected/stale. Resume alone does not make an old UTC reading fresh. A finished source does not remain live forever. Registered per-signal period/freshness drives the API, including cadence overrides. History records late disposition metadata; UI gap rendering remains P1-C.

## Sensor layer and acceptance

Add configurable deterministic bias, Gaussian noise, quantization and declared missing/invalid intervals to the source sensor layer. Keep seeds/fault schedules private. Good out-of-range values become explicit invalid samples, not silently clipped values. Nonfinite input cannot be encoded as JSON NaN/Infinity. Unit/range/schema failures on supposedly good wire data remain admission errors.

Required evidence: schema parity and frozen historical readers; immutable registration/authentication/tamper/conflict tests; epoch/time-reset/retired backlog/ordering and ahead-clock tests; recipe/run discovery scope; quality/missing/recovery and noisy-sensor tests; actual pause/heartbeat/resume and heartbeat-loss stale behavior; existing 20-signal outage/restart and complete plant repeatability; restricted database-role negatives; fresh-source fingerprints and owned cleanup. This closes P1-B only within local simulated monitoring scope, not dashboard, full workloads, physical calibration, restoration or actuation.
