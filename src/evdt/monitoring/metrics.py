"""Small internal authenticated observability endpoint; no asset command surface."""

import hmac
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def start(snapshot):
    file = os.environ.get("METRICS_TOKEN_FILE")
    if not file:
        return None
    credential = "Bearer " + Path(file).read_text().strip()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics" or not hmac.compare_digest(
                self.headers.get("Authorization", ""), credential
            ):
                self.send_error(401)
                return
            try:
                payload = {
                    "version": "1.0.0",
                    "run_id": os.environ["EVDT_RUN_ID"],
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    **snapshot(),
                }
                raw = json.dumps(payload, allow_nan=False).encode()
            except Exception:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 9100), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="internal-metrics").start()
    return server


def spool_status(spool, connected):
    disk = shutil.disk_usage(spool.path.parent)
    counts = spool.counts()
    provider = getattr(spool, "storage", None)
    storage = provider.status() if provider is not None else None
    return {
        "asset_id": spool.identity.asset_id,
        "source_id": spool.identity.source_id,
        "broker_connected": connected.is_set(),
        "queue": counts,
        "spool_file_bytes": spool.path.stat().st_size,
        "volume_free_bytes": disk.free,
        "volume_total_bytes": disk.total,
        "pending_byte_limit": spool.max_pending_bytes,
        "dropped_records": counts.get("dropped"),
        "overflow_indication": counts["evicted_lifetime"] > 0
        if "evicted_lifetime" in counts
        else None,
        "physical_quota_enforced": storage is not None
        and storage["physical_quota_enforced"] is True,
        "storage": storage,
    }


def queue_diagnostics(sender):
    """Unavailable/legacy diagnostics stay unknown; never infer zero lost originals."""
    result = {
        "dropped_records": None,
        "drop_status": "not instrumented",
        "physical_quota_enforced": False,
    }
    if sender.get("available") is not True:
        result["drop_status"] = "sender metrics unavailable"
        return result
    queue = sender.get("queue")
    fields = (
        "generated",
        "queued",
        "pending",
        "accepted",
        "rejected",
        "dropped",
        "evicted_unconfirmed",
        "evicted_lifetime",
    )
    if (
        isinstance(queue, dict)
        and all(type(queue.get(k)) is int and queue[k] >= 0 for k in fields)
        and queue["generated"]
        == sum(queue[k] for k in ("pending", "accepted", "rejected", "dropped"))
        and queue["pending"] == queue["queued"] + queue["evicted_unconfirmed"]
        and queue["evicted_lifetime"] >= queue["dropped"] + queue["evicted_unconfirmed"]
        and type(sender.get("dropped_records")) is int
        and sender["dropped_records"] == queue["dropped"]
    ):
        result["dropped_records"] = queue["dropped"]
        result["drop_status"] = (
            "confirmed loss count; "
            + str(queue["evicted_unconfirmed"])
            + " evicted originals still await reconciliation"
        )
    storage = sender.get("storage")
    result["physical_quota_enforced"] = (
        isinstance(storage, dict)
        and storage.get("profile") == "vehicle-queue-storage/1.0.0"
        and storage.get("physical_quota_enforced") is True
        and sender.get("physical_quota_enforced") is True
    )
    return result
