# Docker Engine inside Ubuntu

Updated: November 3, 2026

The native Ubuntu Engine is installed and qualified for the local simulation services. Application, simulator, browser, and recovery results are indexed in the [release evidence](../release/verification.md).

## What is running where

Run Docker Engine and Compose in Ubuntu through the project launcher. Docker Engine runs as a systemd service.

The source folder remains <checkout>, visible in Ubuntu as <checkout>. Docker's runtime data is stored separately inside Ubuntu at /var/lib/docker. Runtime packages and data are not a second source checkout.

```mermaid
flowchart LR
    W[Windows PowerShell] --> L[Linux command launcher]
    L --> U[Ubuntu in WSL2]
    U --> E[systemd-managed Docker Engine]
    E --> C[Containers]
    C --> V[Named storage volumes]
```

Docker Desktop is still installed on Windows, but it is stopped and is not the engine used for DriveTwin. Its images, containers, volumes, credentials, and settings were not reset. [ADR-0002](../adr/0002-container-engine.md) records why its repeatable-startup test failed and the endpoint folders preserved during diagnosis.

## Installation and service access

The native engine runs as a persistent system service inside Ubuntu. Docker-group membership permits engine control, including mounting files available to WSL.

Use the [bootstrap](../../scripts/platform/bootstrap_docker_engine.py) with Ubuntu root for installation. Use an ordinary Linux account for project commands.

For future reproduction in a fresh equivalent environment, the installation sequence from Windows PowerShell is:

```powershell
wsl --distribution Ubuntu-24.04 --user root --exec python3 scripts/platform/bootstrap_docker_engine.py prepare
wsl --distribution Ubuntu-24.04 --user root --exec python3 scripts/platform/bootstrap_docker_engine.py install
```

The installation was completed and qualified for the recorded environment. `prepare` configures Docker's official signed Ubuntu package source and records package versions. `install` uses the recorded versions, configures access for YOUR_LINUX_USER, and enables the systemd services. It does not select a moving latest package version on every run. The [package manifest](../../environments/docker-engine-packages.json) is the reproduction input.

The installed versions are Docker Engine/CLI 29.9.2, Compose 5.7.0, Buildx 0.38.1, and containerd 2.4.2. containerd is the lower-level component Docker uses to manage container execution; Buildx is a tool for building container images. The release preparation builds the application and simulator images from the pinned inputs.

## Everyday commands

From Windows PowerShell in the project folder:

```powershell
python scripts/linux.py -- docker version
python scripts/linux.py -- docker compose version
python scripts/linux.py -- docker ps
```

The launcher handles entering Ubuntu. `docker version` reports both client and engine versions. The **client** is the command program sending requests to the engine. `docker compose version` checks the Compose tool. `docker ps` lists running containers; an empty list is expected before project services are created.

The settings in `environments/dev.toml`, generated from the [settings template](../../environments/dev.example.toml) are passed only to Linux child commands:

| Setting | Meaning |
|---|---|
| DOCKER_HOST = unix:///var/run/docker.sock | Connect to the local Ubuntu engine through its Unix socket, a local communication endpoint |
| DOCKER_CONFIG = /home/YOUR_LINUX_USER/.config/ev-dt/docker | Read project-specific client settings from this separate Linux folder |

These are **environment variables**: named settings passed to a running program. They do not change the Windows environment or the desktop agent setting. The isolated client folder starts with an empty configuration; Windows/Desktop credentials and named connection profiles are left intact. The expanded launcher suite verified this scoping.

Use this entry point for DriveTwin Docker commands. A plain Windows `docker` command still belongs to the existing Windows configuration and is not how this project selects its native Ubuntu engine. Engine identity is checked before the qualification program restarts services or cleans up resources. Simultaneous use of Desktop's Ubuntu integration and the native engine is outside the selected single-engine arrangement.

## What was verified

The [qualification program](../../scripts/platform/qualify_docker_engine.py) ran real containers using an official BusyBox image. An **image** is a packaged template used to create a container; BusyBox supplies small Linux utilities suitable for this platform test. Its human-readable tag was used for initial selection, then its exact **digest**, a content fingerprint, was recorded in [the image manifest](../../environments/reference-container-images.json). Subsequent runs use that digest.

For each of two cycles, the program:

1. Wrote a small test record into a uniquely named Docker **volume**. A volume is Docker-managed storage that survives container removal.
2. Confirmed there were no running containers before stopping the engine, protecting unrelated container work.
3. Stopped and started the native engine through systemd.
4. Checked that it was still the same Ubuntu engine.
5. Started a new container and verified that the stored record was unchanged.

Afterward, cleanup verified the volume's exact name, engine identity, and project/run ownership labels before removing that test volume. It did not run a general cleanup or prune operation. The small pinned test image remains available for reproduction.

The final cycles took roughly 3.1 and 3.5 seconds for the stop/start-and-verification sequence. Those timings include command transport, identity checks, and reading the file; they are not isolated engine-startup benchmarks. There was no runtime-folder repair or app restart during these cycles.

To reproduce deliberately, with no running containers:

```powershell
python scripts/platform/qualify_docker_engine.py --report evidence/p0/native-engine-qualification-repeat.json
```

This test restarts the native engine. It refuses to proceed when containers are running and uses only its own labelled test volume. Use a new report filename to retain previous evidence.

## Memory and qualification limits

The final resource sample kept a controlled foreground Linux command active after confirming engine readiness. Windows reported 2,307 MiB available, approximately 2.25 GiB. WSL's working set was approximately 1.34 GiB; Linux reported approximately 0.62 GiB active memory and no swap in use. These are different measurement layers and must not be added together.

An earlier snapshot measured the host before its Linux command started; it showed more free memory and is retained with that methodological limitation. Use [the active-WSL sample](../verification/README.md) for this idle baseline. No WSL memory allocation change was needed for these tests. The full application's headroom and paging behavior remain unqualified until the real workload runs; this does not pass Q-12.

The service is enabled for Ubuntu startup. Normal service stop/start was tested; a controlled full Windows or WSL-distribution reboot was not performed. WSL lifetime and the lifecycle of application containers will be tested with the actual Compose fixture. This setup does not claim production uptime or vehicle safety certification.

## Evidence and official references

- [Engine-prerequisite acceptance](../verification/README.md)
- [Authorized installation log](../verification/README.md)
- [Installed Ubuntu package revisions](../verification/README.md)
- [Regular-account access and service checks](../verification/README.md)
- [Final restart/persistence report](../verification/README.md)
- [Docker's Ubuntu installation documentation](https://docs.docker.com/engine/install/ubuntu/)
- [Docker group access](https://docs.docker.com/engine/install/linux-postinstall/)
- [Docker volumes](https://docs.docker.com/engine/storage/volumes/)
- [BusyBox official image](https://hub.docker.com/_/busybox)
