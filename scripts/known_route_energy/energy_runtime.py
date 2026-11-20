"""Trusted host controller for the restricted energy worker, never packaged in it."""

import hashlib
import json
import os
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE_TAG = "evdt-energy-worker:p2"
POLICY = ROOT / "qualification/energy/runtime-policy.json"
PACKAGED = [
    ROOT / "src/evdt/__init__.py",
    *[
        ROOT / f"src/evdt/energy/{name}.py"
        for name in ("__init__", "model", "observations", "forecasts", "jobs", "worker")
    ],
]


@lru_cache(maxsize=1)
def linux_checkout():
    if os.name != "nt":
        return str(ROOT)
    return subprocess.check_output(
        [sys.executable, str(ROOT / "scripts/linux.py"), "--", "pwd"],
        cwd=ROOT,
        text=True,
        timeout=30,
    ).strip()


def docker(*argv, input_bytes=None, timeout=120, check=True):
    prefix = (
        [sys.executable, str(ROOT / "scripts/linux.py"), "--timeout", str(timeout), "--"]
        if os.name == "nt"
        else []
    )
    result = subprocess.run(
        [*prefix, "docker", *argv],
        cwd=ROOT,
        input=input_bytes,
        capture_output=True,
        timeout=timeout + 15,
    )
    if check and result.returncode:
        raise RuntimeError(f"Docker command failed: {argv[0]}; exit {result.returncode}")
    return result


def fingerprints():
    paths = [
        *PACKAGED,
        ROOT / "containers/energy-worker.Dockerfile",
        POLICY,
        ROOT / "environments/dataflow-images.json",
        Path(__file__),
        ROOT / "scripts/known_route_energy/run_energy_job.py",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }


def policy():
    data = json.loads(POLICY.read_bytes())
    # These are fixed local policy 1.0.0 requirements, not optional CLI preferences.
    expected = {
        "version": "energy-worker-sandbox/1.0.0",
        "user": "10001:10001",
        "network": "none",
        "read_only": True,
        "cap_drop": ["ALL"],
        "no_new_privileges": True,
        "memory_bytes": 268435456,
        "memory_swap_bytes": 268435456,
        "cpus": 1,
        "pids_limit": 32,
        "timeout_s": 30,
        "mounts": [],
        "published_ports": [],
    }
    if {k: data.get(k) for k in expected} != expected or set(data) != set(expected) | {"scope"}:
        raise ValueError("Unsupported worker policy; review and requalify a successor")
    return data


def build_image(output):
    before = fingerprints()
    base = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())["python"]["digest"]
    result = docker(
        "build",
        "--network",
        "none",
        "--platform",
        "linux/amd64",
        "--build-arg",
        "PYTHON_IMAGE=" + base,
        "-f",
        "containers/energy-worker.Dockerfile",
        "-t",
        IMAGE_TAG,
        ".",
        timeout=180,
    )
    (output / "build.log").write_bytes(result.stdout + result.stderr)
    image = json.loads(docker("image", "inspect", IMAGE_TAG).stdout)[0]
    if before != fingerprints():
        raise RuntimeError("Worker source changed during build")
    return image["Id"]


def create_args(image_id, name, owner, *, command=None):
    p = policy()
    args = [
        "create",
        "--name",
        name,
        "--label",
        "evdt.energy.owner=" + owner,
        "--interactive",
        "--read-only",
        "--network",
        "none",
        "--user",
        p["user"],
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--memory",
        str(p["memory_bytes"]),
        "--memory-swap",
        str(p["memory_swap_bytes"]),
        "--cpus",
        str(p["cpus"]),
        "--pids-limit",
        str(p["pids_limit"]),
    ]
    if command is not None:
        args.extend(["--entrypoint", "python"])
    return [*args, image_id, *(command or [])]


def verify_container(info):
    p = policy()
    host = info["HostConfig"]
    valid = (
        info["Config"]["User"] == p["user"]
        and host["NetworkMode"] == "none"
        and host["ReadonlyRootfs"] is True
        and host["Privileged"] is False
        and host["CapDrop"] == ["ALL"]
        and not host.get("CapAdd")
        and "no-new-privileges:true" in host["SecurityOpt"]
        and host["Memory"] == p["memory_bytes"]
        and host["MemorySwap"] == p["memory_swap_bytes"]
        and host["NanoCpus"] == 1_000_000_000
        and host["PidsLimit"] == p["pids_limit"]
        and host["PidMode"] == ""
        and host["IpcMode"] == "private"
        and not info["Mounts"]
        and not host.get("PortBindings")
    )
    if not valid:
        raise RuntimeError("Worker isolation settings do not match policy")


def remove_owned(name, owner):
    result = docker("container", "inspect", name, check=False)
    if result.returncode:
        # A daemon error is not evidence that cleanup succeeded.
        remaining = docker("ps", "-aq", "--filter", "name=^/" + name + "$")
        if remaining.stdout.strip():
            raise RuntimeError("Could not inspect a container that still exists")
        return
    info = json.loads(result.stdout)[0]
    if info["Config"].get("Labels", {}).get("evdt.energy.owner") != owner:
        raise RuntimeError("Refuse cleanup of an unowned container")
    if info["State"]["Running"]:
        docker("stop", "--time", "2", name)
    docker("rm", name)


@lru_cache(maxsize=8)
def image_environment(image_id):
    return json.loads(docker("image", "inspect", image_id).stdout)[0]["Config"]["Env"]


def run_isolated(image_id, raw, *, command=None):
    owner = uuid.uuid4().hex
    name = "evdt-energy-" + owner
    try:
        docker(*create_args(image_id, name, owner, command=command))
        info = json.loads(docker("inspect", name).stdout)[0]
        verify_container(info)
        if info["Image"] != image_id or info["Config"]["Env"] != image_environment(image_id):
            raise RuntimeError("Unexpected image or injected worker environment")
        result = docker(
            "start",
            "--attach",
            "--interactive",
            name,
            input_bytes=raw,
            timeout=policy()["timeout_s"],
            check=False,
        )
        after = json.loads(docker("inspect", name).stdout)[0]
        if after["State"]["Running"] or after["State"]["OOMKilled"]:
            raise RuntimeError("Worker failed to exit normally or ran out of memory")
        return {
            "exit_code": after["State"]["ExitCode"],
            "controller_exit_code": result.returncode,
            "stdout": result.stdout.decode("utf-8"),
            "stderr": result.stderr.decode("utf-8"),
            "policy_verified": True,
        }
    finally:
        remove_owned(name, owner)
