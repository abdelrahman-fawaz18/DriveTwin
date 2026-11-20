"""Prepare pinned simulator inputs without regenerating qualification drives."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIMULATOR = "evdt-energy-collector:p2"
SOURCE_LABEL = "org.evdt.source-inventory"
NETWORK = ROOT / "evidence/p4/station-arrivals-02/network.net.xml"
RELEASE = ROOT / "qualification/charging/station-arrival-release-1.0.0.json"
HEADER = ROOT / "qualification/release/station-network-header.txt"


def image_tag(inventory_hash):
    return f"evdt-release-dashboard:{inventory_hash}"


def docker(*args, timeout=120, check=True):
    return subprocess.run(["docker", *args], cwd=ROOT, timeout=timeout, check=check)


def build_image(pins, target, tag, *options):
    build_args = []
    for name in ("python", "uv", "node"):
        build_args.extend(("--build-arg", f"{name.upper()}_IMAGE={pins[name]['digest']}"))
    docker(
        "build",
        "--platform",
        "linux/amd64",
        "--target",
        target,
        "-f",
        "containers/monitoring.Dockerfile",
        *build_args,
        *options,
        "-t",
        tag,
        ".",
        timeout=600,
    )


def ensure_images():
    pins = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())
    pins.update(json.loads((ROOT / "environments/engineering-images.json").read_bytes()))
    for image in (pins["postgres"]["digest"], pins["mosquitto"]["digest"]):
        if docker("image", "inspect", "--format", "{{.Id}}", image, check=False).returncode:
            docker("pull", "--platform", "linux/amd64", image, timeout=300)
    if docker("image", "inspect", "--format", "{{.Id}}", SIMULATOR, check=False).returncode:
        build_image(pins, "simulator", SIMULATOR)
    return pins


def prepare_dashboard(pins):
    from qualify_restarts import source

    before = source()
    fingerprint = before["inventory"]["sha256"]
    build_image(pins, "runtime", image_tag(fingerprint), "--label", f"{SOURCE_LABEL}={fingerprint}")
    if source() != before:
        raise RuntimeError("Release sources changed during the dashboard build")


def exact_network(generated, expected):
    # netconvert adds a timestamp and command paths in its leading comment.
    # Restore only those bytes from the archived public-map build header.
    prefix, marker, body = generated.partition(b"-->")
    if not marker or b"Eclipse SUMO netconvert 1.28.0" not in prefix:
        raise ValueError("Unexpected netconvert metadata")
    if not body.startswith(b"\n\n<net "):
        raise ValueError("Unexpected generated network root")
    restored = HEADER.read_bytes().rstrip(b"\n") + body
    if hashlib.sha256(restored).hexdigest() != expected:
        raise ValueError("Reconstructed network does not match the frozen charging release")
    return restored


def prepare_network(reconstruct):
    expected = json.loads(RELEASE.read_bytes())["network_sha256"]
    if NETWORK.exists() and hashlib.sha256(NETWORK.read_bytes()).hexdigest() != expected:
        raise ValueError("Existing network differs from the frozen charging release")
    if NETWORK.exists() and not reconstruct:
        print("Verified existing charging network.")
        return
    scratch = ROOT / ".runtime"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-map-", dir=scratch) as tmp:
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "384m",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:size=32m,mode=1777",
            "--mount",
            f"type=bind,src={ROOT},dst=/workspace,readonly",
            "--mount",
            f"type=bind,src={tmp},dst=/output",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONPATH=/workspace/src",
            SIMULATOR,
            "python",
            "-B",
            "-c",
            "from pathlib import Path; from evdt.monitoring.network import build_network; "
            "build_network(Path('simulation/montreal'), "
            "Path('data/public/montreal-core-v1/roads.osm.xml'), Path('/output'))",
        )
        restored = exact_network((Path(tmp) / "network.net.xml").read_bytes(), expected)
        if not NETWORK.exists():
            NETWORK.parent.mkdir(parents=True, exist_ok=True)
            with NETWORK.open("xb") as handle:
                handle.write(restored)
    print("Reproduced the exact charging network.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconstruct-network", action="store_true")
    parser.add_argument("--dashboard", action="store_true")
    args = parser.parse_args()
    if os.name == "nt":
        parser.error(
            "Run with python scripts/linux.py -- python3 scripts/release/prepare_simulation.py"
        )
    pins = ensure_images()
    prepare_network(args.reconstruct_network)
    if args.dashboard:
        prepare_dashboard(pins)


if __name__ == "__main__":
    main()
