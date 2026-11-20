"""Opt-in bounded local correlation records; never record payloads or secrets."""

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

LIMIT = 128 * 1024 * 1024
_lock = threading.Lock()
_sequence = 0
_process = str(uuid.uuid4())


def stamp():
    return {"utc_s": time.time(), "monotonic_s": time.monotonic()}


def emit(stage, obj=None, *, at=None, **metadata):
    destination = os.environ.get("EVDT_TRACE_FILE")
    if not destination:
        return
    global _sequence
    identity = {}
    if obj is not None:
        for key in ("run_id", "asset_id", "source_id", "event_id"):
            value = obj.get(key)
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError("Trace requires a bounded correlation identity")
            identity[key] = value
    with _lock:
        path = Path(destination)
        if _sequence == 0:
            # Every process uses its own file; refuse accidental reuse or symlinks.
            with path.open("xb") as file:
                header = {
                    "version": "event-trace/1.1.0",
                    "monotonic_resolution_s": time.get_clock_info("monotonic").resolution,
                    "stage": "process_start",
                    "process": _process,
                    "sequence": 0,
                    "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                    **stamp(),
                }
                file.write((json.dumps(header, allow_nan=False) + "\n").encode())
        _sequence += 1
        record = {
            "stage": stage,
            "process": _process,
            "sequence": _sequence,
            **(at or stamp()),
            **identity,
            **metadata,
        }
        raw = (json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(raw) > 4096 or path.stat().st_size + len(raw) > LIMIT:
            raise RuntimeError("Enabled trace exceeded its bounded storage budget")
        with path.open("ab") as file:
            file.write(raw)


def packet(raw):
    """Extract only validated-shape identity metadata from an internal packet."""
    obj = json.loads(raw)
    return {key: obj[key] for key in ("run_id", "asset_id", "source_id", "event_id")}


def wire(raw):
    return hashlib.sha256(raw).hexdigest()
