# ADR 0068. Defer W1-30 and advance single-bus integration

Date November 6, 2026. The ten-bus endurance workload is deferred following repeated host-memory stops.

## Decision

Defer the official P6-B1 W1-30 execution. The two official attempts stopped during warmup on the Windows memory guard. The one-time 1 GiB diagnostic reached about 8 minutes 50 seconds of measurement before its own memory guard stopped it. Keep those three outcomes and the passing W0-30 result. Retain the consumed diagnostic claim and the diagnostic harness on its own local branch. [One-bus pass](../verification/one-bus-monitoring-review.json) · [First attempt](../verification/fleet-monitoring-first-attempt.json) · [Plugged-in attempt](../verification/fleet-monitoring-powered-attempt.json) · [Diagnostic](../verification/fleet-memory-diagnostic-review.json)

Advance the independent P6-C1 single-bus charging and thermal fault work. Its entry depends on the verified single-bus monitoring path and relevant component evidence. Its first live acceptance still requires a dedicated isolated adapter, a reviewed run recipe with one actuator owner, exact fault timing, independent effect observations, and passing host checks. The test uses operator-only charging and a fixture-scoped cooling request. Existing automatic dispatch authority stays within its previously approved profile. [P6-C1 entry review](../setup/charging-and-cooling-checks.md)

Keep P6-B2 fleet burst, outage recovery and queue capacity, and P6-B3 fleet soak as separate planned workloads. The present host needs readiness evidence before their live execution. Offline preparation, reuse of the sealed history data, and smaller independent checks may proceed.

## Release consequence

This changes the execution order. The frozen workload definitions and 2 GiB host rule remain in force. Full-duration fleet latency and host capacity remain open under Q-04 and Q-12. Phase 6 full acceptance and final Phase 7 release sign-off still require the applicable mandatory evidence. Documentation and demonstration preparation may proceed with claims limited to verified scope. Data integrity and command authority remain mandatory.
