# Operational uplink

An uplink carries readings from the vehicle to its twin. A durable queue keeps outgoing data through process restart. An application persistence receipt confirms that the central database committed a disposition; a broker delivery acknowledgment alone does not establish that.

## Current components

```mermaid
sequenceDiagram
    participant S as Live SUMO source
    participant Q as Local SQLite queue
    participant B as Authenticated broker
    participant I as Receiver
    participant D as PostgreSQL
    participant A as Read API
    S->>Q: Commit identified, encoded observation
    Q->>B: Send immutable bytes; retain pending row
    B->>I: Delivery may repeat
    I->>D: Commit history/current and receipt
    D-->>I: Commit completes
    I-->>B: Persistence receipt and delivery acknowledgment
    B-->>Q: Matching identity + wire fingerprint + disposition
    Q->>Q: Record terminal result and retire pending event
    A->>D: Asset-scoped history/current reads
```

SQLite is a database stored in local files; PostgreSQL is the central database server. This separation lets a future vehicle buffer data without deploying a central-style server on it. Both programs store data, but they have different responsibilities.

The queue uses a rollback journal and explicit synchronization. A journal supports atomic recovery of an interrupted transaction. The queue cap currently applies backpressure to the simulated producer; it preserves previously queued observations. See [ADR-0010](../adr/0010-operational-uplink.md) for exact identity, durability and remaining-capacity boundaries.

## Run the automated qualification

From Windows PowerShell in `<checkout>`:

```powershell
python scripts/linux.py --timeout 1200 -- python3 scripts/monitoring/qualify_uplink.py --evidence-dir evidence/p1/NEW-uplink
```

Replace `NEW` with a new descriptive name. The script refuses overwriting previous evidence.

1. Create ignored run-local credentials and topic permissions. No password is passed in command-line arguments or written to source.
2. Build pinned backend and optional SUMO images. The optional simulator dependency group is excluded from ordinary backend images.
3. Start uniquely named private database/broker services, apply a checksummed monitoring migration, and start the receiver/read API/sender. The API uses host loopback; the other services are not published.
4. Verify authentication, asset scope and unknown initial state. Start the live reviewed Montreal scenario, sampling four motion signals at 1 Hz.
5. Interrupt the broker/receiver while the source continues writing its queue. Restart the sender against the same queue, observe a backlog, restore services and reconcile all source events.
6. Check exact stored bytes, duplicate/conflicting identities, terminal rejection, late history without current regression, stale state, role permissions and the absence of a command endpoint.
7. Save raw private evidence and remove only the uniquely owned test resources. Preserve failed runs; never alter them to report success.

Expected final output is `passed: True` only if all checkpoint assertions and cleanup pass. Reports/logs are under ignored `evidence/p1/`; temporary secrets are under ignored `.runtime/p1-uplink/`.

The original P1-A test emitted 18 live events with four motion scalars each. Current P1-B1 qualification uses 18 live events with all 20 initial scalars, plus separately identified negative/isolation test cases, and two complete accelerated plant missions. The deliberate stopped-broker interval includes a short wait and process restart. It is not the five-minute outage or the W0/W1 benchmark required for full Phase 1. See the [plant guide](measurement-plant.md) for equations, proxies and limitations.

## Data and interface

Current live monitoring uses registered schema 3.0.0/catalog 1.2.0 and `evdt/simulation/{run}/{asset}/{source}/observations`. Historical simulation 2.0.0/1.0.0 and 2.1.0/1.1.0 remain readable. Qualification schema 1.0.0/1.1.0 and original fixtures remain intact. A source session/epoch is identity metadata, not a permission. Credentials, broker scope and immutable run policy enforce authorization. Sender/source identity is configured. [Registry/quality guide](run-registry-and-quality.md) explains the additional registration/clock acceptance and recipe queries.

The API currently provides readiness, the authorized catalog, bounded history, and current values with age/status. A value of zero is a known reading; unknown means no observation exists. Freshness deadlines turn old readings stale while keeping their last value visible. There is no product dashboard or streaming/reconnect interface in this checkpoint.

Adding a supported scalar requires catalog metadata plus a source mapping and tests. New message kinds, unsupported value shapes, hardware protection or control require explicit contracts and gates. The current source generates battery/thermal channels from its declared assumed plant; it does not fill missing measurements with zero or splice independent dataset timestamps into one alleged vehicle.

## Developer checks

`python scripts/dev.py check` runs existing qualification cases, monitoring-family cases, queue/process-exit and plant/policy/sensor tests, strict TypeScript compilation and scoped Python typing. Mypy checks program types before execution; schema validation checks actual incoming values. Neither replaces real-service integration. P1-B2 adds a real local pause, registration restart/tamper/isolation, quality/recovery, clock/session ordering and preserved historical SQL data to that integration command.

Source changes require new evidence. Actual power-loss behavior, physical media failure, the full spool/disk quota, retention, long workloads, calibrated models, complete replay/backup behavior and remote TLS/security remain separate future checks.
