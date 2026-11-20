"""Explicit Linux bounded-volume binding; never silently initialize on normal open."""

import argparse
import errno
import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from evdt.monitoring.contracts import monitoring_contract, read_receipt
from evdt.monitoring.critical_fault import FaultLatch, unique_object
from evdt.monitoring.formats import supported_pair
from evdt.monitoring.spool import Identity, Spool, SpoolFull
from evdt.monitoring.spool_capacity import DropOldestPolicy

VERSION = "vehicle-queue-storage/1.0.0"
LIMIT = 32 * 1024**2
BUDGETS = {"telemetry": 64 * 1024**2, "critical": 8 * 1024**2}
MOUNTS = {"telemetry": Path("/spool"), "critical": Path("/critical")}


class StorageUnavailable(RuntimeError):
    """Required binding or physical storage could not be verified."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise StorageUnavailable(message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def regular_bytes(path: Path, maximum: int = 16384) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum,
            "Storage record is linked, not regular or too large",
        )
        raw = os.read(descriptor, maximum + 1)
        require(len(raw) == info.st_size, "Storage record changed or was incompletely read")
        return raw
    finally:
        os.close(descriptor)


def checked_binding(raw: bytes, identity: Identity) -> dict[str, Any]:
    require(len(raw) <= 16384, "Storage binding exceeds its bound")
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(
        isinstance(value, dict)
        and set(value) == {"version", "mode", "owner", "boot_id", "identity", "queue", "roles"},
        "Storage binding fields differ",
    )
    require(
        value["version"] == VERSION and value["identity"] == identity.__dict__,
        "Storage binding version or identity differs",
    )
    require(
        isinstance(value["owner"], str) and re.fullmatch(r"evdt-disk-[0-9a-f]{32}", value["owner"]),
        "Storage owner differs",
    )
    for field in (value["boot_id"], identity.run_id):
        require(
            isinstance(field, str) and str(uuid.UUID(field)) == field,
            "Noncanonical storage identity",
        )
    require(
        all(
            isinstance(s, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", s)
            for s in (identity.asset_id, identity.source_id)
        ),
        "Invalid asset/source identity",
    )
    queue = value["queue"]
    require(
        isinstance(queue, dict)
        and set(queue)
        == {"schema_version", "catalog_version", "encoded_limit_bytes", "loss_record_limit"},
        "Storage queue settings differ",
    )
    require(
        isinstance(queue["schema_version"], str)
        and isinstance(queue["catalog_version"], str)
        and supported_pair(queue["schema_version"], queue["catalog_version"]),
        "Unsupported storage wire/catalog pair",
    )
    encoded = queue["encoded_limit_bytes"]
    require(
        type(encoded) is int
        and 0 < encoded <= LIMIT
        and queue["loss_record_limit"] == 16384
        and type(queue["loss_record_limit"]) is int,
        "Storage capacity differs",
    )
    require(
        value["mode"] == ("nominal" if encoded == LIMIT else "reduced-test"),
        "Reduced capacity must be explicitly labeled as a test",
    )
    require(
        isinstance(value["roles"], dict) and set(value["roles"]) == set(BUDGETS),
        "Storage roles differ",
    )
    for role, budget in BUDGETS.items():
        item = value["roles"][role]
        require(
            isinstance(item, dict) and set(item) == {"device", "filesystem_bytes", "image_bytes"},
            "Storage role fields differ",
        )
        require(
            all(type(item[k]) is int and item[k] > 0 for k in item)
            and item["image_bytes"] == budget
            and item["filesystem_bytes"] < budget,
            "Storage role budgets differ",
        )
    require(
        value["roles"]["telemetry"]["device"] != value["roles"]["critical"]["device"],
        "Critical storage must be a separate filesystem",
    )
    return cast(dict[str, Any], value)


def mount_record(folder: Path) -> int:
    matches = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        separator = fields.index("-")
        if fields[4] == str(folder):
            matches.append((fields, separator))
    require(len(matches) == 1, "Expected one exact storage mount; refusing fallback or overmount")
    fields, separator = matches[0]
    require(
        fields[3] == "/"
        and fields[separator + 1] == "ext4"
        and {"rw", "nosuid", "nodev", "noexec"} <= set(fields[5].split(",")),
        "Storage mount type/root/options differ",
    )
    major, minor = (int(n) for n in fields[2].split(":"))
    return os.makedev(major, minor)


def database_schema(path: Path) -> bytes:
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        require(
            conn.execute("PRAGMA quick_check").fetchall() == [("ok",)],
            "Initialized queue integrity check failed",
        )
        rows = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        return hashlib.sha256(canonical(rows)).hexdigest().encode()
    finally:
        conn.close()


class BoundedStorage:
    def __init__(self, raw: bytes, identity: Identity) -> None:
        self.binding = checked_binding(raw, identity)
        self.identity = identity
        self.raw = canonical(self.binding)
        self.marker = MOUNTS["critical"] / "provider-binding.json"
        self.schema_marker = MOUNTS["critical"] / "queue-schema.sha256"
        self.fault_folder = MOUNTS["critical"] / "fault-state"

    @property
    def fault(self) -> FaultLatch:
        return FaultLatch(self.fault_folder, self.identity)

    def check_mounts(self) -> None:
        require(
            Path("/proc/sys/kernel/random/boot_id").read_text().strip() == self.binding["boot_id"],
            "Storage binding belongs to another Ubuntu boot; explicit attachment is required",
        )
        for role, folder in MOUNTS.items():
            require(
                folder.is_dir() and not folder.is_symlink(), "Storage mount is missing or linked"
            )
            expected = self.binding["roles"][role]
            usage = os.statvfs(folder)
            require(
                os.stat(folder).st_dev == mount_record(folder) == expected["device"]
                and usage.f_blocks * usage.f_frsize == expected["filesystem_bytes"],
                "Storage device or filesystem capacity differs",
            )

    def check_ready(self) -> None:
        self.check_mounts()
        require(regular_bytes(self.marker) == self.raw, "Durable storage binding differs")
        require(
            self.fault.read().valid_copies == 2,
            "Critical fault status is degraded; explicit recovery is required",
        )
        path = MOUNTS["telemetry"] / "queue.db"
        info = path.lstat()
        require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            "Initialized queue is missing, linked or not regular",
        )

    def queue_arguments(self) -> dict[str, Any]:
        spec = self.binding["queue"]
        return {
            "max_pending_bytes": spec["encoded_limit_bytes"],
            "schema_version": spec["schema_version"],
            "catalog_version": spec["catalog_version"],
            "capacity_policy": DropOldestPolicy(
                spec["encoded_limit_bytes"], spec["loss_record_limit"]
            ),
        }

    def initialize(self) -> None:
        self.check_mounts()
        path = MOUNTS["telemetry"] / "queue.db"
        require(
            not any(
                p.exists() or p.is_symlink()
                for p in (path, self.marker, self.schema_marker, self.fault_folder)
            ),
            "Refusing to initialize existing or partial vehicle storage",
        )
        FaultLatch(self.fault_folder, self.identity, create=True)
        Spool(
            path,
            self.identity,
            monitoring_contract().validate,
            read_receipt,
            **self.queue_arguments(),
        )
        for destination, raw in (
            (self.schema_marker, database_schema(path)),
            (self.marker, self.raw),
        ):
            descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                require(os.write(descriptor, raw) == len(raw), "Short storage binding write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        descriptor = os.open(self.marker.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.check_ready()

    def open(self, path: Path, catalog_version: str) -> "BoundedSpool":
        require(
            path == MOUNTS["telemetry"] / "queue.db"
            and catalog_version == self.binding["queue"]["catalog_version"],
            "Queue path or selected catalog differs from the explicit storage binding",
        )
        self.check_ready()
        require(
            database_schema(path) == regular_bytes(self.schema_marker, 64),
            "Initialized queue schema differs; refusing automatic repair",
        )
        return BoundedSpool(self, monitoring_contract().validate)

    def status(self) -> dict[str, Any]:
        self.check_ready()
        fault = self.fault.read()
        return {
            "profile": VERSION,
            "mode": self.binding["mode"],
            "binding_sha256": hashlib.sha256(self.raw).hexdigest(),
            "physical_quota_enforced": True,
            "critical_fault": {
                **fault.body,
                "valid_copies": fault.valid_copies,
                "healthy": fault.healthy,
            },
            "filesystems": {
                role: {
                    "image_bytes": BUDGETS[role],
                    "filesystem_bytes": self.binding["roles"][role]["filesystem_bytes"],
                    "available_bytes": os.statvfs(folder).f_bavail * os.statvfs(folder).f_frsize,
                }
                for role, folder in MOUNTS.items()
            },
        }


class BoundedSpool(Spool):
    def __init__(
        self,
        storage: BoundedStorage,
        validate: Callable[[bytes, str, str], tuple[dict[str, Any], str]],
    ) -> None:
        self.storage = storage
        super().__init__(
            MOUNTS["telemetry"] / "queue.db",
            storage.identity,
            validate,
            read_receipt,
            **storage.queue_arguments(),
        )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.storage.check_ready()
        try:
            with super().connection() as conn:
                yield conn
        except (sqlite3.Error, SpoolFull, OSError) as exc:
            reason = None
            code = getattr(exc, "sqlite_errorcode", None)
            if isinstance(exc, SpoolFull):
                reason = "telemetry_capacity_refused"
            elif code is not None and code & 255 in (
                sqlite3.SQLITE_FULL,
                sqlite3.SQLITE_IOERR,
                sqlite3.SQLITE_CORRUPT,
                sqlite3.SQLITE_NOTADB,
            ):
                reason = (
                    "telemetry_sqlite_full"
                    if code & 255 == sqlite3.SQLITE_FULL
                    else "telemetry_sqlite_fault"
                )
            elif isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT, errno.EIO):
                reason = "telemetry_io_fault"
            if reason:
                try:
                    self.storage.fault.trip(reason)
                except Exception:
                    exc.add_note("Independent critical fault status is also unavailable")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize", "verify"))
    parser.add_argument("--binding", type=Path, required=True)
    args = parser.parse_args()
    raw = regular_bytes(args.binding)
    identity = Identity(**json.loads(raw, object_pairs_hook=unique_object)["identity"])
    storage = BoundedStorage(raw, identity)
    if args.action == "initialize":
        storage.initialize()
    spool = storage.open(
        MOUNTS["telemetry"] / "queue.db", storage.binding["queue"]["catalog_version"]
    )
    print(
        json.dumps(
            {
                "passed": True,
                "role": "telemetry",
                "device": os.stat(MOUNTS["telemetry"]).st_dev,
                "filesystem_bytes": storage.binding["roles"]["telemetry"]["filesystem_bytes"],
                "counts": spool.counts(),
                "storage": storage.status(),
            }
        )
    )


if __name__ == "__main__":
    main()
