# Monitoring acceptance

November 4, 2026. **Phase 1 monitoring milestone complete under revised scope.**

The accepted scope is `phase1-local-monitoring/1.3.1` and host policy `phase1-local-host/1.0.1`, as approved in ADR-0037/0038. It covers local monitoring of simulated buses using short acceptance runs and explicitly compatible retained evidence. Architecture and features stayed frozen; unfinished optional bounded storage remains disabled. This does not qualify every original release target, physical accuracy, operational commands, endurance, or production deployment.

The [machine-readable closeout](monitoring-complete.json) records measured values, compatibility, earlier failures, cleanup, and the deferred gates. The [operator guide](../setup/monitoring-start-view-stop.md) explains how to start, view, and stop the existing temporary demonstration.

## Passed acceptance and requirement trace

| Requirement | Result and evidence | Boundary |
|---|---|---|
| P1-01: all initial readings reach monitoring | Twenty initial signals; compatible complete W0 data/latency/Linux-resource components retained. W1 completed ten simultaneous buses, 60 warmup + 300 measured seconds: 3,600 messages / 72,000 readings, all accepted | Original failed W0 overall outcome stays unchanged; W1 ran in the independent terminal from a dedicated PowerShell session |
| P1-02: explicit admission, quality, identity and ordering | Retained registry/clock/quality/schema negatives, current 20/21-signal admission and isolation checks, and final offline regressions passed | Local scalar contracts; later actuation and physical calibration are separate |
| P1-03: accountable outage/recovery and capacity failure behavior | Retained actual five-minute nominal single-bus outage recovered within 120 seconds; existing deterministic capacity/full-storage checks retained. Current restored queues reconcile all originals | Latest successful outage remains in its recorded opt-in nominal configuration; full-fleet outage and optional-provider deployment claims are unqualified |
| P1-04: live/history views and recovery | Current Edge and Chromium passed readings, history, JSON/CSV downloads, map, stale/disconnect/reconnect, asset switching, scoped reauthentication and replay | Functional browser checks passed; comprehensive visual regression is deferred |
| P1-05: supported sensor extension | Historical frozen-core coolant-extension proof retained; current independent buses with 20/21 signals passed generic display, ingestion, storage/history, and replay | Assumed parameterized models; no real second-vehicle calibration claim |
| P1-06: read-only observer/replay separation | Retained direct/structural denials, current scoped access/replay checks, and restored database read/replay write denials passed | Operational command execution and independent physical effect belong to later phases |
| P1-07: isolated development restore | Current small backup/restore preserved database/schema/permissions/originals and both queued backlogs. Two queues reached 30 accepted / zero pending each; 60 original messages / 1,215 readings | Coordinated stopped development checkpoint; broader deployment/disaster recovery is deferred |
| Q-04: W1 displayed-reading latency | Conservative confirmation bound: 95th percentile 0.744 s and 99th percentile 0.817 s, within 2 s / 5 s limits | Same-Linux-clock witness method; not an unbounded distributed-clock claim |
| Q-05 / Q-10: full historical queries/restore | Retained complete W2 source-backed dataset, audited queries/downloads and 109.4-second restore. All 88 checkpoint files / 648,477,603 bytes reverified; original required image remains available | Exact recorded image and compatible readers; no historical dataset regeneration or image-guard bypass |
| Q-11: warm-engine application readiness | Current functional stack ready in 8.001 s; backup source stack ready in 8.415 s, within 120 s | Images already present and engine running; broader reboot/deployment is deferred |
| Q-12: selected local resource conditions | W1 core peak about 1.82 GiB, zero observed Linux swap/OOM; local emergency guards passed. Small functional/recovery checks also passed the local guard | Full Q-12 host capacity is unpassed/deferred; 2 GiB warnings occurred during the small checks with other desktop applications active |
| Shared engineering checks | 527 Python tests, 22 dashboard tests, eight strict Python modules, TypeScript, formatting/build and unchanged contracts passed | Final shared checks ran once after code stabilized; all 314 recorded source fingerprints match |
| Cleanup and privacy | No temporary containers or new volumes remain; all 13 preexisting volumes preserved; all session sleep requests released. Candidate/staged privacy and secret checks accompany the local commit | Raw reports, credentials and backup bundles stay ignored; nothing published |

## Failed attempts remain failed

| Attempt | Recorded failure | Follow-up |
|---|---|---|
| Earlier host-memory sessions | Original reserve or emergency guard stopped those attempts | Original reports retained; no failures reclassified |
| Current backup attempt 01 | Restore/data/permission checks passed, but independent inventory found one unlabeled temporary queue volume | Overall attempt remains failed. Only that new unattached volume was removed, with a cleanup follow-up; preexisting storage was preserved |
| Current backup attempt 02 | Correction used unsupported `docker compose create --no-deps`; command rejected | Overall attempt remains failed; cleanup passed |
| Separately authorized backup attempt 03 | Passed | The corrected command passed the installed Compose dry run, followed by recovery and independent cleanup |

The correction affects only the recovery test harness. Compose now creates and owns the stopped second queue before a probe can create it without ownership labels. The test verifies ownership, refuses preexisting target resources before entering its cleanup block, and checks the exact queue name after cleanup. Fifteen focused ownership/recovery tests passed. No application runtime, wire contract, catalog, migration, dependency, workload rate, duration, or threshold changed.

## Compatibility review

All 78 controller fingerprints from W1 and the current functional check still match. Their service manifests have 212 unchanged files and one explicitly reviewed difference, `scripts/monitoring/recovery_fixture.py`. That file is used by the separate backup `--recovery` branch; it was not executed by those two accepted branches. Their application/contract/catalog/migration/Compose bytes remain unchanged. The new backup run's full 213-file manifest matches current code. Raw manifests and runtime/image guards were preserved.

The six historical query/restore runtime modules and two relevant history browser modules match their recorded evidence. All 108 relevant runtime/configuration hashes from the retained outage match. Optional storage-component results remain in their tested scopes and do not silently qualify the disabled provider for deployment.

## Deferred release gates

Phase 6 retains:

- The former 30-minute qualification, sustained-load/soak testing, and full host-capacity proof.
- Comprehensive visual regression.
- Broader reboot, deployment, and storage-lifecycle qualification.
- Automatic retention deletion.
- Production hardening beyond the active local monitoring configuration.

Full-fleet outage claims beyond the retained nominal recovery scope, inactive optional-storage deployment, and the original complete Q-12 claim remain unqualified. Real-bus calibration, energy/range/charging services and verified actuation retain their later-phase gates. No silent corruption or unauthorized control failure was deferred.

There is no remaining functional acceptance task in the approved Phase 1 monitoring scope. Phase 2 and publication were not started.
