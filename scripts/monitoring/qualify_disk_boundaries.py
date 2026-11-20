"""Qualify only new owned file-backed disk boundaries; run this provisioner as Ubuntu root."""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE = "queue-disk-boundaries/1.0.0"


class TemporaryStorageReleased(Exception):
    """Exit the temporary serving branch through the common guarded cleanup."""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def owned_volume(value, name, owner):
    require(
        re.fullmatch(r"evdt-disk-[0-9a-f]{32}", owner)
        and name in {owner + "-" + role for role in ("backing", "telemetry", "critical")},
        "Volume is outside the closed fixture namespace",
    )
    require(value.get("Name") == name and value.get("Driver") == "local", "Volume identity differs")
    labels = value.get("Labels") or {}
    require(
        labels.get("com.evdt.owner") == owner and labels.get("com.evdt.scope") == SCOPE,
        "Volume ownership differs",
    )


def backing_directory(value, docker_root, name, owner):
    owned_volume(value, name, owner)
    expected = docker_root.resolve() / "volumes" / name / "_data"
    actual = Path(value["Mountpoint"])
    require(
        actual.is_absolute() and actual == expected and actual.resolve() == expected,
        "Backing volume path escaped its exact daemon-owned directory",
    )
    require(
        actual.is_dir() and not actual.is_symlink(),
        "Backing volume is not an ordinary owned directory",
    )
    return actual


def checked_file(path, parent, size, original=None):
    require(
        path.parent == parent and path.name in ("telemetry.ext4", "critical.ext4"),
        "Disk image path is outside the approved owned files",
    )
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == size,
        "Disk image is linked, not regular or has changed size",
    )
    identity = {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size}
    require(original is None or identity == original, "Owned disk image identity changed")
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--critical-fault",
        action="store_true",
        help="Also qualify preallocated fault status under actual full-disk conditions",
    )
    parser.add_argument(
        "--queue-image",
        help="Exact local application image ID for the explicit queue integration mode",
    )
    parser.add_argument(
        "--serve-control",
        type=Path,
        help="New private coordination directory for temporary vehicle storage",
    )
    parser.add_argument(
        "--vehicle-identity",
        type=Path,
        help="Explicit run/asset/source identity for temporary vehicle storage",
    )
    parser.add_argument("--serve-seconds", type=int, default=3600)
    parser.add_argument("--encoded-limit", type=int, default=32 * 1024**2)
    parser.add_argument("--catalog-version", default="1.4.0")
    args = parser.parse_args()
    require(
        not args.serve_control
        or (
            args.queue_image
            and args.critical_fault
            and args.vehicle_identity
            and 1 <= args.serve_seconds <= 3600
            and 0 < args.encoded_limit <= 32 * 1024**2
        ),
        "Temporary vehicle storage requires an exact image, identity and bounded lifetime",
    )
    require(
        args.serve_control
        or (args.vehicle_identity is None and args.encoded_limit == 32 * 1024**2),
        "Vehicle settings require the explicit temporary storage mode",
    )
    require(
        not args.queue_image
        or (args.critical_fault and re.fullmatch(r"sha256:[0-9a-f]{64}", args.queue_image)),
        "Queue integration requires critical-fault mode and an exact local image ID",
    )
    require(
        os.name != "nt" and os.geteuid() == 0,
        "Use the scoped Ubuntu-root provisioner; normal commands remain regular-user",
    )
    output = args.evidence_dir.resolve()
    require(
        output.is_relative_to(ROOT / "evidence/p1") and not output.exists(),
        "Use a new private output directory under evidence/p1",
    )
    output.mkdir(parents=True)
    serve_control = args.serve_control.resolve() if args.serve_control else None
    if serve_control:
        require(
            serve_control.is_relative_to(ROOT / ".runtime/bounded-storage")
            and not serve_control.exists(),
            "Use a new private temporary-storage control directory",
        )
        serve_control.mkdir(parents=True)
    profile = json.loads((ROOT / "benchmarks/storage.json").read_bytes())
    require(
        profile.get("version") == SCOPE
        and [
            profile.get(k)
            for k in (
                "encoded_telemetry_bytes",
                "telemetry_disk_bytes",
                "critical_disk_bytes",
                "host_free_reserve_bytes",
            )
        ]
        == [32 * 1024**2, 64 * 1024**2, 8 * 1024**2, 20 * 1024**3],
        "Frozen storage budgets differ",
    )
    sources = [
        Path(__file__),
        ROOT / "scripts/monitoring/disk_boundary_probe.py",
        ROOT / "benchmarks/storage.json",
        ROOT / "environments/dataflow-images.json",
    ]
    if args.critical_fault:
        sources.extend(sorted(ROOT.glob("src/**/*.py")))
    if args.queue_image:
        sources.extend(
            [
                ROOT / "benchmarks/storage-queue.json",
                ROOT / "benchmarks/workloads.json",
                *sorted(ROOT.glob("contracts/**/*.json")),
                *sorted(ROOT.glob("catalog/*.json")),
            ]
        )

    def fingerprints():
        return {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources
        }

    before = fingerprints()
    owner = "evdt-disk-" + uuid.uuid4().hex
    names = {role: owner + "-" + role for role in ("backing", "telemetry", "critical")}
    sizes = {role: profile[role + "_disk_bytes"] for role in ("telemetry", "critical")}
    record = {
        "passed": False,
        "scope": SCOPE,
        "owner": owner,
        "profile": profile,
        "source_sha256": before,
        "commands": [],
        "disks": {},
        "checks": {},
        "phase_1_complete": False,
        "vehicle_storage_integration_passed": False,
    }
    if args.critical_fault:
        record["fault_identity"] = {
            "run_id": str(uuid.uuid4()),
            "asset_id": "bus-001",
            "source_id": "storage-probe",
        }
        record["critical_fault_mode"] = True
        if args.queue_image:
            record["fault_identity"]["source_id"] = "sumo-adapter"
            record["queue_mode"] = True
        if serve_control:
            identity_raw = args.vehicle_identity.read_bytes()
            require(len(identity_raw) <= 1024, "Vehicle identity exceeds its bound")
            identity = json.loads(identity_raw)
            require(
                isinstance(identity, dict)
                and set(identity) == {"run_id", "asset_id", "source_id"}
                and str(uuid.UUID(identity["run_id"])) == identity["run_id"]
                and all(
                    isinstance(identity[k], str)
                    and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", identity[k])
                    for k in ("asset_id", "source_id")
                ),
                "Invalid vehicle identity",
            )
            record["fault_identity"] = identity
            record["queue_mode"] = False
            record["temporary_vehicle_storage_mode"] = True
    queue_control = None
    queue_control_file = output / "queue-control.json"
    client_environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
    }
    containers = []
    eligible_volumes = set()
    docker_root = None
    parent = None
    log = (output / "commands.log").open("w", encoding="utf-8")

    def save_state():
        record["eligible_volume_names"] = sorted(eligible_volumes)
        temporary = output / "state.json.tmp"
        temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / "state.json")

    def interrupted(signum, frame):
        raise InterruptedError("Owned filesystem qualification interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    save_state()

    def execute(argv, *, check=True, timeout=60):
        start = time.monotonic()
        result = subprocess.run(
            argv, cwd=ROOT, env=client_environment, capture_output=True, text=True, timeout=timeout
        )
        record["commands"].append(
            {"argv": argv, "exit_code": result.returncode, "elapsed_s": time.monotonic() - start}
        )
        log.write(json.dumps(argv) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        save_state()
        require(
            not check or result.returncode == 0, "Owned filesystem command failed; see private log"
        )
        return result

    def docker(*args, **kwargs):
        return execute(["docker", "--host", "unix:///var/run/docker.sock", *args], **kwargs)

    def inspect(name):
        result = docker("volume", "inspect", name, check=False)
        if result.returncode:
            require(
                not docker("volume", "ls", "-q", "--filter", "name=^" + name + "$").stdout.strip(),
                "Volume inspection failed for an existing name",
            )
            return None
        values = json.loads(result.stdout)
        require(len(values) == 1, "Ambiguous volume inspection")
        return values[0]

    def associated(path):
        rows = json.loads(
            execute(
                [
                    "losetup",
                    "--list",
                    "--json",
                    "--associated",
                    str(path),
                    "--output",
                    "NAME,BACK-FILE",
                ]
            ).stdout
        )["loopdevices"]
        for row in rows:
            require(
                re.fullmatch(r"/dev/loop[0-9]+", row["name"])
                and Path(row["back-file"]).resolve() == path.resolve(),
                "Kernel loop association differs from the owned file",
            )
        return rows

    def loop_guard(role):
        disk = record["disks"][role]
        checked_file(Path(disk["path"]), parent, sizes[role], disk["file_identity"])
        rows = associated(Path(disk["path"]))
        require(
            len(rows) == 1 and rows[0]["name"] == disk["device"],
            "Loop device was replaced or duplicated",
        )
        info = os.stat(disk["device"])
        require(
            stat.S_ISBLK(info.st_mode) and os.major(info.st_rdev) == 7,
            "Owned loop device identity differs",
        )

    def probe(action, role="telemetry", reason=None):
        queue_action = action.startswith("queue-")
        vehicle_action = action.startswith("vehicle-")
        if queue_action or vehicle_action:
            queue_control_file.write_text(
                json.dumps(queue_control, sort_keys=True) + "\n", encoding="utf-8"
            )
        name = owner + "-probe-" + str(len(containers))
        require(
            docker("container", "inspect", name, check=False).returncode != 0,
            "Refusing an existing helper container",
        )
        containers.append(name)
        argv = [
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "com.evdt.owner=" + owner,
            "--label",
            "com.evdt.scope=" + SCOPE,
            "--network",
            "none",
            "--read-only",
            "--memory",
            "256m" if queue_action or vehicle_action else "96m",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--user",
            "0:0" if action == "initialize" else "10001:10001",
        ]
        if action == "initialize":
            argv.extend(["--cap-add", "CHOWN"])
        if args.critical_fault:
            argv.extend(
                [
                    "--mount",
                    "type=bind,src=" + str(ROOT / "src") + ",dst=/app/src,readonly",
                    "--env",
                    "PYTHONPATH=/app/src",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--env",
                    "PYTHONPYCACHEPREFIX=/unused-evdt-pycache",
                ]
            )
        for selected in ("telemetry", "critical"):
            loop_guard(selected)
            value = inspect(names[selected])
            owned_volume(value, names[selected], owner)
            require(
                value.get("Options")
                == {
                    "type": "ext4",
                    "device": record["disks"][selected]["device"],
                    "o": "nosuid,nodev,noexec",
                },
                "Refusing changed filesystem volume options",
            )
            destination = "spool" if vehicle_action and selected == "telemetry" else selected
            argv.extend(["--mount", "type=volume,src=" + names[selected] + ",dst=/" + destination])
        if queue_action or vehicle_action:
            argv.extend(
                [
                    "--mount",
                    "type=bind,src="
                    + str(queue_control_file)
                    + ",dst=/queue-control.json,readonly",
                    "--entrypoint",
                    "python",
                    args.queue_image,
                    "-m",
                    "evdt.monitoring.vehicle_storage"
                    if vehicle_action
                    else "evdt.workload.disk_queue",
                    action.removeprefix("vehicle-")
                    if vehicle_action
                    else action.removeprefix("queue-"),
                    "--binding" if vehicle_action else "--control",
                    "/queue-control.json",
                ]
            )
        else:
            argv.extend(
                [
                    "--mount",
                    "type=bind,src="
                    + str(ROOT / "scripts/monitoring/disk_boundary_probe.py")
                    + ",dst=/probe.py,readonly",
                    "--entrypoint",
                    "python",
                    record["image_id"],
                    "/probe.py",
                    action,
                    "--role",
                    role,
                ]
            )
        if action.startswith("fault-"):
            argv.extend(["--fault-identity", json.dumps(record["fault_identity"], sort_keys=True)])
            if reason:
                argv.extend(["--fault-reason", reason])
        result = json.loads(docker(*argv, timeout=900 if queue_action else 120).stdout)
        require(result.get("passed") is True, "Ordinary filesystem check failed")
        if action != "initialize":
            require(
                0 < result["filesystem_bytes"] <= sizes[role],
                "Actual filesystem exceeds the frozen disk budget",
            )
            require(
                result["device"] == os.stat(record["disks"][role]["device"]).st_rdev,
                "Container did not mount the intended loop filesystem",
            )
        return result

    try:
        for tool in ("docker", "losetup", "mkfs.ext4", "blkid"):
            require(shutil.which(tool), "Required existing Ubuntu storage tool is missing")
        docker_root = Path(json.loads(docker("info", "--format", "{{json .DockerRootDir}}").stdout))
        require(docker_root.is_absolute(), "Daemon root is not absolute")
        required = profile["host_free_reserve_bytes"] + sum(sizes.values())
        require(
            shutil.disk_usage(ROOT).free >= required
            and shutil.disk_usage(docker_root).free >= required,
            "Host free-disk reserve would be violated",
        )
        image = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())["python"][
            "digest"
        ]
        record["image_id"] = docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()
        require(
            re.fullmatch(r"sha256:[0-9a-f]{64}", record["image_id"]),
            "Pinned base image is unavailable",
        )
        for name in names.values():
            require(inspect(name) is None, "Refusing an existing volume name")
        if args.queue_image:
            require(
                docker("image", "inspect", args.queue_image, "--format", "{{.Id}}").stdout.strip()
                == args.queue_image,
                "Exact queue application image is unavailable",
            )
            sample_spec = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())
            queue_profile = json.loads((ROOT / "benchmarks/storage-queue.json").read_bytes())
            image_sources = {
                p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for pattern in ("src/**/*.py", "contracts/**/*.json", "catalog/*.json")
                for p in ROOT.glob(pattern)
            }
            verifier = "import hashlib,json,sys;from pathlib import Path;names=json.loads(sys.argv[1]);print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest()for n in names}))"
            image_check_name = owner + "-probe-" + str(len(containers))
            require(
                docker("container", "inspect", image_check_name, check=False).returncode != 0,
                "Refusing an existing image-check helper",
            )
            containers.append(image_check_name)
            actual = json.loads(
                docker(
                    "run",
                    "--rm",
                    "--pull",
                    "never",
                    "--name",
                    image_check_name,
                    "--label",
                    "com.evdt.owner=" + owner,
                    "--label",
                    "com.evdt.scope=" + SCOPE,
                    "--network",
                    "none",
                    "--read-only",
                    "--memory",
                    "128m",
                    "--pids-limit",
                    "32",
                    "--cap-drop",
                    "ALL",
                    "--user",
                    "10001:10001",
                    "--entrypoint",
                    "python",
                    args.queue_image,
                    "-c",
                    verifier,
                    json.dumps(sorted(image_sources)),
                    timeout=120,
                ).stdout
            )
            require(
                actual == image_sources,
                "Actual packaged application inputs differ from the current source",
            )
            queue_control = {
                "profile": queue_profile,
                "identity": record["fault_identity"],
                "image_source_sha256": image_sources,
                "events": [],
                "samples": [
                    {
                        "signal_id": s["signal_id"],
                        "unit": s["unit"],
                        "value": s["planning_value"],
                        "quality": "good",
                        "provenance": "simulated-observation",
                    }
                    for s in sample_spec["signals"]
                ],
            }
            record["queue_application_image_id"] = args.queue_image
            record["queue_packaged_sources_match"] = True
        eligible_volumes.add(names["backing"])
        save_state()
        docker(
            "volume",
            "create",
            "--label",
            "com.evdt.owner=" + owner,
            "--label",
            "com.evdt.scope=" + SCOPE,
            names["backing"],
        )
        parent = backing_directory(inspect(names["backing"]), docker_root, names["backing"], owner)
        for role, size in sizes.items():
            path = parent / (role + ".ext4")
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            initial = os.fstat(descriptor)
            disk = {
                "path": str(path),
                "initial_inode": initial.st_ino,
                "initial_device": initial.st_dev,
            }
            record["disks"][role] = disk
            try:
                os.posix_fallocate(descriptor, 0, size)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            disk["file_identity"] = checked_file(path, parent, size)
            execute(["mkfs.ext4", "-q", "-F", "-m", "0", "-E", "nodiscard", str(path)])
            checked_file(path, parent, size, disk["file_identity"])
            disk["filesystem_uuid"] = execute(
                ["blkid", "-p", "-s", "UUID", "-o", "value", str(path)]
            ).stdout.strip()
            disk["device"] = execute(
                ["losetup", "--find", "--show", "--nooverlap", str(path)]
            ).stdout.strip()
            loop_guard(role)
            eligible_volumes.add(names[role])
            save_state()
            docker(
                "volume",
                "create",
                "--label",
                "com.evdt.owner=" + owner,
                "--label",
                "com.evdt.scope=" + SCOPE,
                "--driver",
                "local",
                "--opt",
                "type=ext4",
                "--opt",
                "device=" + disk["device"],
                "--opt",
                "o=nosuid,nodev,noexec",
                names[role],
            )
        record["checks"]["initial_ownership"] = probe("initialize")
        record["checks"]["telemetry_marker"] = probe("marker", "telemetry")
        if serve_control:
            critical = probe("marker", "critical")
            registry = json.loads((ROOT / "contracts/monitoring.profile.json").read_bytes())
            selected_format = registry["operational_formats"][args.catalog_version]
            queue_control = {
                "version": "vehicle-queue-storage/1.0.0",
                "mode": "nominal"
                if args.encoded_limit == profile["encoded_telemetry_bytes"]
                else "reduced-test",
                "owner": owner,
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "identity": record["fault_identity"],
                "queue": {
                    "catalog_version": args.catalog_version,
                    "schema_version": selected_format["schema_version"],
                    "encoded_limit_bytes": args.encoded_limit,
                    "loss_record_limit": 16384,
                },
                "roles": {
                    role: {
                        "device": value["device"],
                        "filesystem_bytes": value["filesystem_bytes"],
                        "image_bytes": sizes[role],
                    }
                    for role, value in (
                        ("telemetry", record["checks"]["telemetry_marker"]),
                        ("critical", critical),
                    )
                },
            }
            record["checks"]["vehicle_initialize"] = probe("vehicle-initialize")
            record["checks"]["vehicle_fresh_reopen"] = probe("vehicle-verify")
            ready = {
                "version": "temporary-vehicle-storage/1.0.0",
                "owner": owner,
                "volumes": names,
                "binding": queue_control,
            }
            pending = serve_control / "ready.tmp"
            pending.write_text(json.dumps(ready, sort_keys=True) + "\n", encoding="utf-8")
            pending.replace(serve_control / "ready.json")
            deadline = time.monotonic() + args.serve_seconds
            while True:
                release = serve_control / "release.json"
                if release.exists():
                    require(
                        release.is_file()
                        and not release.is_symlink()
                        and release.stat().st_size <= 1024
                        and json.loads(release.read_bytes()) == {"owner": owner},
                        "Temporary-storage release identity differs",
                    )
                    break
                require(time.monotonic() < deadline, "Temporary vehicle storage lease expired")
                time.sleep(0.25)
            for role in sizes:
                loop_guard(role)
            record["temporary_vehicle_storage_lifecycle_passed"] = True
            record["mechanism_checks_passed"] = True
            raise TemporaryStorageReleased
        if args.queue_image:
            for action in ("nominal", "capacity", "overflow"):
                result = probe("queue-" + action)
                queue_control["events"].extend(result.pop("added_originals"))
                queue_control["expected_inventory"] = result["inventory"]
                record["checks"]["queue_" + action] = result
                record["checks"]["queue_" + action + "_reopen"] = probe("queue-verify")
        for key, action, role in (
            ("telemetry_full", "fill", "telemetry"),
            ("critical_writable_with_telemetry_full", "marker", "critical"),
            ("telemetry_reopen_when_full", "check", "telemetry"),
            ("critical_reopen", "check", "critical"),
            ("critical_full", "fill", "critical"),
            ("telemetry_recover_with_critical_full", "clear", "telemetry"),
            ("critical_stays_full", "full", "critical"),
            ("telemetry_final_reopen", "check", "telemetry"),
        ):
            if args.critical_fault and key == "critical_full":
                record["checks"]["fault_initialize"] = probe("fault-init", "critical")
                record["checks"]["fault_telemetry_full"] = (
                    probe("queue-full-fault")
                    if args.queue_image
                    else probe("fault-trip", "critical", "telemetry_full")
                )
                record["checks"]["fault_first_reopen"] = probe("fault-check", "critical")
            record["checks"][key] = probe(action, role)
        if args.queue_image:
            record["checks"]["queue_after_full_failure_reopen"] = probe("queue-verify")
            reconciled = probe("queue-confirm-loss")
            queue_control["confirmed"] = True
            queue_control["expected_inventory"] = reconciled["inventory"]
            record["checks"]["queue_confirmed_never_published_loss"] = reconciled
            record["checks"]["queue_final_reopen"] = probe("queue-verify")
            record["queue_storage_integration_passed"] = True
        if args.critical_fault:
            record["checks"]["fault_critical_full"] = probe(
                "fault-trip", "critical", "critical_full"
            )
            record["checks"]["fault_final_reopen"] = probe("fault-check", "critical")
            first = record["checks"]["fault_first_reopen"]
            final = record["checks"]["fault_final_reopen"]
            require(
                first["fault_status"]["occurrences"] == 1
                and first["fault_status"]["first_reason"]
                == ("telemetry_sqlite_full" if args.queue_image else "telemetry_full"),
                "First persisted fault differs",
            )
            require(
                final["fault_status"]["occurrences"] == 2
                and final["fault_status"]["first_reason"]
                == ("telemetry_sqlite_full" if args.queue_image else "telemetry_full")
                and final["fault_status"]["last_reason"] == "critical_full"
                and final["valid_copies"] == 2
                and final["allocated_slot_bytes"] >= 8192
                and final["available_bytes"] == 0
                and not final["status_healthy"],
                "Full critical storage did not retain the synchronized sticky fault",
            )
            record["critical_fault_full_disk_passed"] = True
        require(
            record["checks"]["telemetry_recover_with_critical_full"]["available_bytes"] > 0,
            "Telemetry storage did not recover independently",
        )
        record["mechanism_checks_passed"] = True
    except TemporaryStorageReleased:
        pass
    except Exception as exc:
        record["error"] = str(exc)
    finally:
        try:
            for name in containers:
                result = docker("container", "inspect", name, check=False)
                if result.returncode == 0:
                    item = json.loads(result.stdout)[0]
                    require(
                        item["Config"]["Labels"].get("com.evdt.owner") == owner,
                        "Helper ownership changed",
                    )
                    docker("rm", "-f", name)
            for role in ("telemetry", "critical"):
                value = inspect(names[role]) if names[role] in eligible_volumes else None
                if value is not None:
                    owned_volume(value, names[role], owner)
                    loop_guard(role)
                    require(
                        value.get("Options")
                        == {
                            "type": "ext4",
                            "device": record["disks"][role]["device"],
                            "o": "nosuid,nodev,noexec",
                        },
                        "Filesystem volume options changed",
                    )
                    require(
                        not docker("ps", "-aq", "--filter", "volume=" + names[role]).stdout.strip(),
                        "Refusing removal of a referenced filesystem volume",
                    )
                    docker("volume", "rm", names[role])
            value = inspect(names["backing"]) if names["backing"] in eligible_volumes else None
            if value is not None:
                parent = backing_directory(value, docker_root, names["backing"], owner)
                require(
                    {path.name for path in parent.iterdir()} <= {"telemetry.ext4", "critical.ext4"},
                    "Refusing a backing volume containing unexpected files",
                )
                for role, size in sizes.items():
                    path = parent / (role + ".ext4")
                    if path.exists():
                        disk = record["disks"].get(role, {})
                        if disk.get("file_identity"):
                            checked_file(path, parent, size, disk["file_identity"])
                        else:
                            info = path.lstat()
                            require(
                                stat.S_ISREG(info.st_mode)
                                and info.st_nlink == 1
                                and 0 <= info.st_size <= size
                                and info.st_ino == disk.get("initial_inode")
                                and info.st_dev == disk.get("initial_device"),
                                "Refusing an unverified partial disk image",
                            )
                        for row in associated(path):
                            execute(["losetup", "--detach", row["name"]])
                        deadline = time.monotonic() + 5
                        while associated(path) and time.monotonic() < deadline:
                            time.sleep(0.05)
                        require(not associated(path), "Owned loop device did not detach")
                require(
                    not docker(
                        "ps", "-aq", "--filter", "volume=" + names["backing"]
                    ).stdout.strip(),
                    "Refusing removal of a referenced backing volume",
                )
                docker("volume", "rm", names["backing"])
            require(all(inspect(name) is None for name in eligible_volumes), "Owned volumes remain")
            record["cleanup"] = {
                "owned_containers": [],
                "owned_volumes": [],
                "owned_loop_associations": [],
            }
        except Exception as exc:
            record["cleanup_error"] = str(exc)
        if fingerprints() != before:
            record["error"] = "Source changed during filesystem qualification"
        record["passed"] = (
            record.get("mechanism_checks_passed") is True
            and "error" not in record
            and "cleanup_error" not in record
        )
        log.close()
        (output / "qualification.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "passed": record["passed"],
                    "evidence": output.relative_to(ROOT).as_posix(),
                    "error": record.get("error"),
                    "cleanup_error": record.get("cleanup_error"),
                }
            )
        )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
