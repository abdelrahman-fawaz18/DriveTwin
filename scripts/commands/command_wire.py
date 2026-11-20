"""Scoped local transport, with process interruption points for the recovery fixture."""

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from urllib.request import Request, urlopen

FAULTS = ("before_actuation", "after_actuation", "before_result_ack")
ACTUATOR_FAULT_RESULTS = {"stuck": "acknowledged", "failed": "unknown_outcome"}
TIMING_CASES = ("pause_expiry", "accelerated_expiry", "delivery_expiry")
TIMING_TTL_S = 12
LIMIT = 2048
CLOCK_HEADER = "X-EVDT-Command-Clock"
FENCE_HEADER = "X-EVDT-Command-Fence"


def post(endpoint: str, credential: str, data: bytes) -> bytes:
    token = Path(f"/run/secrets/command_{credential}").read_text().strip()
    request = Request(
        "http://command-api:8000/internal/v1/" + endpoint,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=4) as response:
        body = response.read(LIMIT + 1)
        if len(body) > LIMIT:
            raise RuntimeError("Command service response exceeded its byte limit")
        if endpoint == "claim" and body:
            # Preserve the original UTF-8 bytes and keep the authenticated clock header with them.
            header = response.headers.get(CLOCK_HEADER)
            if not header or len(header) > 256:
                raise RuntimeError("Command service did not provide a bounded clock lease")
            return json.dumps(
                [body.decode(), header, response.headers.get(FENCE_HEADER)], ensure_ascii=False
            ).encode()
        return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint", choices=("claim", "result"))
    parser.add_argument("--fault", choices=FAULTS)
    args = parser.parse_args()
    data = sys.stdin.buffer.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("Transport input too large")
    if args.fault == "after_actuation" and args.endpoint == "result":
        os.kill(os.getpid(), signal.SIGKILL)
    response = post(args.endpoint, "executor", data)
    if args.fault == "before_actuation" and args.endpoint == "claim":
        if not response:
            raise RuntimeError("Fault test expected a committed reservation")
        os.kill(os.getpid(), signal.SIGKILL)
    if args.fault == "before_result_ack" and args.endpoint == "result":
        os.kill(os.getpid(), signal.SIGKILL)
    sys.stdout.buffer.write(response)


if __name__ == "__main__":
    main()
