# Preview the age and size of a recording

Retention is the policy for how long readings are kept. The preview counts how many original messages and individual readings are older than the policy allows. It also reports the space used by the local database. The ordinary reader account opens a read-only transaction, so this procedure is safe to use before deciding what to archive.

The raw-history policy uses a 48-hour age threshold and a 32 GiB database budget. The age comparison uses the time a message reached the service. The size comparison measures the whole local database, including indexes and application tables. Summaries are calculated from the original history when requested. [Versioned workload policy](../../benchmarks/workloads.json)

## Reproduce the verified example

From the project root in Windows PowerShell, run the following with a new evidence directory. The project’s configured Ubuntu Docker Engine starts an isolated database and inserts four small synthetic records. Three belong to the selected run and one belongs to another run.

```powershell
python scripts/release/qualify_restarts.py --case retention-preview --output evidence/p6/retention-review
```

The final report should show three selected messages and one age candidate. A message exactly at the threshold stays outside the age candidates. A protected run reports zero candidates. A deliberately small budget verifies the over-budget flag. The test compares the stored original bytes before and after the preview, then removes its temporary resources. [Verification result](../verification/retention-preview.json)

## Run the preview in an owned monitoring deployment

Mount `scripts/release/retention_preview.py` and `benchmarks/workloads.json` as read-only files in the monitoring reader service. Use the deployment's existing `evdt_read` credential and `EVDT_RUN_ID`. Run `python /fixture/retention_preview.py --policy /fixture/workloads.json` inside that service if those are the selected mount paths. `--as-of` accepts a timestamp with a time zone, such as `2026-11-06T12:00:00Z`. `--protected` excludes the selected recording from age candidates. The report keeps the observed age counts visible for review.

An over-budget flag reports storage pressure. The reported size is the amount currently used by the database. A deletion or backup lifecycle policy needs its own qualification.
