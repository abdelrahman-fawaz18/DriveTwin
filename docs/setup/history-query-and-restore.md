# Check historical queries, recovery and complete browser downloads

**Historical fixture retirement:** the original completed history/normal-queue backups and recordings described below were retired under [ADR 0075](../adr/0075-minimal-historical-retention.md). Their measured outcomes remain historical summaries. Commands requiring those named originals cannot reproduce the old checks from the reduced tree. Normal new-trip recording and recovery remain implemented; a new qualification needs new authorized evidence.


## Current reader compatibility

The release check restores the retained local recording with its original application image. It then runs the current read-only web service against that database and checks scoped access, representative queries, JSON and CSV exports, and the frozen query limits. The original recording and reports keep their own fingerprints.

Replacing the reader can remove a container between the monitor listing it and reading its resource counters. If that happens, the monitor takes one fresh inventory under the same sampling and resource limits.

The current-reader audit checks the direct JSON and CSV exports. It verifies their format version, frozen snapshot, policy fingerprint, signal definitions, row limit, and original values. The browser builds its own paged downloads, which have separate metadata and a separate verifier.

Each cold query starts with a database restart. Docker can reuse the same Linux memory-group path for the new process, so the history monitor checks cumulative memory-event counters against the process start identity as well as the path. Counter decreases within a process, gaps in monitoring, and out-of-memory events still stop the run.

In Windows PowerShell from the project root, run these commands with the sealed recording, its original image, and the private credentials from the original run available. The old credentials are used to verify that the restored service rejects them. Choose a new output folder for each attempt.

```powershell
python scripts/linux.py --timeout 1000 -- python3 scripts/release/prepare_simulation.py --dashboard
python scripts/release/qualify_restarts.py --case history-reader --output evidence/p6/history-reader-REPLACE
```

The first command builds the current reader image. The second checks host readiness and creates an isolated database for the restore. It saves the result to `qualification.json` in the chosen evidence folder. A failed launch check leaves the original recording untouched and starts no restore services.

## Original history qualification

These procedures reuse a sealed [history checkpoint](history-database-checkpoint.md). A small pilot tests the method. W2 acceptance needs ten assets with 7,200 events each and 1,440,000 scalar history rows. Check [Phase 1 status](../monitoring-status.md) for measured results.

## Tools and checks

`pg_restore` is PostgreSQL's tool for rebuilding a database from a saved archive. The qualifier creates a fresh database with new passwords and uses the checkpoint's exact application/database images. It refuses to overwrite an original/nonempty target. The existing application must return the same frozen history after restoration. Full inventories compare every durable table's contents, schema, permissions and sequence state; counts alone are insufficient. New credentials must work while old credentials and forbidden writes fail. This procedure restores database history, not vehicle queues or an in-memory physical simulation.

Cold and warm describe the documented cache conditions, not different data. Cold requests follow a PostgreSQL restart/readiness check; disk and operating-system caches are uncontrolled. Warm requests follow one identical untimed priming request per case. The same twenty fixed asset/window cases are used in both series. Every returned original point, segment, extremum, quality count and summary is independently checked against source packets. The measured request includes HTTP transfer and JSON parsing. No failed request is silently removed from the denominator.

CSV means comma-separated values, a table stored as text. Its quoting rules matter because descriptions can contain commas or line breaks. The browser qualifier saves files actually produced by the product, then uses Python's standard CSV reader and a separate JSON checker to compare every row with original source evidence. It verifies complete page counts and shared frozen metadata; seeing a download button or one page is insufficient.

## Run from Windows PowerShell

Start in `<checkout>`. The example checkpoint name is a placeholder: read the actual `history_volume.checkpoint.path` in the successful dataset report. Keep each attempt in a new evidence directory.

1. Run shared checks:

   ```powershell
   python scripts/dev.py check
   ```

2. For independent restore and frozen representative reads:

   ```powershell
   python scripts/linux.py --timeout 1800 -- python3 scripts/monitoring/qualify_history_restore.py --checkpoint .private/backups/CHOOSE-THE-OWNED-PROJECT/history --evidence-dir evidence/p1/CHOOSE-A-NEW-RESTORE
   ```

   The qualifier verifies the backup, creates only its own temporary cluster, restores the binary archive, checks complete inventories and actual volume, checks saved queries and tests permissions/rotated credentials. `restore_and_verification_s` is the actual measured interval; `within_900s` is the unchanged deadline. `Q10_W2_passed` additionally requires complete W2 volume. The original local fixture credentials must remain available for this qualifier's explicit old-password/token denial tests; they are not recovered from the backup. A general disaster-recovery workflow without those old test credentials is separate work.

3. For restore followed by all fixed cold/warm plot cases and independent summaries:

   ```powershell
   python scripts/linux.py --timeout 1800 -- python3 scripts/monitoring/qualify_history_queries.py --checkpoint .private/backups/CHOOSE-THE-OWNED-PROJECT/history --evidence-dir evidence/p1/CHOOSE-A-NEW-QUERY-ATTEMPT
   ```

   Read `history-queries.json`. Require twenty passing cold and twenty passing warm cases, successful recorded warm priming, all original-value/bucket audits, and p95 <=2 seconds in each series. `Q05_W2_passed` remains false for a small pilot even when the method passes. Raw responses and their hashes remain private. A callback failure fails the overall controller, and owned cleanup must be empty.

4. For actual product downloads in both browsers:

   ```powershell
   python scripts/monitoring/qualify_history_browser.py --checkpoint .private/backups/CHOOSE-THE-OWNED-PROJECT/history --evidence-dir evidence/p1/CHOOSE-A-NEW-HISTORY-BROWSER-ATTEMPT
   ```

   Windows runs the already qualified browser tools while Ubuntu hosts the restored read API. No agent-mode switch or app restart is needed. Check the top-level report, `download-audits.json`, browser reports and nested backend report. At W2 volume require first/last asset checks, five real charts, 18,000 original rows and eighteen pages in each JSON/CSV download, every-row equality, consistent frozen metadata, empty browser storage and empty owned cleanup. `W2_browser_export_passed` stays false for a single-page pilot. Downloads/screenshots/traces remain ignored and private.

5. Preserve failed or incomplete attempts. Review the top-level result and all requested subchecks; an error or failed nested gate cannot be treated as acceptance. The prototype's first query attempt is explicitly unaccepted and preserved with a separate authoritative review because its controller incorrectly retained an earlier success flag after a follow-up failure. The corrected controller was exercised with an intentionally failing real check and clean teardown.

Shared checks cover original-value/Boolean distinctions, CSV quoting/negative/zero values, corrupted downloads, continuity/extrema/quality, fixed case selection and nearest-rank percentile behavior. Actual service/browser evidence is still required in addition to unit tests. See [ADR-0029](../adr/0029-history-query-restore-and-download-evidence.md).

These historical procedures do not change live Phase 1 duration: W0/W1 remain 60 warmup plus 1,800 measured wall seconds. No one-hour live benchmark is launched here.
