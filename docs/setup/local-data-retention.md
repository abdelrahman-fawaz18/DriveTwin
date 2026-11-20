# Local recording retention

DriveTwin stores operational files in ignored directories. The retention policy keeps foundation evidence and working inputs, replaces
important historical issues with concise notes, and retires completed old
recordings and diagnostics. [ADR 0075](../adr/0075-minimal-historical-retention.md)
supersedes the earlier five-session minimum and complete historical raw-reference
retention. Automatic background deletion remains disabled.

## Protected material

- Active sessions, credentials and leases, durable command journals and
  reservations, and unresolved recovery data or retained recovery owners.
- The complete `evidence/p0` foundation evidence.
- Current map, geometry, elevation, station, and route inputs; the portable
  scenario manifest and fingerprinted private installed bundle.
- Original frozen energy and thermal models, consumed evaluation claims,
  candidate declarations, and compact outcome/source-identity receipts.
- The current prepared browser-demo case, selected site, and matching receipt.
  Protect an older site while a local server still serves it.
- Current source datasets, attribution, and installed tools for map preparation
  and focused checks.

Completed historical recordings, their backups, superseded study input copies,
redundant demonstration builds, and historical browser/log captures may be
retired. The private inventory names exact paths and fingerprints; compact
summaries preserve pass/fail outcomes and source identities. The
[issue guide](known-issues-and-fixes.md) records important causes, fixes, and code
locations. Do not preserve a second full-size copy of discarded data.

Historical results are summary-only when their required raw inputs are retired.
Their old inspectors cannot independently recheck them from the reduced tree.
A new run creates new evidence, not the original measurement. Consumed evaluation
identities remain consumed, and new evaluation/retuning needs separate scope.

## Perform a deliberate cleanup

1. In PowerShell at the project root, confirm the source revision and working
   tree. Inventory sizes without following symlinks/junctions, processes, Docker
   ownership, and session/recovery state. Use ignored `.private/reviews/`.
2. Trace consumers. Verify catalog fingerprints, current demo selection, frozen
   models, consumed claims, and active/unresolved recovery ownership. Separate
   working inputs from history and save important issue/fix notes.
3. Review exact paths, sizes, reasons, and fingerprints. Run
   `python scripts/release/check_retention_references.py --plan .private/reviews/REVIEW/cleanup-plan.json`.
   Resolve historical conflicts by retaining the file or recording its explicit
   authorized retirement before deletion. Preserve compact outcomes and source
   identities. A disposable checkout can contain required original evidence.
4. Recheck ownership and acquire the existing live-workload lock. Verify every
   resolved target stays inside `<checkout>`, reject redirected ancestors,
   and recheck bytes/fingerprints. Delete files sequentially, then empty folders.
   Retire session indexes and obsolete profiles together. Close an idle launcher
   through its guarded endpoint before retiring its completed current pointer.
   Preserve active/unresolved references.
5. Verify protected foundation/model/claim files, catalog inputs, the current
   demo, empty or valid session indexes, and remaining recovery ownership. Record
   actual removed bytes separately from net size change. Reconcile retired
   references, run affected checks, and save reviewed local commits.

This does not change Docker/WSL storage, reset databases, remove active/unresolved
credentials, create evaluations, run buses, or publish files. New trips still save
recordings; later deliberate cleanup can retire them. Publication follows its
separate [procedure](../release/publication.md).
