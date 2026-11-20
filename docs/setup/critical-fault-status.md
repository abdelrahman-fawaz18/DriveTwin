# Keeping a storage fault visible when the disk is full

The fault-status component and full-disk persistence checks passed. Later queue binding and source/Sender integration are recorded in the [storage binding guide](bound-vehicle-storage.md). The optional provider remains disabled in the release configuration. See [Phase 1 status](../monitoring-status.md), [the measured record](../verification/critical-fault.json) and [ADR-0033](../adr/0033-preallocated-sticky-critical-fault.md).

## Why preallocate a fault record

Creating a new error file can fail precisely when the disk is full. **Preallocation** reserves actual disk blocks while space is available. This component reserves two 4,096-byte slots in the separate critical filesystem. Later fault updates overwrite an existing preallocated slot. The successful physical test still reported zero available bytes after that update.

A **fault latch** remembers that a fault occurred until an explicit recovery procedure addresses it. This implementation has no reset operation. It preserves the first reason/time, latest reason/time and occurrence count. It stores local status, not telemetry or command results. Phase 1 operational command acceptance remains disabled.

## Two copies and one writer at a time

Each fixed-size slot contains a JSON frame and a checksum. The checksum is a content fingerprint that detects changed bytes; it is not authentication against an administrator who can rewrite the files. The frame binds the recording, asset and source identity. A generation is the ordered update number, incremented with the occurrence count.

A Linux file lock coordinates cooperating processes. Without it, two writers could read the same count and overwrite each other's updates. The wait is bounded to two seconds. Updates write the older/inactive slot and synchronize it before reporting success, leaving the previous valid copy available. Initialization synchronizes both allocated files and their directory.

Reads validate framing, checksum, exact identity/format and counter rules, then select the newest valid copy. A missing or torn copy is degraded, never automatically recreated. Two invalid copies are unavailable. Conflicting valid frames at the same generation and a different identity/format fail explicitly. A single readable old copy does not count as healthy redundancy. Callers must treat faulted, degraded or unavailable status conservatively.

"Healthy" means two valid status copies with no latched fault. It does not prove free disk space or authorize a command. Future command handling must independently reserve result capacity and check availability before accepting work; that integration remains later work.

## What was actually tested

Component checks exercise fully allocated slots, sticky first/latest reasons, reopening/identity binding, refusal to overwrite an existing directory, invalid input without changes, missing/torn/linked/conflicting copies, concurrent writers, a held-lock timeout and an actual process exit during a partial slot update. Recovery retained the earlier committed fault and exposed degraded redundancy. No file was silently recreated to pass a read check.

The physical fixture used new 64-MiB telemetry and separate 8-MiB critical filesystems. An ordinary UID-10001 container set the first fault after telemetry filled, then the fixture filled critical storage to a real one-byte no-space error. Another ordinary container updated the already allocated fault slot and synchronized it with no critical free space. A fresh container reopened two valid copies: occurrence count two, first reason `telemetry_full`, latest reason `critical_full`, fault latched. Both slots occupied 8,192 allocated bytes. Every source fingerprint matched and all owned volumes/containers/device associations were removed.

These results establish process/container recovery and tested full-disk updates. Operating-system/power-loss recovery, reboot attachment, protected command-result storage and real vehicle transport fault/reconnect integration are separate gates.

## Files and reproduction

`src/evdt/monitoring/critical_fault.py` implements `FaultLatch`, `FaultStatus` and `FaultUnavailable`. It is Linux code used inside Ubuntu/containers. A fresh owned directory is initialized with `create=True`; normal reopening omits that option. `trip(reason)` records a bounded explicit reason, and `read()` returns current status plus the number of valid copies. Its internal frame format is `critical-fault/1.0.0`. Supported reasons are caller-defined; it has no sensor-specific logic.

Run the following commands in Windows PowerShell from `<checkout>`.

```powershell
python scripts/dev.py check
wsl --distribution Ubuntu-24.04 --user root --exec python3 scripts/monitoring/qualify_disk_boundaries.py --critical-fault --evidence-dir evidence/p1/CHOOSE-A-NEW-FAULT-ATTEMPT
```

The first command passed 386 Python / 22 frontend tests, six strictly checked Python modules, build/format/privacy/secrets and unchanged 339+81 contract outcomes per language. The second uses the scoped owned-device provisioner described in [persistent disk boundaries](persistent-disk-boundaries.md). Its application probes stay unprivileged; current source is mounted read-only and compiled without reusing host bytecode caches. Require the top-level pass, `critical_fault_full_disk_passed`, exact recorded first/latest/count/redundancy/full-disk results, matching source hashes and empty cleanup. Keep all raw state/logs private. No agent-mode change, default-user change, app restart or source relocation occurs.
