"""Charging-only evidence from the unchanged route-aware integration."""

from dataclasses import dataclass, field, replace

from .route_aware.contracts import RoadPoint
from .route_aware.forecast import ForecastResult, forecast
from .route_aware.geometry import RoadPiece, horizontal_length, interpolate, reference_length
from .route_aware.motion import MotionPhase
from .route_aware.validation import integer, number
from .route_aware.wire import ForecastRequest, fingerprint


@dataclass(frozen=True)
class ChargePoint:
    working_s: float
    occurrence_index: int
    soc: float
    coordinates: tuple[float, ...] | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )

    def __post_init__(self) -> None:
        if self.coordinates is not None:
            if len(self.coordinates) != 2:
                raise ValueError("Reserve crossing requires longitude and latitude")
            number(self.coordinates[0], "crossing longitude", -180, 180)
            number(self.coordinates[1], "crossing latitude", -90, 90)


@dataclass(frozen=True)
class DrivingLeg:
    version: str
    forecast: ForecastResult
    initial: ChargePoint
    first_below_reserve: ChargePoint | None
    checkpoints: tuple[ChargePoint, ...]
    input_sha256: str


def calculate_leg(
    request: ForecastRequest, *, checkpoint_occurrences: tuple[int, ...] = ()
) -> DrivingLeg:
    """Record actual integration samples; the crossing is resolved to one model step."""
    if not isinstance(checkpoint_occurrences, tuple) or len(checkpoint_occurrences) > 64:
        raise ValueError("At most 64 charging checkpoints can be requested")
    for index in checkpoint_occurrences:
        integer(
            index,
            "checkpoint occurrence",
            request.state.occurrence_index,
            len(request.route.occurrences) - 1,
        )
    if checkpoint_occurrences != tuple(sorted(set(checkpoint_occurrences))):
        raise ValueError("Charging checkpoint occurrences must be unique and increasing")
    reserve = request.vehicle.reserve_soc
    number(reserve, "charge reserve", 0, 1)
    state = request.state
    initial = ChargePoint(state.working_s, state.occurrence_index, state.soc)
    first = initial if initial.soc < reserve else None
    samples: dict[int, ChargePoint] = {}
    requested = set(checkpoint_occurrences)

    def observe(
        working_s: float,
        occurrence: int,
        soc: float,
        piece: RoadPiece | None,
        phase: MotionPhase,
        elapsed: float,
        phase_start_distance_m: float,
    ) -> None:
        nonlocal first
        crossed = first is None and soc < reserve
        if crossed or occurrence in requested:
            point = ChargePoint(working_s, occurrence, soc)
            if crossed:
                if piece is None:
                    coordinates = _at_cutoff(request)
                elif phase.kind == "dwell":
                    coordinates = _geographic(piece.end)
                else:
                    phase_distance = (
                        phase.position_rate_m_s * elapsed
                        if phase.position_rate_m_s is not None
                        else phase.start_speed_m_s * elapsed
                        + 0.5 * phase.acceleration_m_s2 * elapsed**2
                    )
                    fraction = max(
                        0, min(1, (phase_start_distance_m + phase_distance) / piece.distance_m)
                    )
                    coordinates = _geographic(interpolate(piece.start, piece.end, fraction))
                first = replace(point, coordinates=coordinates)
            if occurrence in requested:
                samples[occurrence] = point

    result = forecast(request, sample=observe)
    version = "moving-charge-driving-leg/1.0.0"
    return DrivingLeg(
        version,
        result,
        initial,
        first if result.summary else None,
        tuple(samples[index] for index in checkpoint_occurrences if index in samples)
        if result.summary
        else (),
        fingerprint(
            {
                "version": version,
                "forecast_request_sha256": fingerprint(request),
                "checkpoint_occurrences": checkpoint_occurrences,
            }
        ),
    )


def _geographic(point: RoadPoint) -> tuple[float, float] | None:
    if point.longitude_deg is None or point.latitude_deg is None:
        return None
    return point.longitude_deg, point.latitude_deg


def _at_cutoff(request: ForecastRequest) -> tuple[float, float] | None:
    occurrence = request.route.occurrences[request.state.occurrence_index]
    if occurrence.shape[0].longitude_deg is None:
        return None
    scale = horizontal_length(occurrence.shape) / reference_length(occurrence)
    target = request.state.lane_offset_m * scale
    for start, end in zip(occurrence.shape, occurrence.shape[1:]):
        distance = horizontal_length((start, end))
        if distance and target <= distance:
            return _geographic(interpolate(start, end, max(0, target / distance)))
        target -= distance
    return _geographic(occurrence.shape[-1])
