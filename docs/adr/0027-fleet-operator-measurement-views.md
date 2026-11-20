# ADR-0027: One fleet operator with ten simultaneous asset views

Date: November 4, 2026. Status: selected; actual resource/latency qualification pending.

## Scope and decision

W1 measures ten simultaneous assets, each producing all 20 initial signals at one event per second, with every event checked in an actual dashboard view. It does not specify ten independent human users. Earlier harness attempts created ten browser storage contexts using the same fleet-wide reader principal. They failed this laptop's unchanged host-memory reserve. Preserve their original policies and failed results.

Select `fleet-operator-tabs/1.0.0`: one operator, one browser storage context, ten separate concurrent asset pages. A browser context holds settings, connections and storage for a browsing session. A page is a separate tab with its own JavaScript state and displayed document. The harness opens every page at a distinct loopback port bound to the same API container. An origin is the combination of scheme, hostname and port. Distinct origins give each tab a separate browser connection pool and origin-local storage while avoiding ten separate operator profiles. Without distinct origins, persistent HTTP/1 event streams can exhaust the browser's per-origin connection limit before all ten pages connect.

The aliases are fixture-only `127.0.0.1` bindings, derived from its base port plus 100 and the asset index. They do not alter the product's routes, authentication, database, protocol, signal rate, port configuration or UI. Preserve the pinned full Chromium/Edge browsers and ordinary launch defaults, sandbox and site isolation. Do not assert a guaranteed renderer-process count or infer a memory saving from the design alone: actual physical memory measurements decide acceptance. Chromium distinguishes origin/site, document groups and process allocation; see its [process-model design](https://raw.githubusercontent.com/chromium/chromium/main/docs/process_model_and_site_isolation.md).

## Verification

Bind the policy, context count and asset-to-origin map in the profile, fixture, service report and browser report. Each page may request only its own origin. Before sampling, prove origin-local storage isolation using a removable noncredential marker; require empty local/session storage and cookies after connection and at completion. Authentication stays in request headers and memory. Cookies are scoped to the host across its ports, so the empty-cookie requirement matters; this design does not claim cross-port cookie isolation or separate user identities.

Keep every per-asset DOM value/unit/identity check, all source originals and byte fingerprints, every clock exchange and event witness, resource samples, native viewport and final/failure screenshots. A single complete private HTTP Archive covers the operator context. Backend scoped-role tests remain separate and unchanged. All counts, duration, p95/p99, clock uncertainty, sample-gap, OOM, application-memory, host-reserve and swap requirements remain unchanged. The original two-browser functional suite retains its own existing contexts and tracing.

Rehearsal is 60 warmup plus 30 measured seconds and cannot claim W1. Full attempts remain exactly 60 warmup plus 1,800 measured wall seconds. No one-hour Phase 1 run is authorized. A passing result would qualify this declared one-operator arrangement, not ten independent users or a browser-memory extrapolation.
