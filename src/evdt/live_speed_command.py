"""Opt-in speed commands at the live SUMO step boundary."""

import json
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evdt.command_authority import FENCE_HEADER, FENCE_PROTOCOL, PROPOSERS
from evdt.command_timing import CLOCK_HEADER, ClockLease, host_clock
from evdt.commands import WIRE_PROFILE, OperatorScope, SpeedLimitExecutor, read_command
from evdt.contract_profile import Rejected

OPERATOR_PROFILE = "operator-only/1.0.0"
ARBITRATION_PROFILE = "arbitration/1.0.0"


class LiveSpeedCommand:
    def __init__(
        self, run_id: str, asset_id: str, credential: Path, profile: str = OPERATOR_PROFILE
    ) -> None:
        if profile not in (OPERATOR_PROFILE, ARBITRATION_PROFILE):
            raise ValueError("Unsupported live speed command profile")
        self.profile = profile
        self.fenced = profile == ARBITRATION_PROFILE
        issuer = "dispatch-coordinator" if self.fenced else PROPOSERS["operator"]
        self.executor = SpeedLimitExecutor(OperatorScope(run_id, asset_id, issuer))
        self.token = credential.read_text().strip()
        if len(self.token) < 32:
            raise ValueError("Live speed commands require a scoped executor credential")

    def post(self, endpoint, value):
        request = Request(
            "http://command-api:8000/internal/v1/" + endpoint,
            json.dumps(value, allow_nan=False).encode(),
            {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=2) as response:
                raw = response.read(WIRE_PROFILE["max_payload_bytes"] + 1)
                if len(raw) > WIRE_PROFILE["max_payload_bytes"]:
                    raise ValueError("Oversized command response")
                return response.status, raw, response.headers
        except HTTPError as error:
            error.close()
            raise

    def step(self, sim_time_s, vehicle):
        target = self.executor.scope
        claim = {"sim_time_s": sim_time_s, "state": "running"}
        if self.fenced:
            claim["fence_protocol"] = FENCE_PROTOCOL
        try:
            status, raw, headers = self.post("claim", claim)
            if status == 204:
                return
            if status != 200 or (not self.fenced and headers.get(FENCE_HEADER) is not None):
                raise ValueError("Unsupported live command handoff")
            command = read_command(raw, target)
        except (OSError, ValueError, HTTPException):
            print("Live speed command handoff unavailable", flush=True)
            return

        def verify_handoff():
            header = headers.get(CLOCK_HEADER)
            if not header or len(header) > 256:
                raise Rejected("invalid_clock_lease")
            lease = ClockLease.decode(header)
            if self.fenced:
                self.verify_fence(command, headers.get(FENCE_HEADER))
            lease.verify(host_clock())

        try:
            try:
                result = self.executor.execute(
                    raw,
                    now=datetime.now(timezone.utc),
                    sim_time_s=sim_time_s,
                    current_limit_m_s=vehicle.getMaxSpeed(target.asset_id),
                    apply_limit=vehicle.setMaxSpeed,
                    verify_handoff=verify_handoff,
                )
            except Rejected as error:
                self.post("refusal", {"command_id": command["command_id"], "reason": str(error)})
            else:
                self.post("result", result)
        except (OSError, ValueError, HTTPException):
            # Preserve telemetry and leave the reserved command for reconciliation.
            print("Live speed command outcome could not be published", flush=True)

    def verify_fence(self, command, generation):
        if (
            not generation
            or len(generation) > 19
            or not generation.isascii()
            or not generation.isdecimal()
            or not 0 < int(generation) <= 2**63 - 1
        ):
            raise Rejected("authority_fence_refused")
        proof = {key: command[key] for key in ("command_id", "run_id", "asset_id")}
        proof["generation"] = int(generation)
        try:
            status, body, _ = self.post("verify-fence", proof)
            if status != 204 or body:
                raise ValueError("Fence verification did not return an empty success")
        except (OSError, ValueError, HTTPException) as error:
            raise Rejected("authority_fence_refused") from error
