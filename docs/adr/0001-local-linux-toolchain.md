# ADR 0001. Local Linux toolchain

Date: November 3, 2026. Status: accepted.

## Decision

Keep the checkout on the Windows host and run Linux tooling in Ubuntu under WSL 2 through the project launcher. The launcher preserves the working directory, argument boundaries, exit status, and managed process-group cancellation. Machine-specific settings are generated locally and ignored.

## Verification and tradeoffs

The original launcher qualification passed 18 integration tests covering shared-file access, interruption, argument handling, exit status, and scoped cleanup. A Windows file-lock failure informed the launcher recovery tests. Cancellation covers managed foreground process groups. Detached processes, supervisor crashes, host suspension, and filesystem throughput require separate verification.

[Launcher setup](../setup/linux-launcher.md) · [Platform verification](../foundation-status.md)
