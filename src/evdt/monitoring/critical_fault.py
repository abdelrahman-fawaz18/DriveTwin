"""Preallocated, identity-bound sticky fault status; not a command-result journal."""

import fcntl
import hashlib
import json
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evdt.monitoring.spool import Identity

PAGE = 4096
SAFE = 2**53 - 1


class FaultUnavailable(RuntimeError):
    """Status could not be established; callers must fail closed."""


class FaultIdentityMismatch(FaultUnavailable):
    """A valid frame belongs to a different identity or format."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate critical-status key")
        result[key] = value
    return result


@dataclass(frozen=True)
class FaultStatus:
    body: dict[str, Any]
    valid_copies: int

    @property
    def healthy(self) -> bool:
        return self.valid_copies == 2 and self.body["faulted"] is False


class FaultLatch:
    def __init__(self, folder: Path, identity: Identity, *, create: bool = False) -> None:
        self.folder, self.identity = folder, identity
        if create:
            folder.mkdir(mode=0o700, parents=False, exist_ok=False)
            initial = {
                "version": "critical-fault/1.0.0",
                "identity": identity.__dict__,
                "generation": 0,
                "faulted": False,
                "occurrences": 0,
                "first_reason": None,
                "first_at": None,
                "last_reason": None,
                "last_at": None,
            }
            for name, size, content in (
                ("fault.lock", 0, b""),
                ("fault.0", PAGE, self.encode(initial)),
                ("fault.1", PAGE, self.encode(initial)),
            ):
                descriptor = os.open(
                    folder / name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600
                )
                try:
                    if size:
                        os.posix_fallocate(descriptor, 0, size)
                        if os.pwrite(descriptor, content, 0) != size:
                            raise FaultUnavailable("Short initial critical-status write")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        if not folder.is_dir() or folder.is_symlink():
            raise FaultUnavailable("Critical-status directory is unavailable")
        self.read()

    @staticmethod
    def encode(body: dict[str, Any]) -> bytes:
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        frame = json.dumps(
            {"body": body, "sha256": hashlib.sha256(raw).hexdigest()},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if len(frame) > PAGE:
            raise FaultUnavailable("Critical status exceeds its preallocated slot")
        return frame.ljust(PAGE, b" ")

    def descriptor(self, name: str, size: int) -> int:
        try:
            fd = os.open(self.folder / name, os.O_RDWR | os.O_NOFOLLOW)
        except OSError as exc:
            raise FaultUnavailable("Preallocated critical-status file is unavailable") from exc
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size:
            os.close(fd)
            raise FaultUnavailable("Preallocated critical-status file changed")
        return fd

    @contextmanager
    def locked(self, exclusive: bool) -> Iterator[None]:
        descriptor = self.descriptor("fault.lock", 0)
        deadline = time.monotonic() + 2
        try:
            while True:
                try:
                    fcntl.flock(
                        descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
                    )
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise FaultUnavailable("Critical-status lock timed out") from None
                    time.sleep(0.01)
            yield
        finally:
            os.close(descriptor)

    def copies(self) -> list[tuple[int, dict[str, Any]]]:
        values = []
        for index in (0, 1):
            try:
                descriptor = self.descriptor("fault." + str(index), PAGE)
                try:
                    raw = os.pread(descriptor, PAGE, 0)
                finally:
                    os.close(descriptor)
                frame = json.loads(raw, object_pairs_hook=unique_object)
                if not isinstance(frame, dict) or set(frame) != {"body", "sha256"}:
                    continue
                body = frame["body"]
                if not isinstance(body, dict) or set(body) != {
                    "version",
                    "identity",
                    "generation",
                    "faulted",
                    "occurrences",
                    "first_reason",
                    "first_at",
                    "last_reason",
                    "last_at",
                }:
                    continue
                canonical = json.dumps(
                    body, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                if len(raw) != PAGE or frame["sha256"] != hashlib.sha256(canonical).hexdigest():
                    continue
                if (
                    body.get("version") != "critical-fault/1.0.0"
                    or body.get("identity") != self.identity.__dict__
                ):
                    raise FaultIdentityMismatch(
                        "Critical status belongs to another identity or format"
                    )
                if (
                    type(body.get("generation")) is not int
                    or not 0 <= body["generation"] <= SAFE
                    or type(body.get("occurrences")) is not int
                    or not 0 <= body["occurrences"] <= SAFE
                    or type(body.get("faulted")) is not bool
                    or body["faulted"] != (body["occurrences"] > 0)
                    or body["generation"] != body["occurrences"]
                ):
                    continue
                if body["faulted"] and not all(
                    isinstance(body.get(k), str) and body[k]
                    for k in ("first_reason", "first_at", "last_reason", "last_at")
                ):
                    continue
                if not body["faulted"] and any(
                    body[k] is not None
                    for k in ("first_reason", "first_at", "last_reason", "last_at")
                ):
                    continue
                values.append((index, body))
            except FaultIdentityMismatch:
                raise
            except FaultUnavailable:
                # A missing/torn copy degrades status; it is never silently recreated.
                continue
            except (ValueError, KeyError, TypeError, UnicodeError):
                continue
        if not values:
            raise FaultUnavailable("Neither critical-status copy is valid")
        if (
            len(values) == 2
            and values[0][1]["generation"] == values[1][1]["generation"]
            and values[0][1] != values[1][1]
        ):
            raise FaultUnavailable("Critical-status copies conflict at the same generation")
        return values

    def read(self) -> FaultStatus:
        with self.locked(False):
            copies = self.copies()
            _, body = max(copies, key=lambda item: (item[1]["generation"], item[0]))
            return FaultStatus(dict(body), len(copies))

    def trip(self, reason: str) -> FaultStatus:
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 128
            or any(ord(ch) < 32 for ch in reason)
        ):
            raise ValueError("A bounded explicit critical fault reason is required")
        reason.encode("utf-8", errors="strict")
        with self.locked(True):
            copies = self.copies()
            index, previous = max(copies, key=lambda item: (item[1]["generation"], item[0]))
            if previous["generation"] >= SAFE:
                raise FaultUnavailable("Critical-status generation exhausted")
            stamp = datetime.now(timezone.utc).isoformat()
            body = {
                **previous,
                "generation": previous["generation"] + 1,
                "occurrences": previous["occurrences"] + 1,
                "faulted": True,
                "first_reason": previous["first_reason"] or reason,
                "first_at": previous["first_at"] or stamp,
                "last_reason": reason,
                "last_at": stamp,
            }
            raw = self.encode(body)
            descriptor = self.descriptor("fault." + str(1 - index), PAGE)
            try:
                if os.pwrite(descriptor, raw, 0) != PAGE:
                    raise FaultUnavailable(
                        "Incomplete critical-status update; outcome must be checked"
                    )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return self.read()
