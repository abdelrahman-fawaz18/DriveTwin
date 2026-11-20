# ADR-0032: Separate persistent filesystem boundaries for queue storage

Date: November 4, 2026. Status: standalone physical-boundary mechanism passed with corrected fail-closed verification. Vehicle queue/critical-result and deployment lifecycle integration remain pending.

## Decision and scope

The encoded queue limit does not constrain all SQLite pages, indexes, transaction journals and audit records. Validate an independently enforced, persistent filesystem ceiling for each storage class. Freeze the existing budgets in `benchmarks/storage.json`: 64 MiB telemetry, another 8 MiB critical storage and unchanged 32 MiB encoded telemetry. Filesystem metadata and its own journal fit inside each physical boundary; do not promise that the entire physical budget is usable record payload.

Create only new, uniquely owned regular disk-image files in an explicitly labeled temporary Docker backing volume. Fully allocate their fixed sizes and create ext4 filesystems only after checking exact owned paths, regular-file identity, size and absence of links. An ext4 filesystem is a Linux disk format. A loop device makes one regular image file available as a disk device. Docker's local volume driver can mount that device into an ordinary container. This follows Docker's documented [block-device volume procedure](https://docs.docker.com/engine/storage/volumes/); [losetup](https://man7.org/linux/man-pages/man8/losetup.8.html) manages the file/device association. No existing drive or partition is formatted and no daemon storage-driver setting changes.

The short provisioner requires Ubuntu administrator rights for device setup; normal application containers remain unprivileged and ordinary development uses the existing regular-user launcher. Use the normal escalation review for this tightly scoped administrator action. It installs no service/package, changes no agent setting and does not restart Ubuntu or the app. Keep the canonical source tree in its current Windows folder; these are owned temporary Linux test data, as with existing Docker database volumes.

## Safety and ownership checks

All names derive from one generated fixture identity. Refuse existing output/state/volume names. Inspect the actual local daemon root and derive backing paths from its inspected owned volume, never from arbitrary caller input. Validate ownership labels, image-file identity and the kernel's exact loop backing-file association before every cleanup. Application containers receive only the new mounted volumes, no host socket/device access, dropped capabilities, no network, a read-only root filesystem and bounded memory.

Stop/remove only this fixture's named/labeled helper containers. Refuse volume removal while any container references it. Remove only verified owned filesystem volumes, then detach only loop devices still associated with the exact owned files, and finally remove the owned backing volume. Save failure and cleanup evidence privately. Do not remove unrelated containers, devices, files or storage. The existing host free-disk reserve remains a prerequisite.

## Required mechanism tests and limits

Check actual filesystem sizes and device identities. Write and synchronize known markers, fill telemetry to a real no-space error, prove critical storage remains writable, then reopen markers in new ordinary containers. Fill critical storage independently, free only the owned telemetry filler and prove telemetry can write while critical storage stays full. Check marker hashes and measured changes in free space. Preserve all observed error and durability evidence, source hashes and empty owned cleanup.

This first mechanism test does not integrate SQLite queue limits, critical-result journal behavior, automatic command refusal, transport/reconnect indications or production startup. Those remain separate E4 tests. Container-reopen persistence does not qualify an operating-system crash, power loss or host reboot. Loop associations are temporary across reboot; a deployed profile needs an explicit identity-checked attachment/startup lifecycle and compatible backup/restore before use. Existing backup/image guards and default vehicle queue behavior stay unchanged.
