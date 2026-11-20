"""Trusted Linux-host launcher for one restricted route-energy worker."""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE_TAG = "evdt-route-energy:development"
MEMORY_BYTES = 268435456
PACKAGED = (
    "__init__",
    "validation",
    "contracts",
    "policy",
    "wire",
    "scenarios",
    "environment",
    "geometry",
    "motion",
    "power",
    "signals",
    "ledger",
    "forecast",
    "worker",
)
CHARGING_PACKAGED = (
    "moving_charge_worker",
    "moving_charge_candidate",
    "moving_charge_search",
    "moving_charge_leg",
    "moving_charge_station",
    "moving_charge_transfer",
)


def docker(*args, timeout=10, check=True, input_bytes=None):
    result = subprocess.run(
        ["docker", *args], input=input_bytes, capture_output=True, cwd=ROOT, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed with exit {result.returncode}")
    return result


def source_hashes():
    paths = [ROOT / f"src/evdt/energy/route_aware/{name}.py" for name in PACKAGED]
    paths += [ROOT / f"src/evdt/energy/{name}.py" for name in CHARGING_PACKAGED]
    paths += [
        ROOT / "src/evdt/__init__.py",
        ROOT / "src/evdt/energy/__init__.py",
        ROOT / "containers/energy-route-worker.Dockerfile",
        ROOT / "environments/dataflow-images.json",
        ROOT / "qualification/energy/route-aware/runtime-policy.json",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }


def build_image(output):
    before = source_hashes()
    base = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())["python"]["digest"]
    result = docker(
        "build",
        "--network",
        "none",
        "--platform",
        "linux/amd64",
        "--build-arg",
        "PYTHON_IMAGE=" + base,
        "--label",
        "drivetwin.route-energy.source="
        + hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        "-f",
        "containers/energy-route-worker.Dockerfile",
        "-t",
        IMAGE_TAG,
        ".",
        timeout=180,
    )
    (output / "worker-build.log").write_bytes(result.stdout + result.stderr)
    if before != source_hashes():
        raise RuntimeError("Worker source changed during image build")
    info = json.loads(docker("image", "inspect", IMAGE_TAG).stdout)[0]
    return info["Id"]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Build the isolated numerical worker; start no services"
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if os.name == "nt":
        parser.error("Run through the project Linux launcher")
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / "evidence/energy-v2") or output.exists():
        raise ValueError("Choose a new ignored evidence/energy-v2 build folder")
    output.mkdir(parents=True)
    image = build_image(output)
    verify_image(image)
    record = {"worker_image_id": image, "source_sha256": source_hashes(), "worker_started": False}
    (output / "worker-image.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(image)


def verify_image(image):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("Immutable worker image identity required")
    info = json.loads(docker("image", "inspect", image).stdout)[0]
    expected = hashlib.sha256(json.dumps(source_hashes(), sort_keys=True).encode()).hexdigest()
    if info["Config"].get("Labels", {}).get("drivetwin.route-energy.source") != expected:
        raise ValueError("Worker image does not match the current numerical source")
    if info["Config"].get("Entrypoint") != [
        "python",
        "-I",
        "-B",
        "-m",
        "evdt.energy.route_aware.worker",
    ]:
        raise ValueError("Worker image entry point differs")


def worker_name(fence):
    if isinstance(fence, bool) or not isinstance(fence, int) or not 1 <= fence <= 9007199254740991:
        raise ValueError("Positive durable fence required")
    return f"evdt-route-energy-{fence}"


def remove_owned(fence, owner):
    name = worker_name(fence)
    result = docker("container", "inspect", name, check=False)
    if result.returncode:
        listed = docker("ps", "-aq", "--filter", "name=^/" + name + "$")
        if listed.stdout.strip():
            raise RuntimeError("Worker still exists but could not be inspected")
        return
    info = json.loads(result.stdout)[0]
    labels = info["Config"].get("Labels", {})
    if labels.get("drivetwin.route-energy.owner") != owner or labels.get(
        "drivetwin.route-energy.fence"
    ) != str(fence):
        raise RuntimeError("Refuse cleanup of a foreign worker")
    if info["State"]["Running"]:
        docker("stop", "--time", "2", name)
    docker("rm", name)
    if docker("ps", "-aq", "--filter", "name=^/" + name + "$").stdout.strip():
        raise RuntimeError("Owned worker cleanup incomplete")


def verify_container(info, image, owner, fence):
    host = info["HostConfig"]
    labels = info["Config"].get("Labels", {})
    valid = (
        info["Image"] == image
        and info["Config"]["User"] == "10001:10001"
        and labels.get("drivetwin.route-energy.owner") == owner
        and labels.get("drivetwin.route-energy.fence") == str(fence)
        and host["NetworkMode"] == "none"
        and host["ReadonlyRootfs"] is True
        and host["Privileged"] is False
        and host["CapDrop"] == ["ALL"]
        and not host.get("CapAdd")
        and "no-new-privileges:true" in host["SecurityOpt"]
        and host["Memory"] == MEMORY_BYTES
        and host["MemorySwap"] == MEMORY_BYTES
        and host["NanoCpus"] == 1_000_000_000
        and host["PidsLimit"] == 32
        and host["PidMode"] == ""
        and host["IpcMode"] == "private"
        and not info["Mounts"]
        and not host.get("PortBindings")
    )
    if not valid:
        raise RuntimeError("Worker isolation settings differ from policy")


def _execute_isolated(image, fence, owner, raw, deadline_monotonic_s, *, module=None):
    name = worker_name(fence)
    created = False
    try:
        labels = []
        project = os.environ.get("EVDT_ENERGY_RESOURCE_PROJECT")
        if project:
            if not re.fullmatch(r"evdt-e2c-[a-f0-9]{12}", project):
                raise ValueError("Invalid qualification resource project")
            labels = [
                "--label",
                "com.docker.compose.project=" + project,
                "--label",
                "com.docker.compose.service=energy-worker",
                "--label",
                "com.docker.compose.oneoff=False",
            ]
        command = ["--entrypoint", "python"] if module is not None else []
        args = ["-I", "-B", "-m", module] if module is not None else []
        docker(
            "create",
            "--name",
            name,
            "--label",
            "drivetwin.route-energy.owner=" + owner,
            "--label",
            "drivetwin.route-energy.fence=" + str(fence),
            "--interactive",
            "--read-only",
            "--network",
            "none",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            str(MEMORY_BYTES),
            "--memory-swap",
            str(MEMORY_BYTES),
            "--cpus",
            "1",
            "--pids-limit",
            "32",
            *labels,
            *command,
            image,
            *args,
        )
        created = True
        info = json.loads(docker("container", "inspect", name).stdout)[0]
        verify_container(info, image, owner, fence)
        if module is not None and (
            info["Config"]["Entrypoint"] != ["python"] or info["Args"] != args
        ):
            raise RuntimeError("Charging worker entry point differs")
        remaining = deadline_monotonic_s - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Worker claim deadline expired before start")
        result = docker(
            "start",
            "--attach",
            "--interactive",
            name,
            input_bytes=raw,
            timeout=remaining,
            check=False,
        )
        finished = json.loads(docker("container", "inspect", name).stdout)[0]
        if result.returncode or finished["State"]["OOMKilled"] or finished["State"]["ExitCode"]:
            raise RuntimeError("Restricted energy worker failed")
        return result.stdout
    finally:
        # Even a partially successful create can leave a named container.
        if created or docker("ps", "-aq", "--filter", "name=^/" + name + "$").stdout.strip():
            remove_owned(fence, owner)


def run_isolated(image, claim, *, deadline_monotonic_s):
    """May run on one executor thread; never accesses a database connection."""
    from evdt.energy.route_aware.forecast import parse_result, validate_result
    from evdt.energy.route_aware.wire import canonical_bytes

    raw = canonical_bytes(claim.request)
    if len(raw) > claim.request.policy.max_request_bytes:
        raise ValueError("Worker input budget exceeded")
    result = _execute_isolated(image, claim.fence, claim.owner, raw, deadline_monotonic_s)
    parsed = parse_result(result)
    validate_result(claim.request, parsed)
    return parsed


def run_charging_isolated(image, claim, request):
    """Use the same owned worker slot for a provisional candidate calculation."""
    from evdt.energy.moving_charge_worker import parse_result, validate_planning_result
    from evdt.energy.route_aware.wire import MAX_BYTES, canonical_bytes

    if request.baseline != claim.request:
        raise ValueError("Charging worker input differs from the held forecast claim")
    raw = canonical_bytes(request)
    if len(raw) > MAX_BYTES:
        raise ValueError("Charging worker input budget exceeded")
    result = _execute_isolated(
        image,
        claim.fence,
        claim.owner,
        raw,
        claim.deadline_monotonic_s,
        module="evdt.energy.moving_charge_worker",
    )
    parsed = parse_result(result)
    validate_planning_result(request, parsed)
    return parsed


if __name__ == "__main__":
    main()
