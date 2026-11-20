"""Owned storage-lifecycle fixture; no source timing or broker delivery claim."""

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evdt.monitoring.metrics import queue_diagnostics, spool_status, start
from evdt.monitoring.transport import open_spool
from evdt.monitoring.vehicle_storage import MOUNTS, BoundedSpool, regular_bytes, require


def metadata(queue):
    with queue.connection() as conn:
        return dict(conn.execute("SELECT * FROM metadata WHERE id=1").fetchone())


def sample(queue, when):
    return queue.enqueue(
        [
            {
                "signal_id": "location.latitude",
                "value": 45.51,
                "unit": "deg",
                "quality": "good",
                "provenance": "simulated-observation",
            }
        ],
        when,
        manifest_sha256="0" * 64,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enqueue", "verify", "full-fault"))
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--witness", type=Path)
    args = parser.parse_args()
    raw = regular_bytes(args.binding)
    binding = json.loads(raw)
    os.environ.update(
        EVDT_RUN_ID=binding["identity"]["run_id"],
        ASSET_ID=binding["identity"]["asset_id"],
        SOURCE_ID=binding["identity"]["source_id"],
        CATALOG_VERSION=binding["queue"]["catalog_version"],
        SPOOL_FILE=str(MOUNTS["telemetry"] / "queue.db"),
        EVDT_STORAGE_BINDING_FILE=str(args.binding),
    )
    queue = open_spool()
    require(
        isinstance(queue, BoundedSpool),
        "Normal factory did not select the explicit bounded provider",
    )
    storage = queue.storage
    result = {
        "passed": False,
        "action": args.action,
        "scope": "Ordinary queue on held owned filesystems; no broker or wall-time workload",
    }
    if args.action == "enqueue":
        require(queue.counts()["generated"] == 0, "Fixture queue is already used")
        queue.start_session()
        event = sample(queue, 0)
        result["witness"] = {
            "raw_base64": base64.b64encode(event.raw).decode(),
            "event_id": event.event_id,
            "wire_sha256": event.wire_sha256,
            "metadata": metadata(queue),
            "counts": queue.counts(),
            "fault_expected": False,
        }
    else:
        witness = json.loads(regular_bytes(args.witness))
        if args.action == "full-fault":
            try:
                sample(queue, 1)
            except sqlite3.Error as exc:
                require(exc.sqlite_errorcode == sqlite3.SQLITE_FULL, "Expected actual SQLITE_FULL")
                result["actual_sqlite_error"] = exc.sqlite_errorcode
            else:
                raise RuntimeError("The physically full queue accepted another original")
        events = queue.pending()
        require(
            len(events) == 1
            and events[0].raw == base64.b64decode(witness["raw_base64"], validate=True)
            and events[0].event_id == witness["event_id"]
            and events[0].wire_sha256
            == witness["wire_sha256"]
            == hashlib.sha256(events[0].raw).hexdigest(),
            "Original witness differs after reopening",
        )
        require(
            metadata(queue) == witness["metadata"] and queue.counts() == witness["counts"],
            "Failed transaction or reopening changed durable sequence/counters",
        )
        fault = storage.status()["critical_fault"]
        fault_expected = args.action == "full-fault" or witness["fault_expected"]
        require(
            fault["valid_copies"] == 2
            and fault["faulted"] is fault_expected
            and fault["occurrences"] == int(fault_expected)
            and fault["first_reason"] == ("telemetry_sqlite_full" if fault_expected else None),
            "Sticky full-disk fault or redundancy differs",
        )
        result.update(
            complete_original_metadata_counts_unchanged=True,
            fault_expected=fault_expected,
            valid_fault_copies=fault["valid_copies"],
        )
    server = start(lambda: spool_status(queue, threading.Event()))
    require(server is not None, "Actual authenticated metrics endpoint was not started")
    try:
        try:
            urlopen("http://127.0.0.1:9100/metrics", timeout=2)
        except HTTPError as exc:
            require(exc.code == 401, "Anonymous metrics access did not receive 401")
        else:
            raise RuntimeError("Anonymous metrics access succeeded")
        token = Path(os.environ["METRICS_TOKEN_FILE"]).read_text().strip()
        with urlopen(
            Request("http://127.0.0.1:9100/metrics", headers={"Authorization": "Bearer " + token}),
            timeout=2,
        ) as response:
            measured = json.load(response)
            require(
                response.headers["Cache-Control"] == "no-store"
                and measured["run_id"] == binding["identity"]["run_id"],
                "Current metrics identity/cache policy differs",
            )
        diagnostic = queue_diagnostics({"available": True, **measured})
        require(
            measured["queue"] == queue.counts()
            and diagnostic["dropped_records"] == 0
            and diagnostic["physical_quota_enforced"] is True
            and measured["overflow_indication"] is False,
            "Authenticated current queue diagnostics differ",
        )
        result.update(
            authenticated_metrics_passed=True,
            anonymous_metrics_refused=True,
            normal_factory_selection_passed=True,
            operational_metrics=measured,
        )
    finally:
        server.shutdown()
        server.server_close()
    result.update(passed=True, counts=queue.counts(), storage=storage.status())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
