"""Registered source plan/progress records, separate from scalar measurements.

These records describe present state and assigned plans. Parsing establishes
structure only; the producer endpoint and database establish source authority.
"""

import json
from dataclasses import dataclass

from .contracts import SourceScope
from .validation import choice, digest, integer, number, sequence, text
from .wire import _bad_constant, _pairs, decode_record, fingerprint


@dataclass(frozen=True)
class SourceStop:
    stop_id: str
    external_index: int
    lane_id: str
    offset_m: float
    dwell_working_s: float

    def __post_init__(self) -> None:
        text(self.stop_id, "stop identity")
        integer(self.external_index, "stop road occurrence", 0, 9_999)
        text(self.lane_id, "stop lane")
        number(self.offset_m, "stop offset", 0, 10_000_000)
        number(self.dwell_working_s, "stop dwell", 0, 14_400)


@dataclass(frozen=True)
class SourcePlan:
    network_sha256: str
    edges: tuple[str, ...]
    remaining_stops: tuple[SourceStop, ...]

    def __post_init__(self) -> None:
        digest(self.network_sha256, "network digest")
        sequence(self.edges, "assigned roads", str, 1, 10_000)
        for edge in self.edges:
            text(edge, "road identity")
        sequence(self.remaining_stops, "remaining stops", SourceStop, 0, 1_000)
        if len({s.stop_id for s in self.remaining_stops}) != len(self.remaining_stops):
            raise ValueError("Remaining stop identities must be unique")
        if any(s.external_index >= len(self.edges) for s in self.remaining_stops):
            raise ValueError("Stop outside assigned route")
        positions = [(s.external_index, s.offset_m) for s in self.remaining_stops]
        if positions != sorted(positions):
            raise ValueError("Remaining stops must follow their route occurrences")

    @property
    def revision(self) -> str:
        return fingerprint(self)


@dataclass(frozen=True)
class SourceContext:
    version: str
    scope: SourceScope
    manifest_sha256: str
    working_s: float
    observed_real_life_s: float
    plan: SourcePlan
    external_index: int
    lane_id: str
    lane_offset_m: float
    charging_state: str
    current_stop_id: str | None
    remaining_dwell_working_s: float | None

    def __post_init__(self) -> None:
        choice(self.version, "source context version", ("source-route-context/2.0.0",))
        if not isinstance(self.scope, SourceScope) or not isinstance(self.plan, SourcePlan):
            raise ValueError("Typed source identity and plan required")
        digest(self.manifest_sha256, "registration digest")
        number(self.working_s, "context working time", 0, 1e12)
        number(self.observed_real_life_s, "context observed time", 0, 1e12)
        integer(self.external_index, "current road occurrence", 0, len(self.plan.edges) - 1)
        text(self.lane_id, "current lane")
        number(self.lane_offset_m, "current lane offset", 0, 10_000_000)
        choice(self.charging_state, "charging state", ("charging", "not_charging", "unknown"))
        if self.current_stop_id is not None:
            stops = [s for s in self.plan.remaining_stops if s.stop_id == self.current_stop_id]
            if len(stops) != 1 or stops[0].external_index != self.external_index:
                raise ValueError("Current stop does not belong to current road occurrence")
            if self.remaining_dwell_working_s is None:
                raise ValueError("Current stop requires remaining dwell")
            number(self.remaining_dwell_working_s, "remaining dwell", 0, 14_400)
        elif self.remaining_dwell_working_s is not None:
            raise ValueError("Remaining dwell requires a current stop")


def parse_context(raw: bytes) -> SourceContext:
    if not isinstance(raw, bytes) or len(raw) > 65_536:
        raise ValueError("Source route context exceeds metadata budget")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_bad_constant)
        return decode_record(SourceContext, value)
    except (RecursionError, UnicodeError, TypeError, KeyError) as exc:
        raise ValueError("Invalid source route context") from exc
