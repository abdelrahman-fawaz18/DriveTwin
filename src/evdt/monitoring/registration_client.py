"""Bounded exact-byte retries for producer registration and clock metadata."""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evdt.monitoring.run_policy import encoded


class RegistrationClient:
    def __init__(self, identity, credential: Path, base_url="http://registry:9000"):
        self.identity = identity
        self.token = credential.read_text().strip()
        self.base_url = base_url
        self.revision = 0
        self.manifest_hash = None

    def post(self, path, raw):
        for attempt in range(3):
            try:
                request = Request(
                    self.base_url + path,
                    raw,
                    {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
                )
                with urlopen(request, timeout=2) as response:
                    return json.load(response)
            except HTTPError as exc:
                exc.close()
                if exc.code != 429 and not 500 <= exc.code < 600:
                    raise RuntimeError("Metadata admission refused") from exc
                if attempt == 2:
                    raise RuntimeError("Metadata service unavailable; source must stop") from exc
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise RuntimeError("Metadata service unavailable; source must stop") from None
            time.sleep(0.2 * (attempt + 1))

    def register(self, raw):
        self.manifest_hash = self.post("/v1/registrations", raw)["manifest_sha256"]

    def clock(self, state, sim_time_s):
        if self.manifest_hash is None:
            raise RuntimeError("Register the source before declaring time")
        value = {
            **self.identity,
            "schema_version": "source-clock/1.0.0",
            "manifest_sha256": self.manifest_hash,
            "event_id": str(uuid.uuid4()),
            "revision": self.revision + 1,
            "state": state,
            "sim_time_s": sim_time_s,
            "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.post("/v1/clock-events", encoded(value))
        self.revision += 1
