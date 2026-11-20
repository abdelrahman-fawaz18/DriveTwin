"""Prepare the Ubuntu controller environment from pinned project dependencies."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    if os.name == "nt" or sys.version_info[:2] != (3, 12):
        parser.error("Run through scripts/linux.py with Ubuntu Python 3.12")
    folder = ROOT / ".runtime/energy-v2" / ("setup-" + uuid4().hex)
    folder.mkdir(parents=True)
    image = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())["uv"]["digest"]
    name = "evdt-energy-" + folder.name
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "65532:65532",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=32m",
        "--workdir",
        "/project",
        "--env",
        "UV_CACHE_DIR=/tmp/uv-cache",
    ]
    for filename in ("pyproject.toml", "uv.lock"):
        command += [
            "--mount",
            f"type=bind,source={ROOT / filename},target=/project/{filename},readonly",
        ]
    command += [
        "--entrypoint",
        "/uv",
        image,
        "export",
        "--frozen",
        "--offline",
        "--no-emit-project",
        "--format",
        "requirements-txt",
    ]
    with (folder / "export.log").open("xb") as log:
        try:
            result = subprocess.run(
                command, cwd=ROOT, stdout=subprocess.PIPE, stderr=log, timeout=60, check=True
            )
        finally:
            # The name belongs only to this invocation; never prune other resources.
            subprocess.run(
                ["docker", "rm", "-f", name], stdout=log, stderr=log, timeout=20, check=False
            )
    requirements = folder / "requirements.txt"
    requirements.write_bytes(result.stdout)
    (folder / "source.json").write_text(
        json.dumps(
            {
                "uv_image": image,
                "inputs": {
                    filename: hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
                    for filename in (
                        "uv.lock",
                        "pyproject.toml",
                        "environments/energy-geospatial-requirements.txt",
                    )
                },
                "export_sha256": hashlib.sha256(result.stdout).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    if args.export_only:
        print("Locked dependencies exported; no environment changed: " + str(requirements))
        return
    environment = ROOT / ".runtime/energy-v2/venv"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, timeout=120)
    python = str(environment / "bin/python")
    subprocess.run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            str(requirements),
            "-r",
            str(ROOT / "environments/energy-geospatial-requirements.txt"),
        ],
        check=True,
        timeout=600,
    )
    subprocess.run([python, "-m", "pip", "check"], check=True, timeout=60)
    print("Controller environment ready. No vehicle or application service started.")


if __name__ == "__main__":
    main()
