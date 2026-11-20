"""Scoped forward assumptions for individual roads and scheduled stops."""

from dataclasses import dataclass, replace

from .contracts import Conditions, Origin, RouteContext, StopRef, VehicleProfile
from .environment import EquipmentProfile
from .geometry import RemainingRoute, RemainingStop, RouteUnavailable
from .validation import integer, number, sequence, text


@dataclass(frozen=True)
class RoadConditions:
    occurrence_index: int
    intervals: tuple[Conditions, ...]

    def __post_init__(self) -> None:
        integer(self.occurrence_index, "condition road occurrence", 0, 9999)
        sequence(self.intervals, "road conditions", Conditions, 1, 1000)
        if any(
            b.from_working_s < a.until_working_s for a, b in zip(self.intervals, self.intervals[1:])
        ):
            raise ValueError("Road condition intervals must be ordered and nonoverlapping")


@dataclass(frozen=True)
class StopEffect:
    reference: StopRef
    additional_dwell_working_s: float = 0
    loaded_mass_on_departure_kg: float | None = None
    occupants_on_departure: int | None = None
    door_open_fraction: float = 0

    def __post_init__(self) -> None:
        if not isinstance(self.reference, StopRef):
            raise ValueError("Stop effect requires an occurrence-aware stop reference")
        number(self.additional_dwell_working_s, "additional stop dwell", 0, 3600)
        if self.loaded_mass_on_departure_kg is not None:
            number(self.loaded_mass_on_departure_kg, "departure mass", 100, 100_000)
        if self.occupants_on_departure is not None:
            integer(self.occupants_on_departure, "departure occupants", 0, 300)
        number(self.door_open_fraction, "door opening", 0, 1)


@dataclass(frozen=True)
class RouteScenario:
    scenario_id: str
    origin: Origin
    available_real_life_s: float
    roads: tuple[RoadConditions, ...] = ()
    stops: tuple[StopEffect, ...] = ()
    closed_occurrences: tuple[int, ...] = ()
    equipment: EquipmentProfile | None = None
    initial_occupants: int = 0
    absorbed_solar_w: float = 0
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        text(self.scenario_id, "scenario identity")
        if not isinstance(self.origin, Origin):
            raise ValueError("Scenario provenance required")
        number(self.available_real_life_s, "scenario availability", 0, 1e12)
        sequence(self.roads, "localized road conditions", RoadConditions, 0, 1000)
        sequence(self.stops, "stop effects", StopEffect, 0, 1000)
        sequence(self.closed_occurrences, "closed road occurrences", int, 0, 1000)
        sequence(self.notes, "scenario assumptions", str, 0, 32)
        for note in self.notes:
            text(note, "scenario assumption")
        for index in self.closed_occurrences:
            integer(index, "closed road occurrence", 0, 9999)
        if len({r.occurrence_index for r in self.roads}) != len(self.roads):
            raise ValueError("Duplicate localized road conditions")
        if len({s.reference for s in self.stops}) != len(self.stops):
            raise ValueError("Duplicate stop effects")
        if len(set(self.closed_occurrences)) != len(self.closed_occurrences):
            raise ValueError("Duplicate road closure")
        if sum(len(r.intervals) for r in self.roads) > 2000:
            raise ValueError("Localized condition interval budget exceeded")
        if self.equipment is not None and not isinstance(self.equipment, EquipmentProfile):
            raise ValueError("Equipment profile type mismatch")
        integer(self.initial_occupants, "initial occupants", 0, 300)
        number(self.absorbed_solar_w, "absorbed solar heat", 0, 100_000)
        if self.equipment is None and (
            self.initial_occupants
            or self.absorbed_solar_w
            or any(s.occupants_on_departure is not None or s.door_open_fraction for s in self.stops)
        ):
            raise ValueError("Occupancy, doors, and solar need an equipment model")

    def validate_for(
        self, route: RouteContext, vehicle: VehicleProfile, cutoff_real_life_s: float
    ) -> None:
        if self.available_real_life_s > cutoff_real_life_s:
            raise ValueError("Scenario was unavailable at the forecast cutoff")
        if any(
            index >= len(route.occurrences)
            for index in (*(road.occurrence_index for road in self.roads), *self.closed_occurrences)
        ):
            raise ValueError("Scenario refers to an unassigned road occurrence")
        stops = {
            StopRef(road.index, stop.stop_id) for road in route.occurrences for stop in road.stops
        }
        if any(effect.reference not in stops for effect in self.stops):
            raise ValueError("Scenario refers to an unassigned stop")
        ordered_stops = [
            StopRef(road.index, stop.stop_id) for road in route.occurrences for stop in road.stops
        ]
        positions = [ordered_stops.index(effect.reference) for effect in self.stops]
        if positions != sorted(positions):
            raise ValueError("Stop effects must follow assigned route order")
        if any(
            effect.loaded_mass_on_departure_kg is not None
            and effect.loaded_mass_on_departure_kg > vehicle.max_loaded_mass_kg
            for effect in self.stops
        ):
            raise ValueError("Departure mass exceeds the vehicle profile")
        for road in self.roads:
            for condition in road.intervals:
                if condition.available_real_life_s > cutoff_real_life_s:
                    raise ValueError("Localized condition was unavailable at the cutoff")
                if (
                    condition.loaded_mass_kg is not None
                    and condition.loaded_mass_kg > vehicle.max_loaded_mass_kg
                ):
                    raise ValueError("Localized condition exceeds maximum vehicle mass")
                if self.equipment is not None and condition.extra_auxiliary_power_w:
                    raise ValueError(
                        "Equipment model cannot combine with unpartitioned extra auxiliary power"
                    )


def apply_stop_assumptions(route: RemainingRoute, scenario: RouteScenario | None) -> RemainingRoute:
    if scenario is None:
        return route
    if any(piece.occurrence_index in scenario.closed_occurrences for piece in route.pieces):
        raise RouteUnavailable("assigned_route_contains_closed_road_requires_confirmed_detour")
    effects = {effect.reference: effect for effect in scenario.stops}

    def update(stop: RemainingStop) -> RemainingStop:
        effect = effects.get(stop.reference)
        return (
            replace(stop, dwell_working_s=stop.dwell_working_s + effect.additional_dwell_working_s)
            if effect
            else stop
        )

    # Current dwell comes from the source's remaining time; only future stops
    # receive an additional scenario delay. Repeated refreshes cannot restart it.
    return replace(
        route,
        pieces=tuple(
            replace(piece, stops_after=tuple(update(s) for s in piece.stops_after))
            for piece in route.pieces
        ),
        assumptions=(
            *route.assumptions,
            "localized_forward_scenario",
            "current_dwell_uses_source_remaining",
            *scenario.notes,
        ),
    )


def conditions_by_road_class(
    route: RouteContext, *, road_class: str, intervals: tuple[Conditions, ...]
) -> tuple[RoadConditions, ...]:
    """Expand a declared class profile into explicit immutable road occurrences."""
    text(road_class, "road class")
    matches = tuple(
        RoadConditions(road.index, intervals)
        for road in route.occurrences
        if road.road_class == road_class
    )
    if not matches:
        raise ValueError("The assigned route contains no matching road class")
    return matches
