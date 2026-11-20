"""Training-only bounded parameter fitting and historical forward assumptions.

This offline/controller module is intentionally excluded from the isolated worker.
It has no plant, truth, file, network or database client.
"""

import itertools
import math
import statistics
from dataclasses import asdict, dataclass, replace
from typing import Any

from evdt.energy.model import DrivingInterval, EnergyParameters, estimate_energy
from evdt.energy.observations import EnergyPoint, fingerprint, number


@dataclass(frozen=True)
class TrainingTrip:
    mission_id: str
    partition: str
    points: tuple[EnergyPoint, ...]
    auxiliary_power_w: float

    def __post_init__(self) -> None:
        if self.partition != "train":
            raise ValueError("Fitting and template selection accept training trips only")
        if (
            not self.mission_id
            or not isinstance(self.points, tuple)
            or not 2 <= len(self.points) <= 2500
        ):
            raise ValueError("Invalid bounded complete training trip")
        if number(self.auxiliary_power_w, "auxiliary_power_w") < 0:
            raise ValueError("Negative auxiliary demand")
        first = self.points[0]
        for previous, current in zip(self.points, self.points[1:]):
            if (
                current.scope != first.scope
                or not 0 < current.sim_time_s - previous.sim_time_s <= 1.5
                or current.stream_seq <= previous.stream_seq
                or current.distance_m < previous.distance_m
            ):
                raise ValueError("Training trip identity, order, gap or odometer invalid")

    @property
    def observed_net_kwh(self) -> float:
        return (self.points[0].energy_j - self.points[-1].energy_j) / 3_600_000

    def motion(self) -> tuple[DrivingInterval, ...]:
        return tuple(
            DrivingInterval(
                b.sim_time_s - a.sim_time_s, a.speed_m_s, b.speed_m_s, 0, self.auxiliary_power_w
            )
            for a, b in zip(self.points, self.points[1:])
        )


@dataclass(frozen=True)
class GridFit:
    version: str
    parameters: EnergyParameters
    training_ids: tuple[str, ...]
    training_mse_kwh2: float
    candidate_scores: tuple[tuple[float, float, float], ...]
    leave_one_trip_out_parameters: tuple[tuple[float, float], ...]


def joint_parameter_pairs(
    fit: GridFit, *, relative_mse_slack: float, absolute_mse_slack_kwh2: float
) -> tuple[tuple[float, float], ...]:
    """Retain joint training-compatible pairs and all leave-one-trip-out choices.

    This is an empirical scenario selection, not a parameter confidence region.
    It preserves the tradeoff between resistance and efficiency rather than
    inventing independent combinations unsupported by the training objective.
    """
    if (
        number(relative_mse_slack, "relative_mse_slack") < 0
        or number(absolute_mse_slack_kwh2, "absolute_mse_slack_kwh2") < 0
    ):
        raise ValueError("Nonnegative training-loss slack required")
    limit = fit.training_mse_kwh2 * (1 + relative_mse_slack) + absolute_mse_slack_kwh2
    pairs = {(r, e) for loss, r, e in fit.candidate_scores if loss <= limit}
    pairs.update(fit.leave_one_trip_out_parameters)
    return tuple(sorted(pairs))


def fit_grid(
    trips: tuple[TrainingTrip, ...],
    base: EnergyParameters,
    rolling: tuple[float, ...],
    efficiencies: tuple[float, ...],
    *,
    step_s: float = 0.2,
) -> GridFit:
    if (
        not isinstance(trips, tuple)
        or not 2 <= len(trips) <= 100
        or len({t.mission_id for t in trips}) != len(trips)
    ):
        raise ValueError("Distinct whole training trips required")
    if not rolling or not efficiencies or len(rolling) * len(efficiencies) > 256:
        raise ValueError("Invalid bounded parameter grid")
    if any(number(r, "rolling") < 0 for r in rolling) or any(
        not 0 < number(e, "efficiency") <= 1 for e in efficiencies
    ):
        raise ValueError("Invalid physical parameter candidates")
    if any(t.partition != "train" for t in trips):
        raise ValueError("Non-training trip refused")
    # Compute mechanical/equipment features once per rolling candidate. Propulsion
    # efficiency then scales only positive traction work; recovery stays independent.
    features: dict[float, list[tuple[float, float, float]]] = {}
    for r in rolling:
        rows = []
        for trip in trips:
            estimate = estimate_energy(
                replace(base, rolling_resistance_coefficient=r, propulsion_efficiency=1),
                trip.motion(),
                trip.points[0].soc,
                step_s=step_s,
            )
            auxiliary = trip.auxiliary_power_w * estimate.planned_duration_s / 3_600_000
            traction = estimate.requested_draw_j / 3_600_000 - auxiliary
            recovered = (
                estimate.potential_regeneration_j - estimate.regeneration_power_rejected_j
            ) / 3_600_000
            if estimate.regeneration_capacity_rejected_j > 1e-6:
                raise ValueError("Training feature reduction requires no full-pack clipping")
            rows.append((traction, auxiliary, recovered))
        features[r] = rows
    errors = {}
    for r, e in itertools.product(rolling, efficiencies):
        errors[(r, e)] = tuple(
            (traction / e + auxiliary - recovered - trip.observed_net_kwh) ** 2
            for (traction, auxiliary, recovered), trip in zip(features[r], trips, strict=True)
        )
    scores = tuple(sorted((math.fsum(v) / len(trips), r, e) for (r, e), v in errors.items()))
    best, r, e = scores[0]
    selected = replace(base, rolling_resistance_coefficient=r, propulsion_efficiency=e)
    for trip in trips:
        estimate = estimate_energy(selected, trip.motion(), trip.points[0].soc, step_s=step_s)
        predicted = (
            features[r][trips.index(trip)][0] / e
            + features[r][trips.index(trip)][1]
            - features[r][trips.index(trip)][2]
        )
        if (
            not estimate.energy_plan_feasible
            or estimate.regeneration_capacity_rejected_j > 1e-6
            or not math.isclose(
                estimate.initial_energy_kwh - estimate.final_energy_kwh,
                predicted,
                abs_tol=1e-8,
                rel_tol=1e-10,
            )
        ):
            raise ValueError("Reduced fit does not match bounded numerical model")
    leave_out = []
    for omitted in range(len(trips)):
        _, lr, le = min(
            (math.fsum(v[:omitted] + v[omitted + 1 :]) / (len(trips) - 1), cr, ce)
            for (cr, ce), v in errors.items()
        )
        leave_out.append((lr, le))
    return GridFit(
        "bounded-energy-grid/1.0.0",
        selected,
        tuple(t.mission_id for t in trips),
        best,
        scores,
        tuple(leave_out),
    )


@dataclass(frozen=True)
class TemplatePoint:
    sim_time_s: float
    distance_m: float
    speed_m_s: float


@dataclass(frozen=True)
class MotionTemplate:
    version: str
    training_mission_id: str
    training_content_sha256: str
    terminal_distance_m: float
    points: tuple[TemplatePoint, ...]

    @property
    def sha256(self) -> str:
        return fingerprint(asdict(self))


def select_template(trips: tuple[TrainingTrip, ...]) -> MotionTemplate:
    if not trips or any(t.partition != "train" for t in trips):
        raise ValueError("Training-only template input required")
    duration = statistics.median(t.points[-1].sim_time_s - t.points[0].sim_time_s for t in trips)
    chosen = min(
        trips,
        key=lambda t: (
            abs(t.points[-1].sim_time_s - t.points[0].sim_time_s - duration),
            t.mission_id,
        ),
    )
    points = tuple(TemplatePoint(p.sim_time_s, p.distance_m, p.speed_m_s) for p in chosen.points)
    return MotionTemplate(
        "historical-motion-template/1.0.0",
        chosen.mission_id,
        fingerprint([asdict(p) for p in points]),
        statistics.median(t.points[-1].distance_m for t in trips),
        points,
    )


def assumed_plan(
    template: MotionTemplate,
    *,
    current_distance_m: float,
    current_speed_m_s: float,
    remaining_distance_m: float,
    auxiliary_power_w: float,
) -> tuple[DrivingInterval, ...]:
    for name, value in (
        ("distance", current_distance_m),
        ("speed", current_speed_m_s),
        ("remaining", remaining_distance_m),
        ("auxiliary", auxiliary_power_w),
    ):
        if number(value, name) < 0:
            raise ValueError("Nonnegative forecast assumptions required")
    if remaining_distance_m == 0:
        return ()
    index = max(
        (i for i, p in enumerate(template.points) if p.distance_m <= current_distance_m), default=0
    )
    tail = template.points[index:]
    if len(tail) < 2:
        raise ValueError("Historical template has no remaining motion")
    # Bound the assumed representation, retaining endpoints. This is explicitly a
    # coarsened historical plan, not interpolation/replacement of source observations.
    stride = max(1, math.ceil((len(tail) - 1) / 900))
    selected = tail[::stride]
    if selected[-1] != tail[-1]:
        selected = (*selected, tail[-1])
    base = []
    previous_speed = current_speed_m_s
    for a, b in zip(selected, selected[1:]):
        base.append(
            DrivingInterval(
                b.sim_time_s - a.sim_time_s, previous_speed, b.speed_m_s, 0, auxiliary_power_w
            )
        )
        previous_speed = b.speed_m_s
    distance = math.fsum(0.5 * (p.start_speed_m_s + p.end_speed_m_s) * p.duration_s for p in base)
    if distance <= 0:
        raise ValueError("Historical template has no forward distance")
    return tuple(
        replace(p, duration_s=p.duration_s * remaining_distance_m / distance) for p in base
    )


@dataclass(frozen=True)
class SensitivityEnvelope:
    method: str
    minimum_energy_kwh: float
    maximum_energy_kwh: float
    minimum_final_soc: float
    maximum_final_soc: float
    probability_guarantee: bool = False


def sensitivity_envelope(
    parameters: EnergyParameters,
    plan: tuple[DrivingInterval, ...],
    initial_soc: float,
    policy: dict[str, Any],
    *,
    step_s: float = 0.2,
    parameter_pairs: tuple[tuple[float, float], ...] | None = None,
) -> SensitivityEnvelope:
    values = []
    if parameter_pairs is None:
        pairs = tuple(
            (
                max(0, parameters.rolling_resistance_coefficient + r * policy["rolling_absolute"]),
                min(
                    1,
                    max(0.01, parameters.propulsion_efficiency + e * policy["efficiency_absolute"]),
                ),
            )
            for r, e in itertools.product((-1, 1), repeat=2)
        )
    else:
        if (
            not parameter_pairs
            or len(parameter_pairs) > 256
            or any(
                number(r, "rolling") < 0 or not 0 < number(e, "efficiency") <= 1
                for r, e in parameter_pairs
            )
        ):
            raise ValueError("Invalid joint parameter scenarios")
        pairs = parameter_pairs
    for mass, regen, aux, pair in itertools.product((-1, 1), (-1, 1), (-1, 1), pairs):
        p = replace(
            parameters,
            loaded_mass_kg=parameters.loaded_mass_kg * (1 + mass * policy["mass_fraction"]),
            rolling_resistance_coefficient=pair[0],
            propulsion_efficiency=pair[1],
            regeneration_efficiency=min(
                1,
                max(
                    0, parameters.regeneration_efficiency + regen * policy["regeneration_absolute"]
                ),
            ),
        )
        varied = tuple(
            replace(
                x,
                auxiliary_power_w=max(
                    0, x.auxiliary_power_w + aux * policy["auxiliary_absolute_w"]
                ),
            )
            for x in plan
        )
        estimate = estimate_energy(p, varied, 0.5, step_s=step_s)
        if not estimate.energy_plan_feasible or estimate.regeneration_capacity_rejected_j > 1e-6:
            raise ValueError("Envelope work is outside unclipped domain")
        values.append(estimate.initial_energy_kwh - estimate.final_energy_kwh)
    # Explicit extra stop-time equipment uncertainty; not a probabilistic interval.
    allowance = max((p.auxiliary_power_w for p in plan), default=0) + policy["auxiliary_absolute_w"]
    allowance *= policy["extra_stop_time_s"] / 3_600_000
    low = max(0, min(values) - allowance)
    high = max(values) + allowance
    c = parameters.declared_capacity_kwh
    lower = max(
        0, initial_soc - policy["soc_absolute"] - high / (c * (1 - policy["capacity_fraction"]))
    )
    upper = min(
        1, initial_soc + policy["soc_absolute"] - low / (c * (1 + policy["capacity_fraction"]))
    )
    method = (
        "declared-parameter-envelope/1.0.0"
        if parameter_pairs is None
        else "training-joint-grid-envelope/1.0.0"
    )
    return SensitivityEnvelope(method, low, high, lower, upper)
