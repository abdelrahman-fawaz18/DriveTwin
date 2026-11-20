"""Call the private automatic policy endpoint from the local run supervisor."""

import ipaddress
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AutomaticPolicy:
    def __init__(self, address, run_id, operator_token, policy_file):
        target = ipaddress.IPv4Address(address)
        if not target.is_private or target.is_loopback or target.is_link_local:
            raise ValueError("Automatic policy needs its owned private command service")
        self.base = f"http://{target}:8000"
        self.body = json.dumps({"run_id": run_id, "asset_id": "bus-001"}).encode()
        self.policy_token = policy_file.read_text().strip()
        self.operator_token = operator_token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.next_poll = 0.0
        self.last_state = None

    def send(self, path, token):
        request = Request(
            self.base + path,
            self.body,
            {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=1) as response:
            return json.loads(response.read(2048))["state"]

    def preempt(self):
        state = self.send("/v2/moving-charge/automatic/override", self.operator_token)
        if state != "override_latched":
            raise RuntimeError("Pre-start automatic operator override was not recorded")

    def poll(self):
        now = time.monotonic()
        if now < self.next_poll:
            return None
        self.next_poll = now + 0.75
        try:
            state = self.send("/internal/v2/moving-charge/automatic/evaluate", self.policy_token)
        except HTTPError as exc:
            with exc:
                if exc.code in (401, 403, 404):
                    raise RuntimeError("Automatic policy authority is unavailable") from None
            state = "review_unavailable"
        except (TimeoutError, URLError, ValueError, KeyError):
            state = "review_unavailable"
        if state == self.last_state:
            return None
        self.last_state = state
        return state
