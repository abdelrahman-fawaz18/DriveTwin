# Workloads, metrics, and resource budgets

The local simulation release accepts the one-bus 30-minute workload and the scoped functional, recovery, history, and command checks. Ten-bus endurance, burst, outage, and queue-capacity workloads remain deferred. The current release profile maps every acceptance gate to its disposition. [Release evidence](release/verification.md) · [Release profile](../qualification/release/simulation.json)

The separate [30-minute profiles](../benchmarks/release-monitoring-capacity.json) use 60 warmup seconds plus 1,800 measured seconds. The one-bus profile passed. The ten-bus profile stopped on the host-memory guard, and its one-time diagnostic retains its separate outcome. The standard release checks retain the 2 GiB Windows launch reserve and their configured runtime guards. [Capacity review](setup/monitoring-capacity.md)

The table below preserves the original named workloads. W0 and W1 are the earlier short-run checks. W2-sized history was separately generated and restored as a fixed dataset. Fleet real-time qualification has its own workload and evidence requirements. Machine-readable arithmetic and encoding inputs are in [workloads.json](../benchmarks/workloads.json).

## Fixed workloads

Each ordinary tick produces one event per asset containing the 20 separately named scalar readings in the specification. The workload inputs are synthetic. Each live runner selects an explicit registered schema, catalog, bus identity, and source recipe.

| ID | Inputs and clock | Expected unique events | Expected scalar samples | Purpose / due |
|---|---|---|---|---|
| W0 | 1 bus, 20 signals, 1 packet/s, 120 measured wall seconds + 60 warmup | 120 | 2,400 | P1 short-run monitoring acceptance |
| W1 | 10 distinct synthetic assets, 20 signals, 1 packet/asset/s, 300 measured wall seconds + 60 warmup | 3,000 | 60,000 | P1 short-run fleet acceptance; policy 1.2.0 |
| W2 | W1 rates for 7,200 wall seconds | 72,000 | 1,440,000 | P6 real-time soak; P1 may separately seed this volume for history tests |
| W3 | 10 assets; 60 s at 1 packet/asset/s, 60 s at 5, then 120 s at 1 | 4,800 | 96,000 | P6 bounded burst/backpressure |
| W4 | W1; broker unavailable t=600–900 s, ingestion restarted at t=900 s | 36,000 | 720,000 | P6 recovery; 3,000 events/60,000 samples generated during outage |
| W5 | 1 bus; fixed-step seeded model scenarios, initial 1,200 simulated-second evaluation window | 1,200 at 1 Hz | 24,000 at 1 Hz | P2+ model/policy cases; a completed mission may end earlier and records its actual duration |

W1 is 10 messages/s and 200 samples/s. W3's burst is 50 messages/s and 1,000 samples/s. Derived estimates, status, acknowledgments, commands and retries are separately counted overhead, not quietly included in these baseline totals. Fleet instances are synthetic load fixtures, not ten validated real bus models.

Run a declared 60-second preparation/warmup outside the listed measurement duration, with separate IDs/counters. Wait for dependencies and source readiness before starting the measured window. Preserve planned/actual duration, seed, source/configuration versions, scheduling lag and achieved rate. Do not shorten a wall-time test by accelerating simulation. Transport workloads go through the normal adapter/spool/broker/ingestion path; direct database insertion is allowed only for the explicitly labeled P1 history-volume fixture.

## Measurement rules and acceptance

A percentile is an ordered cutoff: p95 means 95% of measurements are at or below it. Use nearest-rank `ceil(p*n)`, not the mean of per-window percentiles. Empty/invalid measurements fail. Missing event measurements fail; they are not removed to improve latency. Record the complete distribution/count, minimum/maximum, window and errors.

| Target | Frozen method |
|---|---|
| Q-01 coverage | Every registered initial scalar has catalog/API/display/history coverage; held-back `coolant.inlet.temperature` is outside the initial 20 and added through catalog/adapter/tests only |
| Q-02 accounting | For legitimate unique generated event identities: G = durable accepted + terminal quarantined + explicitly dropped + pending. Pending reaches zero after recovery. Repeated deliveries and conflicting reuse of an accepted ID have separate counters; a rejected conflicting delivery does not change the original event's disposition |
| Q-03 spool | At W1, at least 1,800 seconds per asset with encoded events no larger than 8,192 bytes; no intentional loss inside that declared envelope. Actual disk/WAL/index behavior must be tested, not inferred from encoding arithmetic |
| Q-04 latency | Fault-free W1 source-to-DOM availability p95 <= 2 s and p99 <= 5 s. Correlate every event; each batched scalar inherits the event's availability latency, not an independent transport observation. A missing update after 10 s fails the run |
| Q-05 history | W2-sized store; one asset, five signals, one-hour window, at most 5,000 plotted points total, p95 <= 2 s. Freeze query mix and cold/warm-cache cases separately. Preserve extrema with labeled reduction; raw export is a separate paginated endpoint/test |
| Q-06 freshness | All initial 1 Hz numeric signals initially use a 5 s observation-age deadline; show stale within one additional 1 s refresh. Connection state and source wall/real-time liveness are separate; paused simulation is visible as paused |
| Q-07 recovery | After broker restoration and ingestion readiness, drain the W4 backlog within 120 s while fresh live data remains visible. Include duplicates/rejections and reconcile identities; report source age separately |
| Q-08/Q-09 commands | P3+ independent effect and durable duplicate/restart ledger tests; replay/unauthorized requests cannot reach the operational executor. No command traffic is enabled by these workload specifications |
| Q-10 restoration | Isolated W2-sized backup/restore and representative queries within 15 min; identify backup recovery point and missing interval |
| Q-11 startup | <= 120 s from application start to dependency readiness with Engine running/images present; cold engine startup is a separate measurement |
| Q-12 resources | Core application cgroup memory <= 6 GiB; Windows available physical memory cannot stay below 2 GiB for >30 consecutive seconds; zero out-of-memory kills. Record whole WSL overhead and browser memory separately |

DOM means the browser's rendered element tree; an assertion verifies that the identified reading is present. Record the rendering method and its scope: DOM availability does not measure a human noticing a display or a physical screen's response.

### Clocks

Use monotonic elapsed intervals within one process/clock domain. For cross-process/Windows–Linux timestamps, measure clock offset/error bounds before and after the run using bracketed exchanges. Maximum admitted uncertainty: 50 ms. Larger uncertainty makes cross-clock latency **inconclusive**, not passed. Compare latency targets against percentile plus the uncertainty bound. Never subtract independent monotonic-clock origins or simulated seconds from UTC audit timestamps.

Store observation, receipt, persistence, projection and browser-availability stages with correlation identity. Observation age in simulation time and wall transport delay are separate metrics. For paused/accelerated scenarios, publish speed factor and clock semantics; W5 does not pass Q-04.

### Memory and swap

Sample host available memory each second. For containers record cgroup `memory.current`, anonymous/cache breakdown, limit and OOM counters without summing the same parent/child cgroup twice. Include simulator/adapter/broker/backend/database in the core budget. Browser, Engine and WSL kernel/cache overhead remain visible in separate measurements and whole-host headroom.

Linux swap-in plus swap-out must not exceed 64 MiB in each of three consecutive 60-second windows. Measure counter deltas, not total allocated swap; counter resets invalidate that interval. Windows paging counters are reported separately because disk-backed file paging is not automatically swap. These are initial acceptance limits to implement/measure in P1/P6, not claims about the current idle host.

## Spool and retention

A spool is a local durable queue: it keeps the exact event bytes until the server confirms persistence. The example packet measures 2,558 UTF-8 bytes. The declared worst-case W1 packet budget is 8,192 bytes, below the portable wire-size ceiling of 65,536 bytes. At 1 Hz, 30 minutes requires at most 14,745,600 encoded bytes per asset; ten assets require 147,456,000 bytes. This arithmetic is a lower bound on disk requirements, not a durability test.

Per asset allocate a 32 MiB encoded telemetry limit inside a 64 MiB telemetry disk budget; reserve another 8 MiB for critical status/results. Validate database/index/write-ahead-log overhead and filesystem-full behavior in P1. Write-ahead logging (WAL) records changes durably before ordinary storage is updated, so queue disk use exceeds message bytes. The 30-minute guarantee applies only to the declared nominal payload/rate; bursts have a separately measured horizon.

Beyond the guarantee, if necessary evict the oldest unacknowledged telemetry with durable event-ID/count/interval loss accounting and an overflow indication on reconnect. Never silently discard command results. If the critical reserve is exhausted, refuse new command acceptance and expose the fault. These are required future policies, not implemented spool behavior.

The original storage planning budgets are recorded below. The release implements a read-only retention preview, with automatic deletion disabled. [Retention preview](setup/retention-preview.md)

The planning limits use **age or byte quota, whichever is reached first**:

- Raw history: at most 48 hours and a 32 GiB database budget. Actual oldest retained time is visible; no guaranteed 48-hour window at every payload size.
- One-minute trends: at most 90 days and 8 GiB. Retain extrema/first/last/mean/count and gap/quality coverage; do not fill missing data with invented values.
- Project disk planning cap: 100 GiB, including histories, spools, images, selected diagnostics and isolated backups. Preserve at least 20 GiB host free disk; stop heavy data generation before violating it.
- Keep selected release evidence under a separate reviewed quota. Retention first supports dry-run reports and protects release artifacts. Automatic deletion is outside the release scope.

This replaces the main plan's earlier proposed 30-day raw retention. At W1, message overhead and database/index/WAL growth can make that unsuitable for the development machine. Record measured database bytes/event, index/bloat, backup space and achieved history window in P1/P6; revise limits through an ADR before reevaluation if needed.


## E2-B selected measurement implementation

Each measured run retains its declared workload, warmup, and numerical targets. [ADR-0020](adr/0020-observation-stage-and-resource-measurement.md) selects actual event-stage/attempt/DOM/resource collectors. When a global Windows/Linux offset is inconclusive, primary timing uses source-ready to server receipt of a request issued after verified DOM availability, both on the qualified Linux monotonic/boot domain. This is a conservative upper delay including confirmation overhead. No cross-domain subtraction or optimistic overhead correction is used; compare percentile upper bounds to the unchanged latency limits. Keep the strict cross-clock diagnostic inconclusive when its 50 ms rule fails. Record local resolution, confirmation overhead, complete identities and clocks separately. Short rehearsal results do not replace full W0/W1 or the W2 fixture.

## Monitoring workload duration

Specification 1.2.0 and policy `phase1-short-run/1.2.0` implement the user's bounded closeout request: W0 60 + 120 seconds and W1 60 + 300 seconds. Former 60 + 1,800-second qualifications move to Phase 6. W2 history volume, Q-05's one-hour query, all other quality/resource requirements and old evidence outcomes remain unchanged. Reject stale half-hour/hour profiles and any undeclared shortening. See [ADR-0037](adr/0037-monitoring-acceptance-scope.md).
