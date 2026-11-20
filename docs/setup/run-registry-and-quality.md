# Registered runs, source clocks and sensor quality

This is P1-B2. The twin now stores the recipe associated with each source session and checks incoming readings against it. A recipe is a run manifest: versioned metadata, input/code fingerprints, source identity, timing and model assumptions. It does not expose private random seeds, injected-fault schedules or future plant truth.

## Why registration comes first

Suppose `bus-001` sends a temperature reading. A valid number and unit are insufficient. The receiver must also know which source owns that signal, which run produced it, whether its session is registered, which recipe it references, and whether its simulation time is consistent.

```mermaid
sequenceDiagram
    participant O as Trusted provisioning
    participant P as Producer / simulated vehicle
    participant R as Private registry service
    participant D as PostgreSQL
    participant I as Observation receiver
    participant A as Read-only API
    O->>D: Freeze run policy and source permissions
    P->>R: Register exact manifest bytes / source session
    R->>D: Validate and save immutable registration
    R-->>P: Committed recipe fingerprint
    P->>R: Authenticated clock / heartbeat declarations
    R->>D: Accepted clock-event audit and current clock
    P->>I: Existing durable MQTT path / observation 3.0.0
    I->>D: Verify registration, time and ownership; commit result
    A->>D: Read authorized recipes, current values and history
```

A policy is the trusted deployment configuration: allowed assets/sources, signal ownership, input/code hashes, schema/catalog versions, cadence and deadlines. Its bytes are frozen when the run is provisioned. Changing it requires a new run. The source cannot register itself as the owner of another source's signals.

A SHA-256 fingerprint identifies exact bytes. Returning a registered recipe preserves those bytes, so a reader can verify the fingerprint. A fingerprint is not a signature or proof that an untrusted physical device ran that software. Sources are authenticated within this local setup; remote attestation and a complete downloadable source-artifact archive are outside its scope.

## Separate read and write permissions

| Process | Permission | Deliberate boundary |
|---|---|---|
| Provisioning/migration | Create schema, freeze reviewed run policy and grant database roles | Trusted local setup; not a public registration endpoint |
| Private registry service | Register an authorized producer's recipe and clock declarations | Producer token is restricted to one run/asset/source |
| Observation receiver | Append accepted history, rejections and receipts; update current projection | Cannot rewrite registered recipes or clocks |
| Read API | Query authorized asset measurements and recipes | Read-only database role; no registration or clock mutation route |

The registry has no published host port. Only the read API is exposed on host loopback. Credentials are generated under ignored `.runtime/`, mounted as files and excluded from source/builds/public results. This qualifies local authentication and role boundaries, not TLS or remote deployment.

Registry persistence across restart is checked after the bounded live source finishes. If metadata becomes unavailable during production, bounded exact-byte retries eventually stop the simulated producer. Automatic checkpoint/resume of the bus plant is not implemented; sender/data-path outage recovery is a separate tested behavior.

Useful HTTP routes are below. HTTP is the request/response protocol used by these APIs. `GET` reads information; these `POST` routes submit producer metadata. Clock declarations report local simulation state; they do not command the vehicle or pause an external asset.

The registry reads bounded raw request bytes, then explicitly validates the versioned JSON Schema before using them. Reading a raw request does not provide automatic framework validation. [FastAPI request reference](https://fastapi.tiangolo.com/advanced/using-request-directly/).

| Service | Route | Caller / result |
|---|---|---|
| Registry | `POST /v1/registrations` | Scoped producer submits raw recipe; receives its committed fingerprint |
| Registry | `POST /v1/clock-events` | Active producer declares its clock state with ordered revision and identity |
| Read API | `GET /v1/run` | Reader sees registrations for its authorized assets only |
| Read API | `GET /v1/recipes/{hash}?asset_id=...` | Reader retrieves the exact registered manifest bytes |
| Read API | `GET /v1/current?asset_id=...` | Current sample, quality, freshness, source state and registered timing |
| Read API | `GET /v1/observations?asset_id=...` | Bounded history with original versioned payloads |

## Sessions and consistent history

A session identifies one producer execution. A persistent epoch identifies its generation. The first epoch is 1. Registering its exact recipe again is safe and does not replace anything. A different recipe under the same epoch is a conflict.

A replacement uses the next epoch, a new session ID, and a declared simulation-time floor at or above previous declared/accepted time. An older session is retired at that boundary. Its delayed queued readings may still enter history at or below the boundary. They cannot move current state backward. Sequence numbers and simulation time must have consistent order, even if delivery arrives out of order.

The current bus driver cannot restore physical model state after a producer crash, so restarting that driver still requires a new run. Restarting the independent delivery sender preserves queued identities and remains supported. Controlled protocol tests exercise authorized session continuation; they do not demonstrate restoring the bus plant itself.

Source-specific transaction locks serialize registration, clock and ingress changes, including repeated rejected data. A transaction is an indivisible database operation; commit makes its result durable. The lock prevents two workers from making contradictory decisions about the same source. Independent sources can still proceed separately. Previous observations/receipts remain immutable under application roles.

Observation time cannot predate its registration beyond the aligned local clock tolerance, exceed the receiver's future UTC allowance, precede the session's simulation floor or exceed authenticated declared simulation time. Exact retries return the original committed accepted/rejected receipt; a later time change does not reinterpret the same bytes.

## Paused, disconnected and finished

A heartbeat is a fresh small declaration that the source is alive. Each declaration has a new event ID, consecutive revision, timestamp, session, recipe fingerprint and simulation time. Exact retries do not refresh liveness. Conflicting/reordered revisions, stale/future timestamps and backward simulation time are rejected.

`running` permits clock advancement. `paused` holds simulation time while fresh heartbeats continue. Resume starts at that same instant and adjusts real-time pacing; it does not run a catch-up burst. `finished` ends that session. An older retired session cannot change the active clock.

The qualified local heartbeat deadline is five seconds. If paused heartbeats stop, the source becomes disconnected and its readings stale. UTC is used for audit/liveness on this aligned local host; simulation seconds describe plant evolution; monotonic elapsed time controls waiting. Distributed clock accuracy and suspend/resume remain separate checks.

## Quality is separate from freshness

| Condition | Value / quality | Freshness and meaning |
|---|---|---|
| No reading yet | No observation / unknown | Unknown; never invent zero |
| Valid zero | Numeric `0` / good | Fresh if timing/liveness permits |
| Expected observation unavailable | Null / missing, reason `dropout` | May be recently reported, paused or stale |
| Sensor reading failed checks | Null / invalid, explicit reason; optional finite raw value | Preserve quality independently of freshness |
| Healthy deliberate pause | Keep the recorded sample and quality | Paused only if it was eligible when pause began and remains eligible in simulation time |
| Reading was already too old at pause | Keep its history | Stale; a pause cannot resurrect it |
| Heartbeats lost | Keep the recorded sample and quality | Disconnected/stale, including during a pause |
| Resume without a new reading | Keep the recorded sample and quality | Old UTC readings remain stale until replaced |

The API returns `quality`, `freshness`, `source_state`, wall/simulation age and registered timing separately. `status` is a convenient combined field; a UI must also show freshness when quality is invalid/missing. A newer invalid sample sets current quality to invalid. The earlier good value stays in history. Earlier good values remain in history.

Current observation envelope 3.0.0 requires `manifest_sha256`. This is a major change because missing/invalid samples may have null value and an explicit reason. JSON is the text data format used for messages; null means no numeric value. Good samples must be numeric, within catalog range and without failure-only fields. Invalid may preserve `raw_value`; missing cannot. Nonfinite numbers are never encoded as JSON NaN/Infinity; NaN means “not a number.” Wrong units or out-of-range supposedly good packets are still rejected by ingress.

Historical 2.0.0/catalog 1.0.0 and 2.1.0/catalog 1.1.0 remain readable through explicit registries and byte guards. Current catalog 1.2.0 preserves the same 20 IDs/units. Old bodies are not silently upgraded or rewritten. Live ingress requires the registered current format. The SQL upgrade check applies actual migrations around a stored old-format packet and verifies its original bytes/default metadata survive; it does not qualify an uninterrupted live deployment upgrade.

## Configure sensor imperfections

The source [sensor layer](../../src/evdt/monitoring/sensors.py) supports per-signal bias, Gaussian noise standard deviation, quantization interval, and missing/invalid time intervals. Bias shifts a reading; noise adds reproducible random variation; quantization rounds to a declared step. Missing takes precedence over invalid when configured intervals overlap. Intervals include the start and exclude the end.

The seed and fault schedules are experiment inputs, retained privately or in the configured scenario. The public registered recipe exposes allowed sampling metadata and input fingerprints. Server policy pins the actual signal periods/freshness; inconsistent or hidden extra sampling fields fail registration. Default live/full-mission runs remain ideal sensors. Unit checks exercise configured imperfections, while controlled sensor outputs traverse the real receiver for missing/invalid/recovery acceptance. These noise/proxy parameters are not calibrated error distributions or a complete GPS model.

## Reproduce

Run the registry checks in Windows PowerShell from `<checkout>`.

```powershell
python scripts/dev.py check
python scripts/linux.py --timeout 1200 -- python3 scripts/monitoring/qualify_uplink.py --evidence-dir evidence/p1/NEW-registry
```

Use a new `NEW` label. The first command runs source, type, privacy, frozen-compatibility and Python/TypeScript outcome checks. The second provisions an isolated policy/credentials/database, registers the actual simulated producer, tests a real seven-second local pause with heartbeats, then interrupts delivery, recovers the queue and restarts the registry. Controlled positive/negative cases check scope, binding, time, quality, concurrency and roles. It also verifies historical SQL upgrade and two complete repeatable plant missions.

Expected final output is `passed: True` with exit zero only after assertions and owned-resource cleanup pass. Raw policies, credentials, rejected diagnostics and source artifacts stay ignored. Sanitized results are separate from targets. The complete product UI, replay/extension/restore, five-minute outage, physical quota/retention, full workloads, calibration and actuation retain their later gates.
