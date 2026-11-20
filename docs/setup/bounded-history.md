# Reading large histories without hiding measurements

A chart does not need to draw every stored reading to show a long interval clearly. It does need to preserve important changes. For example, averaging a brief battery-temperature spike into a minute of normal temperatures could hide the exact event a maintenance engineer needs to see.

The new history service keeps the original readings in PostgreSQL, the project's database. It selects original points for the chart and exposes the complete raw readings through a separate export. It never replaces stored readings with calculated chart values.

## What the server provides

An API, or application programming interface, is the set of requests another program can make to the service. These three additive read-only routes select one authorized asset, one frozen snapshot and one to five distinct registered signals. The existing history/export/summary routes remain compatible.

| Route | Purpose | Bound |
|---|---|---|
| `/v1/plot` | Original first, lowest, highest and last good points in each time bucket and continuous segment | At most 5,000 points across all selected signals |
| `/v1/history-summary` | Counts, minimum, mean and maximum calculated in the database | At most 5,000 total signal intervals |
| `/v1/export-page` | Complete original scalar readings, units and provenance over successive pages | At most 1,000 rows per response |

A bucket is a small time interval. A segment is a sequence of valid readings with no missing expected sampling interval. Missing or invalid values, a timing gap, a source change or a new source epoch prevent a chart line from crossing that break. A source epoch identifies one registered execution of a source. Zero remains a real numeric value. Empty summary buckets retain `null` to represent the absence of a numeric result.

SQL means Structured Query Language: the language used to request database rows and calculations. The server uses SQL window functions to compare neighboring readings and rank each bucket's original points. The database performs the reduction before returning the bounded result. A very fragmented interval can exceed even the reduced point budget. In that case the server returns an explicit HTTP 413 response; it does not silently truncate the result or connect separate segments.

Pagination divides a raw export into manageable responses. The continuation identifies the last returned time/event/signal and binds the frozen snapshot, selected signals, time interval and page size. Changing those inputs invalidates the continuation. New readings arriving after the snapshot cannot appear halfway through the download. This route's export metadata version is `1.1.0`; its readings retain their original schema, catalog and recipe provenance.

## Reproduce the backend checks

Use Windows PowerShell in `<checkout>`. The Linux launcher runs Ubuntu tools from Windows PowerShell.

1. Run the shared code checks:

   ```powershell
   python scripts/dev.py check
   ```

2. Run a new isolated real-service qualification:

   ```powershell
   python scripts/linux.py --timeout 1500 -- python3 scripts/monitoring/qualify_history_backend.py --evidence-dir evidence/p1/CHOOSE-A-NEW-ATTEMPT-NAME
   ```

   The launcher runs the Linux command in Ubuntu. `--timeout 1500` limits the launcher to 25 minutes; it does not define a benchmark duration. The fixture owns its temporary database, broker, services and volumes, and removes them at closeout. Existing output directories are refused to preserve earlier attempts.

3. Read the new directory's `qualification.json`. Require `passed: true`, matching source fingerprints, passing history checks and empty owned cleanup. A separate scoped result explains what passed. Do not infer full Phase 1 acceptance from this small fixture.

The fixture first generates 30 actual accelerated plant observations. It then publishes 11 separately labeled controlled observations through the real receiver and durable acknowledgment path. Hand-checkable values include a speed of zero, a speed spike of 20, a temperature spike of 380, one missing speed, one invalid speed and a missing sampling interval. The plotted points must be original good samples, the breaks must remain explicit, and a deliberately too-small point budget must fail clearly.

The frozen five-signal export contains 50 scalar rows over multiple seven-row pages. An additional observation arrives after its first page; the remaining frozen pages still total 50, while a fresh snapshot contains 55. The test also checks unauthorized access, wrong-asset access, invalid selections, changed continuations and empty intervals. Raw outputs and generated credentials remain ignored.

## Dashboard integration

The E3-B implementation requests one bounded plot containing the selected signals. Its browser qualification is a separate checkpoint from E3-A. The browser checks the response's run, asset, frozen revision, selected signals, units, source ownership, sampling metadata, counts and retained extrema before drawing. Unsupported or inconsistent results produce an error before the chart is drawn. The existing live overview trend keeps its raw read path.

During a prepared authenticated monitoring session:

1. Open **Measurements**, then choose **History** for a signal. Set **From** and **To** in simulation seconds. These are times within the simulated journey, not dates on the laptop clock.
2. Expand **Select signals to compare**. Choose up to five measurements, then click **Apply historical query**. Each chart keeps its own vertical axis and stored unit. The query freezes the current committed revision; applying it again can include newer committed readings.
3. Read each chart's point count. For example, “800 original plot points of 3,600 readings” means 800 actual readings were retained for drawing; 3,600 remain stored. The shared cursor only reports a retained point sufficiently close to its time. Absence of a retained point near the cursor does not prove a measurement was missing.
4. Expand **Inspect recorded values and provenance** to inspect the latest 100 retained original points. Use **Download JSON** or **Download CSV** for the complete raw selected interval, including missing and invalid readings. JSON, JavaScript Object Notation, preserves nested metadata and samples. CSV, comma-separated values, is a table format useful in analysis tools. Both downloads include metadata; the CSV quotes embedded commas/newlines and neutralizes formula-like text fields.
5. Choose a positive **Bucket width**, then **Calculate summary** for interval statistics. Only good numeric readings contribute to numeric statistics; missing and invalid readings have separate counts. The display labels any limited table preview explicitly.

Raw download pages are checked for strict row ordering, overlapping/repeated rows, original recipe/schema/catalog metadata and matching query-bound continuations. No file is downloaded until all pages pass. The browser limit is 100,000 scalar readings / 64 MiB, with a two-minute operation timeout and a separate eight-MiB response bound. These are bounded browser download limits, not data-retention limits. A new query cancels the previous operation; late responses cannot replace the newer query. Plot and summary requests have a 15-second client timeout; the unchanged Q-05 performance target is much stricter and requires its own measurements.

## Acceptance still required

E3-A verifies backend semantics. E3-B verifies bounded-format validation, five-signal plots and small complete raw downloads in both qualified browsers, including the added second-bus sensor and existing replay behavior. Run `python scripts/monitoring/qualify_dashboard.py --evidence-dir evidence/p1/CHOOSE-A-NEW-DASHBOARD-ATTEMPT` and then `python scripts/monitoring/qualify_extension.py --evidence-dir evidence/p1/CHOOSE-A-NEW-EXTENSION-ATTEMPT` from Windows PowerShell for those separate fixtures. Require both controllers, their service reports, original-byte checks and cleanup to pass; retain failed attempts.

E3 must still provision the full W2 history volume: 72,000 events / 1,440,000 scalar rows. At that volume the browser must complete actual multipage JSON/CSV downloads. Q-05 measures five-signal, one-hour historical queries separately for cold database-process buffers and warm queries. Q-10 times an isolated W2-sized restore and checks recovered data. Formal history visual baselines remain E4. These are separate from a live workload.

The live Phase 1 W0/W1 policy remains exactly 60 warmup seconds plus 1,800 measured wall seconds. Accelerated fixture generation and historical time windows do not extend that duration. See [ADR-0024](../adr/0024-bounded-history-and-volume-qualification.md).
