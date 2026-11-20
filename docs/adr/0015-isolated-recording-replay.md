# ADR-0015: isolated, frozen recording replay

Date: November 3, 2026. Status: implementation decision; measured acceptance is recorded separately.

## Session and time semantics

P1-D1 adds a browser-local replay session created from a bounded, consistent copy of accepted observations. Every session has a new `replay_run_id` and `replay/<id>` namespace, `mode=replay`, and explicit original source run/asset/revision. It is an analysis run, not another physical-source registration. No replay record is inserted into operational history. Source packet bytes, timestamps, quality, catalog/schema and recipe references stay unchanged inside the replay package.

The chosen replay coordinate is recorded simulation time. Sort eligible accepted observations by simulation time and original source ordering; use the same nonregressing `(time,epoch,sequence)` comparison when projecting values. Late arrivals in the frozen source prefix participate retrospectively at their observation time. This is not a reconstruction of original network/commit delivery chronology. Arrival/persistence UTC timestamps remain available for inspection. Before-window seed observations may initialize state; their actual times and age remain explicit. No value is interpolated into a gap.

The playback clock is local to the page. Play/pause/rate/seek/step never change original clocks or timestamps. Advance it with monotonic browser elapsed time; pause on a hidden page or a long scheduling interruption. Seeking pauses playback. Quality and age relative to the replay clock are distinct from operational liveness. Do not label an old recording as live or infer historical source heartbeat health from sample recency. The original source pause and a user's playback pause are different concepts.

## Structural separation

A separate replay HTTP process/origin serves a distinct frontend entry. It has independent reader credentials, a PostgreSQL account with SELECT-only access to purpose-specific replay views, and database/browser networks without attachment to the operational broker/registry/source network. No broker, producer, operational-reader or administrator credential is mounted. Its route set consists of source discovery, package creation and static/health reads; no dispatch, observation ingress or source-configuration route exists. A same-origin browser content policy restricts background connections. Returning a package from POST is a read operation on the source database, not a persistent server session mutation.

Future command/executor services must retain this separation. Test direct API attempts, real database write denials, actual broker-network isolation and an independently credentialed broker observer's publish denial. Existing absence of an operational executor does not establish P3 command-effect safety; those gates remain separate. Mode flags alone are not the security boundary.

## Frozen package and compatibility

Build the package in a read-only repeatable-read transaction, pinned to a source revision. Copy window observations plus bounded prior-state seeds, only the selected asset's referenced recipes, scoped signal definitions/timings and assigned static map context. Verify original wire/recipe/context hashes and source-catalog artifact hash before use. If required historical artifacts are unavailable or inconsistent, return an explicit error identifying the missing or inconsistent artifact. Package format 1.0.0 preserves raw packets as base64 plus SHA-256; return a digest for the complete response. Browser verification checks hashes, identity, versions, finite values, ownership/order and bounds before opening a session. Hashes establish byte consistency, not independent remote attestation.

Initial limits: one configured source run per replay deployment; at most one simulation day per window, 10,000 selected/seed packets, 8 MiB original packet bytes and 16 MiB serialized package. These are explicit resource bounds, not measured W2 throughput. A loaded session is an immutable in-memory copy; later ingress or service loss cannot change it. Refresh/asset change/new session clears playback state and credentials remain page-local. Persistent session storage, package import and general archival catalog recovery are not claimed.

Use existing React, Leaflet, TypeScript and browser cryptography; add no runtime dependency. Share pure rendering/formatting where useful without bringing live-fetch hooks into replay. Show a persistent REPLAY indicator, playback controls, generic measurements, sample age/quality, past-only trend/map, provenance and empty/error/end states. The assigned route is known context; an observed future trajectory is hidden until playback reaches it.

Acceptance requires deterministic seek/step/rate/end behavior, seed and gap/quality/zero handling, no future observation display, byte/provenance preservation, frozen sessions during later ingress, asset/run/session isolation, rejected tampering, scoped readers, independent process/credential/network/database/broker negatives, real browser controls with no playback network writes, unchanged operational state, current monitoring regressions and owned cleanup. P1-D2/D3 and full performance/capacity/model/control gates remain open.
