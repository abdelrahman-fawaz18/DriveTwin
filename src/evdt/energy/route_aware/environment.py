"""Explicit weather normalization and declared equipment-load assumptions."""

import math
from dataclasses import dataclass, replace

from .contracts import Conditions, Origin
from .validation import number, sequence, text


def wind_vector(speed_m_s: float, from_degrees: float) -> tuple[float, float]:
    """Meteorological north=0, east=90; return air movement east/north."""
    number(speed_m_s, "wind speed", 0, 60)
    number(from_degrees, "wind from direction", 0, 360)
    angle = math.radians(from_degrees)
    return -speed_m_s * math.sin(angle), -speed_m_s * math.cos(angle)


@dataclass(frozen=True)
class TimeMapping:
    """Scenario correspondence, not a measurement of execution speed."""

    working_origin_s: float
    real_life_origin_s: float
    real_life_seconds_per_working_second: float

    def __post_init__(self) -> None:
        number(self.working_origin_s, "working origin", 0, 1e12)
        number(self.real_life_origin_s, "real-life origin", 0, 1e12)
        number(self.real_life_seconds_per_working_second, "scenario time scale", 0.001, 1000)

    def real_life(self, working_s: float) -> float:
        number(working_s, "working time", 0, 1e12)
        return (
            self.real_life_origin_s
            + (working_s - self.working_origin_s) * self.real_life_seconds_per_working_second
        )

    def working(self, real_life_s: float) -> float:
        number(real_life_s, "real-life time", 0, 1e12)
        return (
            self.working_origin_s
            + (real_life_s - self.real_life_origin_s) / self.real_life_seconds_per_working_second
        )


@dataclass(frozen=True)
class WeatherCell:
    """Issued forecast for a bounded area and time; no observed-future input."""

    origin: Origin
    issued_real_life_s: float
    available_real_life_s: float
    valid_from_real_life_s: float
    valid_until_real_life_s: float
    west_deg: float
    south_deg: float
    east_deg: float
    north_deg: float
    wind_speed_m_s: float
    wind_from_deg: float
    wind_height_m: float
    exposure: str
    temperature_k: float
    pressure_pa: float

    def __post_init__(self) -> None:
        if not isinstance(self.origin, Origin) or self.origin.kind not in ("forecast", "synthetic"):
            raise ValueError("Weather cells require forecast or synthetic provenance")
        for value in (
            self.issued_real_life_s,
            self.available_real_life_s,
            self.valid_from_real_life_s,
            self.valid_until_real_life_s,
        ):
            number(value, "weather real-life time", 0, 1e12)
        if self.issued_real_life_s > self.available_real_life_s:
            raise ValueError("Weather cannot arrive before it was issued")
        if self.valid_until_real_life_s <= self.valid_from_real_life_s:
            raise ValueError("Weather validity must be positive")
        for value in (self.west_deg, self.east_deg):
            number(value, "longitude", -180, 180)
        for value in (self.south_deg, self.north_deg):
            number(value, "latitude", -90, 90)
        if self.west_deg >= self.east_deg or self.south_deg >= self.north_deg:
            raise ValueError("Weather cell must have positive area without antimeridian crossing")
        wind_vector(self.wind_speed_m_s, self.wind_from_deg)
        number(self.wind_height_m, "wind reference height", 0.1, 1000)
        text(self.exposure, "wind exposure")
        number(self.temperature_k, "weather temperature", 200, 340)
        number(self.pressure_pa, "weather pressure", 30_000, 120_000)


def weather_conditions(
    base: Conditions,
    cells: tuple[WeatherCell, ...],
    mapping: TimeMapping,
    *,
    latitude_deg: float,
    longitude_deg: float,
    cutoff_real_life_s: float,
    max_issue_age_real_life_s: float,
) -> tuple[Conditions, ...]:
    """Select one unambiguous issued series for a location and complete interval.

    Call separately for different route locations. Reference-height wind is used
    directly; no invented urban shelter or gust scaling is applied.
    """
    sequence(cells, "weather cells", WeatherCell, 1, 1000)
    number(latitude_deg, "latitude", -90, 90)
    number(longitude_deg, "longitude", -180, 180)
    number(cutoff_real_life_s, "weather cutoff", 0, 1e12)
    number(max_issue_age_real_life_s, "weather maximum issue age", 0, 604800)
    selected = sorted(
        (
            cell
            for cell in cells
            if cell.west_deg <= longitude_deg < cell.east_deg
            and cell.south_deg <= latitude_deg < cell.north_deg
            and cell.available_real_life_s <= cutoff_real_life_s
            and 0 <= cutoff_real_life_s - cell.issued_real_life_s <= max_issue_age_real_life_s
            and mapping.working(cell.valid_until_real_life_s) > base.from_working_s
            and mapping.working(cell.valid_from_real_life_s) < base.until_working_s
        ),
        key=lambda cell: cell.valid_from_real_life_s,
    )
    cursor = base.from_working_s
    result = []
    for cell in selected:
        start = max(base.from_working_s, mapping.working(cell.valid_from_real_life_s))
        end = min(base.until_working_s, mapping.working(cell.valid_until_real_life_s))
        if abs(start - cursor) > 1e-7:
            raise ValueError("Weather coverage is missing or ambiguous")
        east, north = wind_vector(cell.wind_speed_m_s, cell.wind_from_deg)
        result.append(
            replace(
                base,
                origin=cell.origin,
                available_real_life_s=cell.available_real_life_s,
                from_working_s=start,
                until_working_s=end,
                wind_east_m_s=east,
                wind_north_m_s=north,
                wind_basis=cell.origin.kind,
                ambient_temperature_k=cell.temperature_k,
                pressure_pa=cell.pressure_pa,
            )
        )
        cursor = end
    if abs(cursor - base.until_working_s) > 1e-7:
        raise ValueError("Weather does not cover the requested location and horizon")
    return tuple(result)


@dataclass(frozen=True)
class EquipmentProfile:
    profile_id: str
    origin: Origin
    base_electrical_w: float
    ventilation_electrical_w: float
    envelope_conductance_w_k: float
    door_conductance_w_k: float
    setpoint_k: float
    heating_cop: float
    cooling_cop: float
    maximum_heating_electrical_w: float
    maximum_cooling_electrical_w: float
    occupant_heat_w: float

    def __post_init__(self) -> None:
        text(self.profile_id, "equipment profile")
        if not isinstance(self.origin, Origin):
            raise ValueError("Equipment provenance required")
        for value in (
            self.base_electrical_w,
            self.ventilation_electrical_w,
            self.maximum_heating_electrical_w,
            self.maximum_cooling_electrical_w,
        ):
            number(value, "equipment electrical power", 0, 200_000)
        for value in (self.envelope_conductance_w_k, self.door_conductance_w_k):
            number(value, "thermal conductance", 0, 20_000)
        number(self.setpoint_k, "cabin setpoint", 270, 310)
        number(self.heating_cop, "heating coefficient of performance", 0.1, 10)
        number(self.cooling_cop, "cooling coefficient of performance", 0.1, 10)
        number(self.occupant_heat_w, "occupant heat", 0, 500)


@dataclass(frozen=True)
class EquipmentDemand:
    total_electrical_w: float
    climate_electrical_w: float
    unserved_heating_thermal_w: float
    unserved_cooling_thermal_w: float
    assumptions: tuple[str, ...]


def equipment_demand(
    profile: EquipmentProfile,
    *,
    ambient_temperature_k: float,
    occupants: int,
    door_open_fraction: float,
    absorbed_solar_w: float,
) -> EquipmentDemand:
    """Steady thermal balance: explicit scenario coefficients, no cabin inertia."""
    from .validation import integer

    number(ambient_temperature_k, "ambient temperature", 200, 340)
    integer(occupants, "occupants", 0, 300)
    number(door_open_fraction, "door open fraction", 0, 1)
    number(absorbed_solar_w, "absorbed solar heat", 0, 100_000)
    conductance = profile.envelope_conductance_w_k + (
        profile.door_conductance_w_k * door_open_fraction
    )
    heat_needed = conductance * (profile.setpoint_k - ambient_temperature_k) - (
        occupants * profile.occupant_heat_w + absorbed_solar_w
    )
    heating = max(0.0, heat_needed)
    cooling = max(0.0, -heat_needed)
    heat_electric = min(heating / profile.heating_cop, profile.maximum_heating_electrical_w)
    cool_electric = min(cooling / profile.cooling_cop, profile.maximum_cooling_electrical_w)
    climate = heat_electric + cool_electric
    return EquipmentDemand(
        profile.base_electrical_w + profile.ventilation_electrical_w + climate,
        climate,
        max(0.0, heating - heat_electric * profile.heating_cop),
        max(0.0, cooling - cool_electric * profile.cooling_cop),
        (
            "declared_steady_cabin_balance",
            "cabin_thermal_inertia_excluded",
            "solar_input_is_absorbed_heat",
            "climate_power_limited_comfort_not_guaranteed",
        ),
    )
