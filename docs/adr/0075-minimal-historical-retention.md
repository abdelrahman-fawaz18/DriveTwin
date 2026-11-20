# ADR 0075. Minimal historical retention

Date: November 18, 2026. Status: accepted.

Local retention keeps original foundation evidence and a compact record of completed runs.
Retire completed recordings and diagnostics after preserving outcomes and corrective actions.
This decision supersedes ADR 0074's five-recording minimum and preservation of
complete historical release, recovery, demonstration, and study payload sets.

Preserve the complete `evidence/p0` foundation set, current simulation inputs and
attribution, the installed geographic catalog and bundle, original frozen model
artifacts and consumed evaluation identities, source/tests/documentation, and
active or unresolved recovery state. Preserve the current prepared browser site,
its matching semantic case and verification receipt, and files actually served
by an existing local server. Installed tools and original source datasets remain
available for current development and map preparation.

Retire completed historical recordings and their backups, superseded geographic
study input copies, old service-run staging, duplicate demonstration builds, and
historical diagnostic captures. Retain compact outcome/source-identity receipts
and a bounded deletion inventory. Do not retain raw recordings inside a renamed
archive or a new backup. The issue guide is [known issues and fixes](../setup/known-issues-and-fixes.md).

Historical numerical outcomes keep their original source, assumptions, and
pass/fail scopes. After retirement, a summary and hash do not establish that the
original payload is still locally inspectable. Historical inspectors that need
retired inputs are unavailable against the reduced tree. Record this limitation
in the current verification index; do not change old reports to manufacture a
new pass. A consumed evaluation identity remains consumed even after its raw
data is removed. A new study or retuning still requires separate scope and
independent evidence.

This is deliberate local maintenance. It does not enable automatic deletion,
disable saving new trips, run a bus, prune Docker/WSL storage, change host
settings, alter publication rules, or authorize a new deployment. A new trip
continues to save its recording; later deliberate cleanup may retire it after
its ownership and recovery state have been checked.

Use exact inventories, current process/resource ownership checks, the existing
workload lock, resolved canonical paths, and before/after input fingerprints.
Keep historical reference conflicts as an explicit retirement record rather
than silently treating them as unreferenced files.
