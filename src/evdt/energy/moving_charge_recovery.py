"""Immutable held-bus observation used to authorize one simulator replacement."""

from dataclasses import dataclass, field
from uuid import UUID

from evdt.energy.route_aware.context import SourceStop
from evdt.energy.route_aware.contracts import SourceScope
from evdt.energy.route_aware.validation import digest, integer, number, sequence, text

RECOVERY_HOLD_REAL_LIFE_S = 75


@dataclass(frozen=True)
class HeldDepartureInputs:
    """Measured equipment draw and the verified passenger-stop suffix."""

    auxiliary_power_w: float
    battery_temperature_k: float
    remaining_stops: tuple[SourceStop, ...]

    def __post_init__(self):
        number(self.auxiliary_power_w, "held auxiliary power", 0, 1e6)
        number(self.battery_temperature_k, "held battery temperature", 200, 400)
        sequence(self.remaining_stops, "held passenger stops", SourceStop, 0, 1_000)


@dataclass(frozen=True)
class HeldChargeStop:
    scope: SourceScope
    interrupted_command_id: str
    manifest_sha256: str
    assignment_sha256: str
    route_revision: str
    remaining_stops_sha256: str
    bay_id: str
    external_index: int
    lane_id: str
    lane_offset_m: float
    working_s: float
    soc: float
    observed_real_life_s: float
    departure: HeldDepartureInputs | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )

    def __post_init__(self):
        if not isinstance(self.scope, SourceScope):
            raise ValueError("Held charging stop needs a registered source")
        if str(UUID(self.interrupted_command_id)) != self.interrupted_command_id:
            raise ValueError("Held charging stop needs its applied command identity")
        for value in (
            self.manifest_sha256,
            self.assignment_sha256,
            self.route_revision,
            self.remaining_stops_sha256,
        ):
            digest(value, "held charging input digest")
        text(self.bay_id, "interrupted bay")
        text(self.lane_id, "held lane")
        if self.lane_id.startswith(":"):
            raise ValueError("Held charging stop cannot lie inside a junction")
        integer(self.external_index, "held road occurrence", 1, 9_999)
        number(self.lane_offset_m, "held position", 0, 1e7)
        number(self.working_s, "held working time", 0, 1e12)
        number(self.soc, "held battery charge", 0, 1)
        number(self.observed_real_life_s, "held real-life time", 0, 1e12)
        if self.departure is not None and not isinstance(self.departure, HeldDepartureInputs):
            raise ValueError("Held charging stop needs typed departure inputs")


@dataclass(frozen=True)
class ReplacementOf:
    command_id: str
    evidence_sha256: str

    def __post_init__(self):
        if str(UUID(self.command_id)) != self.command_id:
            raise ValueError("Replacement requires the original command identity")
        digest(self.evidence_sha256, "interruption evidence digest")
