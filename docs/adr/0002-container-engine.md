# ADR-0002: native Ubuntu container engine

Date: November 3, 2026. Status: selected and installed; scoped engine and P0-02 checks passed.

Docker Engine runs isolated application processes called containers. Compose describes a group of containers, their connections, and persistent storage. A volume stores data independently of a container's lifetime.

## Decision

Use one systemd-managed native Ubuntu Docker Engine through the qualified launcher. systemd is Ubuntu's service manager: it starts/stops background services and tracks their state. Use the locked [official package revisions](../../environments/docker-engine-packages.json) and project-local Docker CLI configuration. Keep Docker Desktop stopped and preserve its existing data.

Desktop failed repeatable normal startup after a bounded diagnostic attempt. Native Engine removes that failed dependency from the active development path. Standard Engine service installation and ordinary-account Docker-group access were explicitly authorized and completed. That group controls an administrator-level Ubuntu service, including container mounts; it does not grant Windows administrator privileges. Do not request the same authorization again for routine use. No public engine endpoint was configured.

## Evidence and consequences

Engine/CLI 29.9.2, containerd 2.4.2, Buildx 0.38.1, and Compose 5.7.0 were installed from the official signed source. Two normal service stop/start cycles preserved engine identity and volume data. Cleanup verified unique test names/labels before removing owned resources. Subsequent messaging/store/query and SUMO fixtures passed. A full host reboot and sustained application workload remain untested.

The launcher passes Docker settings only to its Linux child; Windows/Desktop contexts and credentials are preserved. Rootless Docker was considered but is not the selected setup. See [operating guide](../setup/docker-engine.md), [verification summary](../verification/README.md), [official installation](https://docs.docker.com/engine/install/ubuntu/), and [Docker-group privileges](https://docs.docker.com/engine/install/linux-postinstall/).
