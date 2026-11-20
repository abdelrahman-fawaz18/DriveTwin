"""Administrator-owned configuration, separate from authenticated source state."""

from dataclasses import dataclass
from uuid import UUID

from .contracts import Conditions, VehicleProfile
from .policy import ForecastPolicy
from .scenarios import RouteScenario
from .validation import choice, digest, integer, sequence, text


@dataclass(frozen=True)
class Assignment:
    version: str
    run_id: str
    asset_id: str
    source_id: str
    revision: int
    enabled: bool
    network_sha256: str
    geometry_bundle_sha256: str
    vehicle: VehicleProfile
    conditions: tuple[Conditions, ...]
    policy: ForecastPolicy
    scenario: RouteScenario | None = None
    scenario_route_revision: str | None = None

    def __post_init__(self) -> None:
        choice(self.version, "assignment version", ("route-energy-assignment/2.0.0",))
        if str(UUID(self.run_id)) != self.run_id:
            raise ValueError("Canonical run UUID required")
        text(self.asset_id, "assigned asset")
        text(self.source_id, "assigned source")
        integer(self.revision, "assignment revision", 1, 9_007_199_254_740_991)
        if type(self.enabled) is not bool:
            raise ValueError("Assignment enabled flag must be boolean")
        digest(self.network_sha256, "assigned network")
        digest(self.geometry_bundle_sha256, "assigned geometry bundle")
        if not isinstance(self.vehicle, VehicleProfile) or not isinstance(
            self.policy, ForecastPolicy
        ):
            raise ValueError("Assigned vehicle and policy required")
        sequence(self.conditions, "assigned conditions", Conditions, 1, 1000)
        if any(
            b.from_working_s < a.until_working_s
            for a, b in zip(self.conditions, self.conditions[1:])
        ):
            raise ValueError("Assigned conditions must be ordered and nonoverlapping")
        if self.scenario is not None and not isinstance(self.scenario, RouteScenario):
            raise ValueError("Assigned scenario type mismatch")
        if self.scenario_route_revision is not None:
            digest(self.scenario_route_revision, "scenario route revision")
        if (
            self.scenario is not None
            and (self.scenario.roads or self.scenario.stops or self.scenario.closed_occurrences)
            and self.scenario_route_revision is None
        ):
            raise ValueError("Localized scenario must be bound to its assigned route revision")
