"""Contract and admission boundary for one operator command in a local simulation."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from evdt.contract_profile import PROFILE, Rejected, decode, validator

COMMAND = validator("commands/speed-limit-1.0.0.schema.json")
RESULT = validator("commands/execution-result-1.0.0.schema.json")
WIRE_PROFILE = {**PROFILE, "max_payload_bytes": 2048}


@dataclass(frozen=True)
class OperatorScope:
    """Trusted context supplied by the local process, outside the command message."""

    run_id: str
    asset_id: str
    issuer: str


def read_command(raw: bytes, scope: OperatorScope) -> dict:
    command = decode(raw, WIRE_PROFILE)
    if isinstance(command, dict):
        if command.get("mode") == "replay":
            raise Rejected("replay_mode")
        if "capability" in command and command["capability"] != "set_speed_limit":
            raise Rejected("unsupported_capability")
    if not COMMAND.is_valid(command):
        raise Rejected("command_schema")
    if command["run_id"] != scope.run_id:
        raise Rejected("wrong_run")
    if command["asset_id"] != scope.asset_id:
        raise Rejected("wrong_asset")
    if command["issuer"] != scope.issuer:
        raise Rejected("unauthorized_issuer")
    return command


def check_validity(command: dict, now: datetime, sim_time_s: float | None) -> None:
    issued = datetime.fromisoformat(command["issued_at"])
    expires = datetime.fromisoformat(command["expires_at"])
    if not (0 < (expires - issued).total_seconds() <= 30 and issued <= now < expires):
        raise Rejected("wall_validity")

    start, end = command["not_before_sim_s"], command["expires_sim_s"]
    if not 0 < end - start <= 5 or (sim_time_s is not None and not start <= sim_time_s < end):
        raise Rejected("simulation_validity")


def admit(raw: bytes, scope: OperatorScope, now: datetime, sim_time_s: float) -> dict:
    command = read_command(raw, scope)
    check_validity(command, now, sim_time_s)
    return command


class SpeedLimitExecutor:
    """Allow one actuation attempt in a single run. Repeated bytes return its result."""

    def __init__(self, scope: OperatorScope) -> None:
        self.scope = scope
        self._attempt: tuple[bytes, dict] | None = None

    def execute(
        self,
        raw: bytes,
        *,
        now: datetime,
        sim_time_s: float,
        current_limit_m_s: float,
        apply_limit: Callable[[str, float], None],
        verify_handoff: Callable[[], None] | None = None,
    ) -> dict:
        command = admit(raw, self.scope, now, sim_time_s)
        if self._attempt is not None:
            attempted, result = self._attempt
            if raw != attempted:
                raise Rejected("command_already_attempted")
            return result.copy()

        limit = command["parameters"]["value"]
        if not (math.isfinite(current_limit_m_s) and limit < current_limit_m_s):
            raise Rejected("not_a_speed_reduction")
        if verify_handoff is not None:
            verify_handoff()

        result = {
            "schema_version": "evdt-command-execution/1.0.0",
            "command_id": command["command_id"],
            "run_id": command["run_id"],
            "asset_id": command["asset_id"],
            "attempt_sim_s": sim_time_s,
            "status": "unknown_outcome",
        }
        self._attempt = (raw, result)
        try:
            apply_limit(self.scope.asset_id, limit)
        except Exception:
            # The change may have reached the simulator before the response failed.
            return result.copy()
        result["status"] = "acknowledged"
        return result.copy()
