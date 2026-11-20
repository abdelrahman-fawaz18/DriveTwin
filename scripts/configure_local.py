"""Generate ignored Windows/WSL settings without putting an account in source."""

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Native Linux checks do not need a Windows/WSL settings file")
    target = ROOT / "environments/dev.toml"
    if target.exists():
        print("Existing ignored local configuration preserved.")
        return
    result = subprocess.run(
        [
            "wsl.exe",
            "--distribution",
            args.distribution,
            "--exec",
            "python3",
            "-c",
            "import json,os,getpass; print(json.dumps({'user':getpass.getuser(),'uid':os.getuid(),'home':os.path.expanduser('~')}))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    account = json.loads(result.stdout)
    if account["uid"] == 0:
        raise SystemExit("Create an ordinary Ubuntu account before configuring development.")
    config = (
        "# Machine-specific settings; do not commit this file.\n[linux]\n"
        f"distribution = {json.dumps(args.distribution)}\nuser = {json.dumps(account['user'])}\n"
        'windows_mount_root = "/mnt"\nheartbeat_timeout_seconds = 15.0\ntermination_grace_seconds = 2.0\n\n'
        '[linux.environment]\nDOCKER_HOST = "unix:///var/run/docker.sock"\n'
        f"DOCKER_CONFIG = {json.dumps(account['home'] + '/.config/ev-dt/docker')}\n"
    )
    with target.open("x", encoding="utf-8", newline="\n") as output:
        output.write(config)
    print("Created ignored local configuration at environments/dev.toml.")


if __name__ == "__main__":
    main()
