"""Immutable internal contracts. Admission authenticates their external provenance.

These records describe units and supported numerical domains. They do not grant
permission, establish sensor truth, or make a model qualification claim.
"""

import math
from dataclasses import dataclass, field

from .validation import choice, digest, integer, number, sequence, text

MAX_SEGMENTS = 10_000
MAX_VERTICES = 100_000


@dataclass(frozen=True)
class Origin:
    kind: str
    source: str
    reference: str

    def __post_init__(self) -> None:
        choice(self.kind, "origin kind", ("observed", "forecast", "declared", "synthetic"))
        text(self.source, "origin source")
        text(self.reference, "origin reference")


@dataclass(frozen=True)
class CurvePoint:
    x: float
    y: float

    def __post_init__(self) -> None:
        number(self.x, "curve x", -1e12, 1e12)
        number(self.y, "curve y", -1e12, 1e12)


@dataclass(frozen=True)
class Curve:
    """A piecewise-linear declared curve; outside-domain queries are rejected."""

    x_unit: str
    y_unit: str
    points: tuple[CurvePoint, ...]
    origin: Origin

    def __post_init__(self) -> None:
        choice(self.x_unit, "curve input unit", ("K", "1", "W", "m/s"))
        choice(self.y_unit, "curve output unit", ("1", "W"))
        sequence(self.points, "curve points", CurvePoint, 2, 128)
        if not isinstance(self.origin, Origin):
            raise ValueError("Curve origin required")
        if any(b.x <= a.x for a, b in zip(self.points, self.points[1:])):
            raise ValueError("Curve coordinates must be strictly increasing")

    def at(self, x: float) -> float:
        number(x, "curve query", self.points[0].x, self.points[-1].x)
        for a, b in zip(self.points, self.points[1:]):
            if x <= b.x:
                return a.y + (b.y - a.y) * ((x - a.x) / (b.x - a.x))
        raise ValueError("Curve query is outside its domain")


@dataclass(frozen=True)
class VehicleProfile:
    profile_id: str
    origin: Origin
    capacity_kwh: float
    reserve_soc: float
    loaded_mass_kg: float
    max_loaded_mass_kg: float
    rotating_mass_kg: float
    frontal_area_m2: float
    drag_coefficient: float
    rolling_coefficient: float
    air_density_kg_m3: float
    propulsion_efficiency: float
    regeneration_efficiency: float
    max_discharge_w: float
    max_regeneration_w: float
    max_acceleration_m_s2: float
    max_deceleration_m_s2: float
    gravity_m_s2: float
    propulsion_by_power: Curve | None = None
    regeneration_by_soc: Curve | None = None
    discharge_by_temperature: Curve | None = None
    regeneration_by_temperature: Curve | None = None
    usable_fraction_by_temperature: Curve | None = None
    max_speed_m_s: float | None = field(default=None, metadata={"omit_none_on_wire": True})

    def __post_init__(self) -> None:
        text(self.profile_id, "vehicle profile")
        if not isinstance(self.origin, Origin):
            raise ValueError("Vehicle parameter origin required")
        ranges = {
            "capacity_kwh": (0.01, 10_000),
            "reserve_soc": (0, 1),
            "loaded_mass_kg": (100, 100_000),
            "max_loaded_mass_kg": (100, 100_000),
            "rotating_mass_kg": (0, 10_000),
            "frontal_area_m2": (0.1, 30),
            "drag_coefficient": (0, 3),
            "rolling_coefficient": (0, 0.1),
            "air_density_kg_m3": (0.1, 2),
            "propulsion_efficiency": (0.01, 1),
            "regeneration_efficiency": (0, 1),
            "max_discharge_w": (1, 5_000_000),
            "max_regeneration_w": (0, 5_000_000),
            "max_acceleration_m_s2": (0.01, 5),
            "max_deceleration_m_s2": (0.01, 10),
            "gravity_m_s2": (9, 10),
        }
        for name, (low, high) in ranges.items():
            number(getattr(self, name), name, low, high)
        if self.loaded_mass_kg > self.max_loaded_mass_kg:
            raise ValueError("Loaded mass exceeds the declared vehicle limit")
        if self.max_speed_m_s is not None:
            number(self.max_speed_m_s, "vehicle speed limit", 0.1, 60)
        curves = (
            (self.propulsion_by_power, "W", "1", 0.01, 1),
            (self.regeneration_by_soc, "1", "W", 0, self.max_regeneration_w),
            (self.discharge_by_temperature, "K", "W", 0, self.max_discharge_w),
            (self.regeneration_by_temperature, "K", "W", 0, self.max_regeneration_w),
            (self.usable_fraction_by_temperature, "K", "1", 0.01, 1),
        )
        for curve, x_unit, y_unit, low, high in curves:
            if curve is None:
                continue
            if not isinstance(curve, Curve) or (curve.x_unit, curve.y_unit) != (x_unit, y_unit):
                raise ValueError("Vehicle curve units or type mismatch")
            for point in curve.points:
                number(point.y, "vehicle curve output", low, high)
                if x_unit == "1":
                    number(point.x, "curve charge fraction", 0, 1)
                elif x_unit == "W":
                    number(point.x, "curve power", 0, self.max_discharge_w)
                else:
                    number(point.x, "curve temperature", 200, 400)


@dataclass(frozen=True)
class RoadPoint:
    """Meters in the route context's horizontal frame and vertical datum."""

    x_m: float
    y_m: float
    elevation_m: float | None
    latitude_deg: float | None = None
    longitude_deg: float | None = None

    def __post_init__(self) -> None:
        number(self.x_m, "road x", -50_000_000, 50_000_000)
        number(self.y_m, "road y", -50_000_000, 50_000_000)
        if self.elevation_m is not None:
            number(self.elevation_m, "elevation", -1_000, 10_000)
        if (self.latitude_deg is None) != (self.longitude_deg is None):
            raise ValueError("Geographic coordinates require both latitude and longitude")
        if self.latitude_deg is not None and self.longitude_deg is not None:
            number(self.latitude_deg, "latitude", -89, 89)
            number(self.longitude_deg, "longitude", -180, 180)


@dataclass(frozen=True)
class PlannedStop:
    stop_id: str
    offset_m: float
    dwell_working_s: float

    def __post_init__(self) -> None:
        text(self.stop_id, "stop identity")
        number(self.offset_m, "stop offset", 0, 10_000_000)
        number(self.dwell_working_s, "stop dwell", 0, 14_400)


@dataclass(frozen=True)
class SignalWindow:
    start_s: float
    end_s: float

    def __post_init__(self) -> None:
        number(self.start_s, "signal interval start", 0, 600)
        number(self.end_s, "signal interval end", 0, 600)
        if self.end_s <= self.start_s:
            raise ValueError("Signal interval must have positive duration")


@dataclass(frozen=True)
class FixedSignal:
    signal_id: str
    cycle_working_s: float
    offset_working_s: float
    green: tuple[SignalWindow, ...]
    yellow: tuple[SignalWindow, ...] | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )

    def __post_init__(self) -> None:
        text(self.signal_id, "signal identity")
        number(self.cycle_working_s, "signal cycle", 1, 600)
        number(self.offset_working_s, "signal offset", -14_400, 14_400)
        if not isinstance(self.green, tuple) or not 1 <= len(self.green) <= 32:
            raise ValueError("One to 32 green intervals required")
        self._validate_windows(self.green)
        if self.yellow is not None:
            if not isinstance(self.yellow, tuple) or not 1 <= len(self.yellow) <= 32:
                raise ValueError("One to 32 yellow intervals required")
            self._validate_windows(self.yellow)
            self._validate_windows(
                tuple(sorted((*self.green, *self.yellow), key=lambda w: w.start_s))
            )

    def _validate_windows(self, windows: tuple[SignalWindow, ...]) -> None:
        previous = 0.0
        for window in windows:
            if not isinstance(window, SignalWindow):
                raise ValueError("Signal interval required")
            if window.start_s < previous or window.end_s > self.cycle_working_s:
                raise ValueError("Signal intervals overlap or exceed the cycle")
            previous = window.end_s

    def is_yellow(self, working_s: float) -> bool:
        phase = (working_s - self.offset_working_s) % self.cycle_working_s
        return any(window.start_s <= phase < window.end_s for window in self.yellow or ())

    def wait(self, working_s: float) -> float:
        phase = (working_s - self.offset_working_s) % self.cycle_working_s
        for window in self.green:
            if window.start_s <= phase < window.end_s:
                return 0.0
            if phase < window.start_s:
                return window.start_s - phase
        return self.cycle_working_s - phase + self.green[0].start_s


@dataclass(frozen=True)
class RouteOccurrence:
    index: int
    edge_id: str
    lane_id: str
    shape: tuple[RoadPoint, ...]
    speed_limit_m_s: float
    road_class: str
    structure: str
    elevation_quality: str
    stops: tuple[PlannedStop, ...] = ()
    reference_length_m: float | None = None
    geometry_basis: str = "lane_centerline"
    alternative_lane_ids: tuple[str, ...] = ()
    yield_visibility_m: float | None = field(default=None, metadata={"omit_none_on_wire": True})
    fixed_signal: FixedSignal | None = field(default=None, metadata={"omit_none_on_wire": True})
    signal_controlled: bool | None = field(default=None, metadata={"omit_none_on_wire": True})

    def __post_init__(self) -> None:
        integer(self.index, "occurrence index", 0, MAX_SEGMENTS - 1)
        text(self.edge_id, "edge identity")
        text(self.lane_id, "lane identity")
        text(self.road_class, "road class", 64)
        choice(
            self.geometry_basis, "road geometry basis", ("lane_centerline", "assumed_lane_change")
        )
        if not isinstance(self.alternative_lane_ids, tuple) or len(self.alternative_lane_ids) > 2:
            raise ValueError("At most two alternative lane identities are supported")
        for lane in self.alternative_lane_ids:
            text(lane, "alternative lane identity")
        if self.geometry_basis == "assumed_lane_change" and len(self.alternative_lane_ids) != 2:
            raise ValueError("Assumed lane change requires entry and exit lanes")
        number(self.speed_limit_m_s, "speed limit", 0.01, 60)
        if self.yield_visibility_m is not None:
            number(self.yield_visibility_m, "yield visibility", 0.01, 1_000)
        if self.fixed_signal is not None and not isinstance(self.fixed_signal, FixedSignal):
            raise ValueError("Fixed signal schedule required")
        if self.signal_controlled is not None and type(self.signal_controlled) is not bool:
            raise ValueError("Signal-controlled flag must be boolean")
        if self.reference_length_m is not None:
            number(self.reference_length_m, "source lane length", 0.001, 10_000_000)
        choice(self.structure, "road structure", ("surface", "bridge", "tunnel"))
        choice(
            self.elevation_quality,
            "elevation quality",
            (
                "verified_road",
                "terrain",
                "surface_model",
                "portal_interpolation",
                "declared_flat",
                "synthetic",
                "missing",
            ),
        )
        sequence(self.shape, "road shape", RoadPoint, 2, MAX_VERTICES)
        sequence(self.stops, "stops", PlannedStop, 0, 1_000)
        if len({stop.stop_id for stop in self.stops}) != len(self.stops):
            raise ValueError("Duplicate stop within one route occurrence")
        if any(b.offset_m < a.offset_m for a, b in zip(self.stops, self.stops[1:])):
            raise ValueError("Stops must be ordered along the occurrence")
        if any(a.x_m == b.x_m and a.y_m == b.y_m for a, b in zip(self.shape, self.shape[1:])):
            raise ValueError("Road shape has a zero horizontal-length segment")
        has_missing = any(p.elevation_m is None for p in self.shape)
        if has_missing != (self.elevation_quality == "missing"):
            raise ValueError("Elevation quality and missing values disagree")
        if self.elevation_quality == "declared_flat" and any(
            p.elevation_m != self.shape[0].elevation_m for p in self.shape
        ):
            raise ValueError("Declared flat road has changing height")
        if self.structure != "surface" and self.elevation_quality == "terrain":
            raise ValueError("Terrain elevation does not establish bridge or tunnel road height")


@dataclass(frozen=True)
class RouteContext:
    network_sha256: str
    revision: str
    horizontal_frame: str
    vertical_datum: str
    elevation_origin: Origin
    occurrences: tuple[RouteOccurrence, ...]

    def __post_init__(self) -> None:
        digest(self.network_sha256, "network digest")
        text(self.revision, "route revision")
        text(self.horizontal_frame, "horizontal frame")
        text(self.vertical_datum, "vertical datum")
        if not isinstance(self.elevation_origin, Origin):
            raise ValueError("Elevation origin required")
        sequence(self.occurrences, "route occurrences", RouteOccurrence, 1, MAX_SEGMENTS)
        if tuple(o.index for o in self.occurrences) != tuple(range(len(self.occurrences))):
            raise ValueError("Route occurrences must be consecutively indexed")
        if sum(len(o.shape) for o in self.occurrences) > MAX_VERTICES:
            raise ValueError("Route exceeds the vertex limit")


@dataclass(frozen=True)
class SourceScope:
    run_id: str
    asset_id: str
    source_id: str
    source_epoch: int
    session_id: str

    def __post_init__(self) -> None:
        for name in ("run_id", "asset_id", "source_id", "session_id"):
            text(getattr(self, name), name, 128)
        integer(self.source_epoch, "source epoch", 1, 9_007_199_254_740_991)


@dataclass(frozen=True)
class StopRef:
    occurrence_index: int
    stop_id: str

    def __post_init__(self) -> None:
        integer(self.occurrence_index, "stop occurrence", 0, MAX_SEGMENTS - 1)
        text(self.stop_id, "stop identity")


@dataclass(frozen=True)
class LiveState:
    scope: SourceScope
    event_id: str
    wire_sha256: str
    working_s: float
    observed_real_life_s: float
    received_real_life_s: float
    source_state: str
    route_revision: str
    occurrence_index: int
    lane_offset_m: float
    soc: float
    speed_m_s: float
    auxiliary_power_w: float
    charging_state: str
    battery_temperature_k: float | None = None
    completed_stops: tuple[StopRef, ...] = ()
    current_stop: StopRef | None = None
    remaining_dwell_working_s: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, SourceScope):
            raise ValueError("Source scope required")
        text(self.event_id, "event identity")
        digest(self.wire_sha256, "wire digest")
        text(self.route_revision, "route revision")
        number(self.working_s, "working time", 0, 1e12)
        for name in ("observed_real_life_s", "received_real_life_s"):
            number(getattr(self, name), name, 0, 1e12)
        integer(self.occurrence_index, "occurrence", 0, MAX_SEGMENTS - 1)
        number(self.lane_offset_m, "lane offset", 0, 10_000_000)
        number(self.soc, "charge fraction", 0, 1)
        number(self.speed_m_s, "speed", 0, 100)
        number(self.auxiliary_power_w, "auxiliary power", 0, 1_000_000)
        choice(self.source_state, "source state", ("running", "paused", "finished"))
        choice(self.charging_state, "charging state", ("charging", "not_charging", "unknown"))
        if self.battery_temperature_k is not None:
            number(self.battery_temperature_k, "battery temperature", 200, 400)
        sequence(self.completed_stops, "completed stops", StopRef, 0, 10_000)
        if len(set(self.completed_stops)) != len(self.completed_stops):
            raise ValueError("Duplicate completed stop")
        if self.current_stop is not None:
            if (
                not isinstance(self.current_stop, StopRef)
                or self.current_stop.occurrence_index != self.occurrence_index
                or self.current_stop in self.completed_stops
            ):
                raise ValueError("Current stop conflicts with current progress")
        if self.remaining_dwell_working_s is not None:
            if self.current_stop is None:
                raise ValueError("Remaining dwell requires a current stop")
            number(self.remaining_dwell_working_s, "remaining dwell", 0, 14_400)


@dataclass(frozen=True)
class Conditions:
    """A selected, validity-bounded scenario/forecast, never a hidden future outcome.

    Wind components point toward the direction the air travels. Extra auxiliary
    load explicitly excludes equipment already counted in observed aggregate power.
    """

    origin: Origin
    available_real_life_s: float
    from_working_s: float
    until_working_s: float
    wind_east_m_s: float = 0
    wind_north_m_s: float = 0
    ambient_temperature_k: float | None = None
    pressure_pa: float | None = None
    loaded_mass_kg: float | None = None
    rolling_multiplier: float = 1
    speed_factor: float = 1
    extra_auxiliary_power_w: float = 0
    wind_basis: str = "declared_calm"
    auxiliary_power_w: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.origin, Origin):
            raise ValueError("Conditions origin required")
        number(self.available_real_life_s, "condition availability", 0, 1e12)
        number(self.from_working_s, "condition start", 0, 1e12)
        number(self.until_working_s, "condition end", 0, 1e12)
        if self.until_working_s <= self.from_working_s:
            raise ValueError("Conditions need a positive validity interval")
        for value in (self.wind_east_m_s, self.wind_north_m_s):
            number(value, "wind component", -60, 60)
        if math.hypot(self.wind_east_m_s, self.wind_north_m_s) > 60:
            raise ValueError("Wind exceeds the supported magnitude")
        choice(
            self.wind_basis, "wind basis", ("declared_calm", "observed", "forecast", "synthetic")
        )
        if self.wind_basis == "declared_calm" and (self.wind_east_m_s or self.wind_north_m_s):
            raise ValueError("Declared calm wind must have zero components")
        if self.ambient_temperature_k is not None:
            number(self.ambient_temperature_k, "ambient temperature", 200, 340)
        if self.pressure_pa is not None:
            number(self.pressure_pa, "pressure", 30_000, 120_000)
        if self.loaded_mass_kg is not None:
            number(self.loaded_mass_kg, "condition mass", 100, 100_000)
        number(self.rolling_multiplier, "rolling multiplier", 0.1, 5)
        number(self.speed_factor, "speed factor", 0.01, 1)
        number(self.extra_auxiliary_power_w, "extra auxiliary power", 0, 1_000_000)
        if self.auxiliary_power_w is not None:
            number(self.auxiliary_power_w, "declared forward auxiliary power", 0, 1_000_000)
