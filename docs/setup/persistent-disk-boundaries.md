# Testing persistent disk limits and separate critical space

The filesystem boundary mechanism passed with the corrected verifier. Its queue binding and source/Sender integration are recorded in the [storage binding guide](bound-vehicle-storage.md). The optional provider remains disabled in the release configuration. See [Phase 1 status](../monitoring-status.md), [the sanitized result](../verification/disk-boundaries.json) and [ADR-0032](../adr/0032-persistent-queue-disk-boundaries.md).

## What this mechanism proves

A disk quota is a hard ceiling on storage. An encoded message limit alone cannot enforce it: database pages, indexes and transaction files consume additional space. This fixture creates two new fixed-size disk-image files in one uniquely labeled temporary Docker backing volume. Each file is formatted as an ext4 filesystem, a Linux disk format, and connected to a loop device. A loop device makes a file usable as a disk. Docker mounts each filesystem as a separate ordinary application volume. See Docker's [block-device volume procedure](https://docs.docker.com/engine/storage/volumes/).

The telemetry image is exactly 64 MiB; the critical image is another 8 MiB. These are separate physical budgets, not a shared total from which telemetry can consume the reserve. MiB means 1,048,576 bytes. The existing encoded telemetry target stays 32 MiB. Filesystem overhead reduces the space available for records:

| Area | Fixed image bytes | Reported filesystem bytes | Available after the first 4,096-byte marker |
|---|---:|---:|---:|
| Telemetry | 67,108,864 | 58,675,200 | 57,303,040 |
| Critical | 8,388,608 | 3,653,632 | 3,461,120 |

These are measured values for the tested ext4 creation settings, not promises of equivalent usable payload on every filesystem. Database/journal overhead still has to fit inside the remaining space. No claim of eight MiB of critical-result payload follows from an eight-MiB physical budget.

## Actual checks

The application probes use UID 10001, an ordinary Linux user identity. They have no network or elevated capabilities and a read-only container root filesystem; only the two new volumes are writable. The provisioner uses administrator rights only for exact owned file/device setup and cleanup. It checks names, ownership labels, daemon-root paths, regular files, file size and inode identity (an inode is Linux's identity number for a file), absence of links and the kernel's exact file/device association. Nothing formats an existing drive or partition, changes a daemon/agent setting or restarts Ubuntu.

1. Create and synchronize a known telemetry marker, then fill telemetry until even a one-byte append fails with `ENOSPC`, Linux's no-space error.
2. Write and synchronize the critical marker while telemetry is full. Reopen both markers in new ordinary containers and verify their exact bytes/hashes.
3. Fill critical storage independently. Remove only the owned telemetry filler and successfully write a new telemetry recovery file while critical storage still rejects another byte.
4. Reopen telemetry again and verify the original marker. Remove only owned helper containers/volumes, detach only the verified owned loop associations and confirm empty cleanup.

`fsync` asks the operating system to synchronize a file or directory before reporting success. This test qualifies synchronized writes and new-container reopening. It does not establish sudden-power-loss, operating-system-crash or host-reboot behavior.

The verifier rejects a missing or corrupted marker; it never recreates one while checking persistence. Negative tests also reject linked markers, wrong volume ownership/driver/name, a path outside the exact daemon-owned directory, changed image size/inode, links and directories. The first prototype's raw positive result is preserved but unaccepted because its reopen check could recreate a missing marker. The corrected fresh attempt passed. No original report was overwritten.

## Reproduce from Windows

The agent performs these commands; ordinary development continues through `python scripts/linux.py -- COMMAND`. In PowerShell at `<checkout>`:

1. Run `python scripts/dev.py check`. This checks source, tests, types, contracts, build, formatting and privacy. The recorded result passed 374 Python / 22 frontend tests and unchanged 339+81 contract outcomes per language.
2. Use the scoped administrator provisioner for a new private evidence directory:

   ```powershell
   wsl --distribution Ubuntu-24.04 --user root --cd <checkout> --exec python3 scripts/monitoring/qualify_disk_boundaries.py --evidence-dir evidence/p1/CHOOSE-A-NEW-DISK-ATTEMPT
   ```

   `--user root` applies to this one provisioner command; it does not change the default Linux account agent mode. Device setup requires administrator rights. The ordinary application probes remain unprivileged. The provisioner uses already installed Ubuntu filesystem tools and the existing pinned local Python image, with no package/service installation.
3. Read `qualification.json`. Require its top-level pass, all no-space/isolation/reopening checks, matching captured fingerprints and empty owned container/volume/loop cleanup. `commands.log` and incremental `state.json` remain private. An existing output directory is refused. Preserve failures and partial state; never issue broad Docker or device cleanup commands.

The separate [overflow-accounting component](queue-overflow-accounting.md) is selected only by the optional bounded-storage configuration. Actual SQLite/index/journal peaks, critical status/result durability and exhaustion policy, live reconnect diagnostics, startup/reattachment and compatible backup/restore require the next integration checks. A loop association does not survive reboot automatically; that lifecycle must be qualified before selecting this provider for a running deployment. Full W0/W1, host resources, retention and formal visual gates remain separate.
