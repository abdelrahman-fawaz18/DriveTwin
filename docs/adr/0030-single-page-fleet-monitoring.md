# ADR-0030: A generic fleet measurement page

Date: November 4, 2026. Status: implemented and functionally qualified in both browsers; short ten-bus data/latency/instrumentation passed but overall rehearsal failed host-memory reserve. Full W0/W1 remain unpassed.

## Purpose and decision

A fleet operator needs to see the measurements of several authorized buses together. Add a Fleet page alongside the existing selected-vehicle overview, map, measurements, history and operational pages. Derive its asset list from the authorized catalog and each panel's signals, units, locations and model/provenance details from validated snapshots. Do not hardcode bus IDs, a twenty-signal production limit or particular sensor names. Keep all selected panels' actual rows in the document; do not replace readings with summary cards or virtualize away unmeasured rows.

The previous one-operator/ten-tab live rehearsal reconciled all events and values but failed the unchanged Windows available-memory reserve. Preserve that failed result and ADR-0027's historical policy. A single useful fleet page is a new declared viewing arrangement; its memory saving must be measured, not assumed.

## Reading and failure boundaries

Use the existing authorized `/v1/snapshot` route. Two concurrent workers refresh a cycle at a nominal 250-millisecond interval without overlapping cycles. The selected-vehicle stream is stopped while Fleet is active. No new backend route, schema, catalog, database migration or dependency is introduced. Requests carry credentials in headers and retain no browser storage. Bound responses to eight MiB before JSON parsing, abort a read after 3.5 seconds, and stop all owned requests when the view unmounts or scope changes.

Validate asset, recording, revision, sample quality, units and metadata before replacing that asset's state. Reject backward revisions and wrong-asset/run results. Failed reads preserve last known values with disconnected/stale presentation. A separate 4.5-second watchdog prevents a hung read from retaining live presentation indefinitely. Network loss marks every panel disconnected; successful validated snapshots recover individually. Any 401/403 ends the session and removes its panels. Reauthentication rebuilds the asset list from the new credential. The asset selection remains page-memory state.

These are monitoring snapshots and may coalesce updates under load, just as the original selected-asset stream can. Durable history remains complete. Qualification does not excuse coalescing: every required one-second event must still be observed with every original value/unit and an authenticated post-display witness, or the attempt fails.

## Exact qualification policy

Select `fleet-operator-table/1.0.0`: one fleet reader, one ordinary browser context, one page, all workload assets simultaneously selected at the same fixture origin. Asset/run/source attributes and per-asset panel boundaries establish document isolation. Empty cookies/local/session storage and reader-scope denials remain required. This does not claim independent-user or browser-origin isolation; those are different properties. Retain the old tab harness and evidence under their original policy.

Keep action traces and the private full content-omitted HTTP Archive, all per-asset initial/periodic clock exchanges, every post-document witness, original packet hashes, final/failure screenshots and complete per-asset event records. Preserve all source, broker, receiver, database, count, latency, clock, resource, paging and sample-gap requirements. W0/W1 remain exactly 60 warmup plus 1,800 measured wall seconds; rehearsal remains 60 plus 30. W1 remains ten simultaneous assets, twenty signals each, one event per asset per second. No one-hour Phase 1 benchmark is introduced.

Keep historical review layers immutable and add an exact successor review for the main interface and style sheet. New feature modules and all harness/helper bytes enter captured source fingerprints. Require shared checks, both-browser twenty/twenty-one-signal and scope/offline/recovery checks, and fresh matching-source live qualification. E3 history acceptance remains its recorded earlier-image result until an explicit compatible-image upgrade/requalification is performed; do not weaken the checkpoint's exact-image guard.
