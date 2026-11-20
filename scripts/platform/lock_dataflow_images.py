"""Resolve discovery tags once; later runs pull the recorded immutable images."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "environments/dataflow-images.json"
TAGS = {
    "python": "python:3.12-slim-bookworm",
    "uv": "ghcr.io/astral-sh/uv:latest",
    "postgres": "postgres:18-alpine",
    "mosquitto": "eclipse-mosquitto:2.0",
}


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    info = json.loads(docker("info", "--format", "{{json .}}"))
    if (
        info["OperatingSystem"] != "Ubuntu 24.04.5 LTS"
        or info["DockerRootDir"] != "/var/lib/docker"
    ):
        raise SystemExit("Unexpected engine; refusing to continue.")
    records = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    for name, tag in TAGS.items():
        ref = records.get(name, {}).get("digest", tag)
        subprocess.run(["docker", "pull", "--platform", "linux/amd64", ref], check=True)
        data = json.loads(docker("image", "inspect", ref))[0]
        records[name] = {
            "discovery_tag": tag,
            "digest": sorted(data["RepoDigests"])[0],
            "image_id": data["Id"],
            "platform": "linux/amd64",
        }
        LOCK.write_text(json.dumps(records, indent=2) + "\n")
    print("Locked", len(records), "images in", LOCK.relative_to(ROOT))


if __name__ == "__main__":
    main()
