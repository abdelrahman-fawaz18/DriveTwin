# Important issues and fixes

This guide preserves engineering lessons after the owner-authorized retirement
of historical recordings under [ADR 0075](../adr/0075-minimal-historical-retention.md).
It records causes and implementation locations; it is not a replacement raw
measurement or a new qualification. Existing numerical reports retain their
original scopes. The complete foundation evidence remains in `evidence/p0`.

## Forecast disappears after a temporary unavailable endpoint

**Symptom:** Energy Forecast stays in fallback after an early HTTP 404 even when
the endpoint later supplies a valid result. **Cause:** the hook kept its earlier
unsupported state. **Correction:** a successful response clears that state; a
404 hides current guidance immediately while keeping historical results.
Inspect [useRouteEnergy.ts](../../apps/dashboard/src/features/forecast/useRouteEnergy.ts) and
[routeEnergyPolling.test.ts](../../apps/dashboard/tests/routeEnergyPolling.test.ts).
Check unavailable-to-success recovery before changing polling or fallback logic.

## Failed recording finalization must preserve recovery owners

**Symptom:** an archive write or source-capture failure can leave no usable saved
recording. **Cause:** final cleanup previously removed volumes after a failed
dump, or after a good dump followed by a source-copy/shutdown error.
**Correction:** write the archive exclusively, validate its format and contents,
and retain stopped service owners and volumes when finalization is incomplete.
An accepted database snapshot and complete original-source capture are separate
outcomes. Inspect [live_session.py](../../scripts/geographic/live_session.py),
[recover_run.py](../../scripts/launcher/recover_run.py), and the finalization and
recovery unit tests. Do not replace this protection with unconditional cleanup.

## Cached recovery archive may be invalid or changed

**Symptom:** recovery reports success for an existing filename that contains an
incomplete or substituted archive. **Cause:** trusting a name, header, or prior
saved flag. **Correction:** validate the archive and bind the accepted bytes to
their SHA-256; preserve invalid/partial files and the owner on failure. Inspect
[recover_run.py](../../scripts/launcher/recover_run.py). Retry only with the
existing owner and explicit archive integrity checks.

## Reopened launcher mistakes a finished trip for a running process

**Symptom:** an old run appears active after its process ID has been reused.
**Cause:** process liveness was considered before a verified terminal result.
**Correction:** a valid completed result establishes completion, while a live
owned child still takes precedence. Malformed metadata requires cleanup review
and blocks another start. Inspect [session.py](../../scripts/launcher/session.py),
[history.py](../../scripts/launcher/history.py), and
[recover_run.py](../../scripts/launcher/recover_run.py).

## Damaged saved-session pointer must not become an idle launcher

**Symptom:** a malformed nonempty pointer crashes the status response; null,
empty, or invalid JSON was silently interpreted as no previous trip. **Cause:**
loading the bookmark without validating its required ownership fields.
**Correction:** distinguish a missing file from an invalid existing file, bound
the read, validate identity/path/process fields, preserve the original bytes,
show cleanup review, and block a new start. Invalid saved-history timestamps are
also rejected without crashing the history page. Startup rejects malformed
connection receipts and cannot close an outdated service when its activity is
unknown. Inspect [launch_drivetwin.py](../../scripts/launch_drivetwin.py),
[session.py](../../scripts/launcher/session.py),
[history.py](../../scripts/launcher/history.py), and
[the repair procedure](launch-app.md#saved-session-metadata-repair).

## Interrupted scenario installation leaves an unusable destination

**Symptom:** an incomplete transfer blocks a retry or exposes a partial catalog.
**Cause:** copying directly into the final installation. **Correction:** use a
unique partial directory, verify all manifest fingerprints, then install the
directory and catalog atomically. Inspect
[scenarios.py](../../scripts/launcher/scenarios.py) and the
[portable bundle procedure](../../simulation/geographic/README.md). Keep partial
inputs out of the live catalog and never edit linked immutable inputs in place.

## Demo preparation selects an older site or modifies a linked temporary file

**Symptom:** upload preparation differs from the selected current demo, rejects
its valid external browser receipt, or writes through a reused temporary name.
**Cause:** separate latest-READY discovery and unsafe temporary-file reuse.
**Correction:** use the shared checked current selector, its matching receipt,
exclusive temporary files, and ancestry checks against redirected paths.
Inspect [current_demo.py](../../scripts/demo/current_demo.py),
[cloudflare_bundle.py](../../scripts/demo/cloudflare_bundle.py), and
[serve.py](../../scripts/demo/serve.py). Validate exact file fingerprints and the
actual semantic case before selecting a new site.

## Ordinary route progress continually triggers a new assignment

**Symptom:** many forecasts complete but periodic-refresh qualification fails.
**Cause:** the remaining route shortens as the bus advances; treating the suffix
as a new assignment repeatedly resets update scheduling. **Correction:** retain
authenticated assignment/revision identity across normal progress, reindex local
effects onto the remaining suffix, and invalidate on a real route, assignment,
or elevation change. Inspect [controller.py](../../scripts/route_aware_energy/controller.py),
the route-aware input/lifecycle code, and
[the geographic service correction](../verification/energy-geographic.md#connected-lifecycle-compatibility).

## Energy endpoint fails while serializing a trusted snapshot

**Symptom:** HTTP 500 after accepted observations exist, before browser checks.
**Cause:** a trusted internal snapshot contained values needing conversion to
JSON-compatible types. **Correction:** convert at the API boundary before
consumer decoding. Inspect
[route_energy_views.py](../../src/evdt/monitoring/route_energy_views.py), the
[wire conversion](../../src/evdt/energy/route_aware/wire.py), and snapshot tests.
Exercise a fresh route both before and after its first saved forecast.

## Repeated roads and junctions confuse stop completion

**Symptom:** reference collection rejects a valid completed stop, or assigns a
stop to the wrong lap. **Cause:** using an edge name without occurrence identity,
or comparing an incoming-lane stop offset against a junction-connector position.
**Correction:** bind stops to route occurrences and compare the stop offset on
its actual lane. Inspect the reference collectors and
[independent study development](../verification/energy-route-aware.md#independent-study-development).
Preserve wrong-lap rejection when adjusting valid junction handling.

## Short braking distance disagrees with SUMO integration

**Symptom:** a forecast is unavailable near a junction or stop although the
simulated bus completes the maneuver. **Cause:** continuous constant-deceleration
distance differs from SUMO's configured 0.2-second semi-implicit Euler position
step. **Correction:** the qualified policy declares that integration step and
uses a bounded end-of-step maneuver when required, retaining speed, stop, and
energy-conservation checks. Inspect
[policy.py](../../src/evdt/energy/route_aware/policy.py) and
[the original numerical explanation](../verification/energy-route-aware.md#independent-study-development).
Do not tune thresholds or reuse a consumed evaluation to mask a discrepancy.

## Host capacity and interrupted sampling limit workload claims

**Symptom:** fleet attempts stop on memory limits, or a bounded run fails after
missing resource samples. **Cause:** actual host headroom or missing valid
coverage, not proof of endurance. **Disposition:** retain the existing workload
criteria and safeguards; fleet endurance remains deferred. The original raw
attempts are retired, while their failed/incomplete outcomes remain recorded.
Inspect [workloads and metrics](../workloads-and-metrics.md),
[Phase 1 closeout](../verification/monitoring-acceptance.md), and the current
host-guard implementation. Cleanup does not authorize another workload or a
change to memory/storage limits.

## Command acknowledgment does not establish physical effect

**Rule:** preserve separate operator intent, executor acknowledgment, durable
one-use reservation, and independently observed effect. Interrupted handoff must
remain unresolved until recovery checks it; it cannot be claimed twice.
Inspect the [command catalog](../command-catalog.md),
[command qualification](../verification/command-acceptance.md), and
[moving-charge scope](../verification/moving-bus-charging.md#operating-scope).
Declared future charging availability is an input assumption, not authenticated
runtime state or evidence that charging occurred.

## Historical receipt loss and subsequent deliberate retirement

An earlier cleanup accidentally removed five small source-reproduction receipts;
the original report retained their hashes, and a search found no genuine local
copies. That earlier exception remains documented in
[release evidence](../release/verification.md#availability-of-original-receipts).
The later broad retirement of historical payloads is separately authorized by
ADR 0075. Neither event changes a historical pass/fail outcome or recreates the
original measurement. Preserve exact reference inventories before future
cleanup and label summary-only records explicitly.
