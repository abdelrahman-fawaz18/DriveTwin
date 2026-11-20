"""Prepare/install one systemd-managed Docker Engine inside Ubuntu.

Run as Ubuntu root. Preparation records fixed package versions; installation
uses that record. Windows Docker Desktop must already be stopped.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "environments/docker-engine-packages.json"
PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io",
            "docker-buildx-plugin", "docker-compose-plugin"]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def prepare():
    # Fail for existing distribution packages rather than remove user software.
    for package in ["docker.io", "docker-compose", "docker-compose-v2", "docker-doc",
                    "docker-buildx", "podman-docker", "containerd", "runc"]:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package], capture_output=True, text=True
        )
        if result.returncode == 0 and "install ok installed" in result.stdout:
            raise RuntimeError(f"Review conflicting package before proceeding: {package}")
    Path("/etc/apt/keyrings").mkdir(mode=0o755, exist_ok=True)
    key = Path("/etc/apt/keyrings/docker.asc")
    run("curl", "--fail", "--silent", "--show-error", "--location",
        "https://download.docker.com/linux/ubuntu/gpg", "--output", str(key))
    key.chmod(0o644)
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    source = ("Types: deb\nURIs: https://download.docker.com/linux/ubuntu\n"
              f"Suites: noble\nComponents: stable\nArchitectures: {arch}\n"
              "Signed-By: /etc/apt/keyrings/docker.asc\n")
    source_path = Path("/etc/apt/sources.list.d/docker.sources")
    if source_path.exists() and source_path.read_text() != source:
        raise RuntimeError("Existing Docker apt source differs; review before replacing it.")
    source_path.write_text(source)
    run("apt-get", "update")
    versions = {}
    for package in PACKAGES:
        policy = subprocess.check_output(["apt-cache", "policy", package], text=True)
        candidate = next(line.split(":", 1)[1].strip() for line in policy.splitlines()
                         if line.strip().startswith("Candidate:"))
        if candidate == "(none)":
            raise RuntimeError(f"No stable candidate for {package}")
        versions[package] = candidate
    if not LOCK.exists():
        LOCK.write_text(json.dumps({
            "source": "https://download.docker.com/linux/ubuntu", "suite": "noble",
            "component": "stable", "architecture": arch, "packages": versions,
        }, indent=2) + "\n")
    print(LOCK.read_text())


def install():
    lock = json.loads(LOCK.read_text())
    run("apt-get", "install", "-y", "--no-install-recommends",
        *(f"{package}={lock['packages'][package]}" for package in PACKAGES))
    user = tomllib.loads((ROOT / "environments/dev.toml").read_text())["linux"]["user"]
    account = pwd.getpwnam(user)
    if account.pw_uid == 0:
        raise RuntimeError("Do not select root as the project development user.")
    # Isolated CLI configuration leaves Windows/Desktop credentials and contexts alone.
    config = Path(account.pw_dir) / ".config/ev-dt/docker"
    config.mkdir(parents=True, mode=0o700, exist_ok=True)
    for directory in [config.parent.parent, config.parent, config]:
        os.chown(directory, account.pw_uid, account.pw_gid)
    config_file = config / "config.json"
    if not config_file.exists():
        config_file.write_text("{}\n")
        config_file.chmod(0o600)
        os.chown(config_file, account.pw_uid, account.pw_gid)
    run("usermod", "-aG", "docker", user)
    run("systemctl", "enable", "--now", "docker.service", "containerd.service")
    run("systemctl", "is-active", "docker.service")
    run("/usr/bin/docker", "--host", "unix:///var/run/docker.sock", "version")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "install"])
    args = parser.parse_args()
    if os.getuid() != 0:
        parser.error("Run through WSL as Ubuntu root for package installation only.")
    if 'VERSION_ID="24.04"' not in Path("/etc/os-release").read_text():
        parser.error("This bootstrap targets Ubuntu 24.04; review for another release.")
    if subprocess.check_output(["ps", "-p", "1", "-o", "comm="], text=True).strip() != "systemd":
        parser.error("systemd must manage the engine; an unmanaged daemon is not supported.")
    (prepare if args.action == "prepare" else install)()


if __name__ == "__main__":
    main()
