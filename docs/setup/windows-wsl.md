# Windows and Ubuntu setup

The local deployment uses Windows, Ubuntu 24.04 under WSL 2, and Docker Engine in Ubuntu. Run commands from the repository root unless a guide specifies another directory.

## Install and initialize Ubuntu

On a Windows host that needs Ubuntu, open PowerShell and run the installation command. Windows may request elevation or a restart to enable WSL components.

```powershell
wsl --install --distribution Ubuntu-24.04 --no-launch --web-download
wsl --list --verbose
wsl --distribution Ubuntu-24.04
```

Verify that `Ubuntu-24.04` uses WSL version 2 and complete the first-run account setup.

## Install the base development tools

Run the versioned bootstrap from Windows PowerShell. The `--user root` option selects Ubuntu's administrator for this installation command.

```powershell
wsl --distribution Ubuntu-24.04 --user root --exec bash scripts/platform/bootstrap-ubuntu.sh
```

The script installs Git, Python, package tools, build prerequisites, and the restricted-process utility Bubblewrap. It downloads the fixed Linux Node.js archive and verifies its SHA-256 fingerprint before installation. Tool packages live inside Ubuntu. The project source stays in the canonical folder. [Bootstrap script](../../scripts/platform/bootstrap-ubuntu.sh)

## Configure the project launcher and Docker

From PowerShell in the project folder, generate the private machine settings and verify Linux execution.

```powershell
python scripts/configure_local.py
python scripts/linux.py -- python3 --version
```

The configuration command detects the Ubuntu distribution and account and saves ignored `environments/dev.toml`. The launcher should print the Linux Python version and return exit code zero. Follow the Docker Engine guide for the recorded package setup and ordinary-account access. [Linux launcher](linux-launcher.md) · [Docker Engine](docker-engine.md)



## Verify the environment

The preparation check verifies the ordinary account, source-folder access, Linux tools, temporary writes, command outcomes, an isolated Python environment, and restricted-process support. Save each intentional run under a new evidence name.

```powershell
wsl --distribution Ubuntu-24.04 --exec python3 scripts/platform/check_linux_environment.py --output evidence/p0/my-linux-preflight.json
```

Read the saved report and inspect any failed command and exit code. The launcher has its own cancellation and shared-file checks. The release preparation then builds the pinned simulator and application inputs. Live tests apply their declared host-memory and timing guards. [Foundation record](../foundation-status.md) · [Local demo](../release/demo.md)
