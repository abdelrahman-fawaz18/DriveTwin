# ADR-0033: Preallocated sticky critical-fault status

Date: November 4, 2026. Status: component, actual full-disk update/reopening and shared checks passed. Vehicle transport and command-result storage remain separate.

## Purpose and boundaries

A full telemetry filesystem must not hide its own failure. The independently bounded critical filesystem can also become full. Allocate a small fixed fault-status area before pressure occurs, then update existing allocated blocks without creating another file. This is a fault latch: once set, it remains faulted until a separately designed recovery procedure. It is not a command-result journal and does not silently discard results or clear a fault.

Use a fresh owned directory with two fully allocated 4,096-byte files and one existing lock file. Store canonical JSON body/checksum frames padded to the fixed slot size. The internal format is `critical-fault/1.0.0`; operational telemetry contracts remain unchanged. Bind recording, asset and source identity. Each successful trip increments a safe-integer generation/count, retains the first fault/time and records the latest fault/time. There is no reset method. Refuse initialization of an existing directory and preserve its prior status.

A Linux advisory file lock serializes writers across processes, with a bounded two-second wait. Write the inactive/older slot, check the full write and synchronize it before reporting success. Preserve the other valid copy. Reads verify shape, checksum, identity, format and generation invariants, then select the newest valid generation. Missing/torn copies are degraded, never recreated automatically; both invalid copies fail closed. Conflicting same-generation valid frames and another identity/format fail explicitly. A single valid old copy is not healthy redundancy. Checksums detect accidental changed bytes; they are not authentication against an administrator who can rewrite both files.

"Healthy" here means two valid status copies with no latched fault. It does not mean that disk space exists or grant permission to accept a command. Operational command acceptance is unavailable in Phase 1. A future command policy must independently reserve result capacity and refuse new work on unavailable/faulted/degraded status. This component supplies durable local fault visibility; it does not implement that executor or result lifecycle.

## Required checks and integration boundary

Test preallocation, sticky first/latest reasons, repeated/concurrent process updates, exact identity binding, initialization refusal, invalid input without writes, torn/missing/linked/corrupt copies, conflict detection and bounded failure. Extend the owned disk fixture with an explicit optional critical-fault mode, read-only current source and disabled bytecode-cache reuse. It must set a telemetry-full fault while telemetry is full, fill critical storage to no-space, update the already allocated fault slot while critical remains full, and reopen it in a new ordinary container with both original first and new last fault retained. Capture source hashes and preserve existing numerical budgets and cleanup guards.

Successful process/container reopening does not establish operating-system/power-loss recovery or a host-reboot attachment lifecycle. Existing default sender queue and original backup formats remain unchanged until their separately qualified integration. No remote endpoint or vehicle command is introduced.
