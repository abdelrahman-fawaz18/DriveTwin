# Reading several buses together

The Fleet page groups all currently selected authorized buses on one page. A fleet is simply the group of vehicles an operator is monitoring. This page uses the existing read service; it cannot send a vehicle command.

1. Open the local monitoring address supplied by the current owned fixture, in Windows Edge or the qualified Chromium browser.
2. Enter its reader credential and choose **Connect to monitoring**. A reader credential grants access only to its assigned vehicles. It stays in the page's memory, outside URLs, cookies and browser storage.
3. Choose **Fleet**. The vehicle list comes from that credential's catalog. All authorized buses are initially selected. In **Fleet vehicles**, use Ctrl+click to change the Windows selection.
4. Read each vehicle panel separately. Its heading identifies the bus; the run identifies the recording. Each measurement retains its stored unit, observation quality and freshness. Expand **Measurement metadata** for source, physical measurement location, provenance, model, time and failure reason. A legitimate zero displays as zero; an absent value displays a dash.
5. Choose **Open BUS details** for that bus's existing measurements, map, historical analysis and system views. Returning to Fleet opens a fresh fleet session with the authorized list.

Connection state and measurement freshness answer different questions. Connected means a recent authorized snapshot arrived. A connected panel can still show stale readings from a stopped source. If a panel disconnects, its last values remain visible with stale labels. Other buses can continue updating. A rejected or expired credential ends the session and clears all panels; signing in again rebuilds the permitted list.

## How the refresh works

Polling means asking the service for a fresh snapshot repeatedly. Two workers make at most two simultaneous requests, so adding buses does not create an unbounded request burst. A cycle normally starts every quarter second, with no overlapping cycles. Each response is validated before it can replace one bus's state. Slow or broken requests have a timeout, and a separate watchdog marks an old connection disconnected. No missing observation is invented between updates. Source packets and database history remain the durable record.

`apps/dashboard/src/features/fleet/fleetPolling.ts` contains request, identity and lifecycle rules; `Fleet.tsx` renders the catalog-driven panels. Existing selected-vehicle pages retain their stream. See [ADR-0030](../adr/0030-single-page-fleet-monitoring.md) for exact limits and the qualification boundary.

## Automated checks and current status

From PowerShell in `<checkout>`, run `python scripts/dev.py check` for shared logic/types/build/privacy checks. `python scripts/monitoring/qualify_extension.py --evidence-dir evidence/p1/CHOOSE-A-NEW-FLEET-EXTENSION` runs the existing actual two-source sensor/asset/browser fixture, now including fleet value/unit/quality checks, independent disconnect/recovery, offline/resynchronization, selection/detail navigation, credential expiry and scoped reauthentication. Each attempt needs a new ignored output directory; preserve failures.

The sustained measurement controller uses `fleet-operator-table/1.0.0` and checks every original event/value of all selected workload buses in the actual document. A short rehearsal cannot pass the full thirty-minute workload. Source changes require fresh matching-source evidence. Consult [Phase 1 status](../monitoring-status.md) for measured results; implementation alone is not acceptance.
