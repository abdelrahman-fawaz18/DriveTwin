"""Standard-library-only checks inside uniquely owned mounted test filesystems."""

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path

ROLES = ("telemetry", "critical")


def marker(role):
    return (("EV-DT isolated " + role + " marker\n").encode() * 200)[:4096]


def sync_directory(folder):
    descriptor = os.open(folder, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_marker(folder, role, *, create=False):
    path = folder / "marker.bin"
    expected = marker(role)
    if path.is_symlink():
        raise RuntimeError("Persistent marker is linked")
    if not path.exists():
        if not create:
            raise RuntimeError("Persistent marker is missing; refusing to recreate it")
        with path.open("xb", buffering=0) as stream:
            if stream.write(expected) != len(expected):
                raise RuntimeError("Short marker write")
            os.fsync(stream.fileno())
        sync_directory(folder)
    if path.is_symlink() or path.read_bytes() != expected:
        raise RuntimeError("Persistent marker differs")
    return hashlib.sha256(expected).hexdigest()


def fill(folder):
    errors = []
    path = folder / "filler.bin"
    with path.open("xb", buffering=0) as stream:
        size = 65536
        while size:
            try:
                count = stream.write(b"f" * size)
                if count is None or count <= 0:
                    raise RuntimeError("No-progress disk write")
            except OSError as exc:
                if exc.errno != errno.ENOSPC:
                    raise
                errors.append({"errno": exc.errno, "requested_bytes": size})
                size //= 2
        try:
            os.fsync(stream.fileno())
        except OSError as exc:
            if exc.errno != errno.ENOSPC:
                raise
            errors.append({"errno": exc.errno, "operation": "fsync"})
    if not errors or errors[-1].get("requested_bytes") != 1:
        # A deferred fsync error can follow the one-byte failure.
        if not any(row.get("requested_bytes") == 1 for row in errors):
            raise RuntimeError("No one-byte filesystem-full refusal recorded")
    return {"filler_bytes": path.stat().st_size, "no_space_errors": errors}


def assert_full(folder):
    with (folder / "filler.bin").open("ab", buffering=0) as stream:
        try:
            stream.write(b"x")
            os.fsync(stream.fileno())
        except OSError as exc:
            if exc.errno == errno.ENOSPC:
                return {"one_byte_refused_errno": exc.errno}
            raise
    raise RuntimeError("A supposedly full filesystem accepted another byte")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "initialize",
            "marker",
            "fill",
            "check",
            "clear",
            "full",
            "fault-init",
            "fault-trip",
            "fault-check",
        ),
    )
    parser.add_argument("--role", choices=ROLES, default="telemetry")
    parser.add_argument("--fault-identity")
    parser.add_argument("--fault-reason")
    args = parser.parse_args()
    if args.action == "initialize":
        if os.geteuid() != 0:
            raise RuntimeError("Only the ownership initializer uses container root")
        for role in ROLES:
            folder = Path("/" + role)
            os.chmod(folder, 0o700)
            os.chown(folder, 10001, 10001)
        print(json.dumps({"passed": True, "ordinary_uid": 10001}))
        return
    if os.geteuid() != 10001:
        raise RuntimeError("Storage checks must use the ordinary application UID")
    folder = Path("/" + args.role)
    result = {"passed": False, "action": args.action, "role": args.role, "uid": os.geteuid()}
    if args.action.startswith("fault-"):
        from evdt.monitoring.critical_fault import FaultLatch
        from evdt.monitoring.spool import Identity

        if args.role != "critical":
            raise RuntimeError("Fault status must use the isolated critical area")
        identity = Identity(**json.loads(args.fault_identity))
        latch = FaultLatch(folder / "fault-state", identity, create=args.action == "fault-init")
        state = latch.trip(args.fault_reason) if args.action == "fault-trip" else latch.read()
        result["fault_status"] = state.body
        result["valid_copies"] = state.valid_copies
        result["status_healthy"] = state.healthy
        result["allocated_slot_bytes"] = sum(
            (latch.folder / ("fault." + str(i))).stat().st_blocks * 512 for i in (0, 1)
        )
    if args.action == "marker":
        ensure_marker(folder, args.role, create=True)
    else:
        ensure_marker(folder, args.role)
    if args.action == "fill":
        result.update(fill(folder))
    elif args.action == "full":
        result.update(assert_full(folder))
    elif args.action == "clear":
        filler = folder / "filler.bin"
        if filler.is_symlink() or not filler.is_file():
            raise RuntimeError("Refusing a changed owned filler")
        filler.unlink()
        with (folder / "recovery.bin").open("xb", buffering=0) as stream:
            if stream.write(b"r" * 4096) != 4096:
                raise RuntimeError("Short recovery write")
            os.fsync(stream.fileno())
        sync_directory(folder)
        result["recovery_bytes"] = 4096
    result["marker_sha256"] = ensure_marker(folder, args.role)
    usage = os.statvfs(folder)
    result.update(
        filesystem_bytes=usage.f_blocks * usage.f_frsize,
        available_bytes=usage.f_bavail * usage.f_frsize,
        device=os.stat(folder).st_dev,
        passed=True,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
