"""Declared station availability during a local SUMO session."""

from dataclasses import dataclass

from .moving_charge_station import STATUS_VERSION, StationStatus
from .route_aware.validation import choice, digest, number, sequence, text
from .route_aware.wire import fingerprint


@dataclass(frozen=True)
class BayInterval:
    from_working_s: float
    until_working_s: float
    state: str
    power_w: float | None

    def __post_init__(self) -> None:
        number(self.from_working_s, "bay interval start", 0, 1e12)
        number(self.until_working_s, "bay interval end", self.from_working_s, 1e12)
        if self.until_working_s == self.from_working_s:
            raise ValueError("Bay interval must have positive duration")
        choice(
            self.state,
            "bay interval state",
            ("available", "occupied", "reserved", "out_of_order", "unknown"),
        )
        if self.state == "available":
            number(self.power_w, "declared bay power", 1, 1_000_000)
        elif self.power_w is not None:
            raise ValueError("Closed or unknown bay supplies no power")


@dataclass(frozen=True)
class BaySchedule:
    bay_id: str
    intervals: tuple[BayInterval, ...]

    def __post_init__(self) -> None:
        text(self.bay_id, "scheduled bay")
        sequence(self.intervals, "bay intervals", BayInterval, 1, 128)
        if any(
            a.until_working_s > b.from_working_s for a, b in zip(self.intervals, self.intervals[1:])
        ):
            raise ValueError("Bay intervals must be ordered and nonoverlapping")


@dataclass(frozen=True)
class StationScenario:
    scenario_id: str
    run_id: str
    schedules: tuple[BaySchedule, ...]
    version: str = "moving-charge-station-scenario/1.0.0"

    def __post_init__(self) -> None:
        text(self.scenario_id, "station scenario")
        text(self.run_id, "station scenario run")
        choice(self.version, "station scenario version", ("moving-charge-station-scenario/1.0.0",))
        sequence(self.schedules, "station schedules", BaySchedule, 1, 32)
        if len({row.bay_id for row in self.schedules}) != len(self.schedules):
            raise ValueError("Each bay must have one schedule")

    @property
    def sha256(self) -> str:
        return fingerprint(self)

    def active_interval(self, bay_id: str, working_s: float) -> BayInterval | None:
        """Select the declared plant window for one bounded local SUMO bay."""
        return next(
            (
                interval
                for schedule in self.schedules
                if schedule.bay_id == bay_id
                for interval in schedule.intervals
                if interval.from_working_s <= working_s < interval.until_working_s
            ),
            None,
        )

    def observe(
        self,
        *,
        run_id: str,
        working_s: float,
        observed_real_life_s: float,
        received_real_life_s: float,
        expected_sha256: str,
    ) -> tuple[StationStatus, ...]:
        if run_id != self.run_id:
            raise ValueError("Station scenario belongs to a different run")
        if digest(expected_sha256, "assigned station scenario") != self.sha256:
            raise ValueError("Station scenario differs from the reviewed assignment")
        number(working_s, "station scenario working time", 0, 1e12)
        return tuple(
            self._bay_status(schedule, working_s, observed_real_life_s, received_real_life_s)
            for schedule in self.schedules
        )

    def _bay_status(self, schedule, working_s, observed, received):
        # Each transition, including a gap, advances the schedule revision.
        revision = 1
        state = "unknown"
        available = power = None
        end = working_s
        for index, entry in enumerate(schedule.intervals):
            if entry.from_working_s <= working_s < entry.until_working_s:
                revision, state = 2 * index + 2, entry.state
                end = entry.until_working_s
                if entry.state == "available":
                    available, power = working_s, entry.power_w
                elif entry.state == "occupied":
                    successor = schedule.intervals[index + 1 : index + 2]
                    if (
                        successor
                        and successor[0].from_working_s == entry.until_working_s
                        and successor[0].state == "available"
                    ):
                        opening = successor[0]
                        available, power = opening.from_working_s, opening.power_w
                        end = opening.until_working_s
                break
            if working_s >= entry.until_working_s:
                revision = 2 * index + 3
        return StationStatus(
            STATUS_VERSION,
            schedule.bay_id,
            self.scenario_id,
            revision,
            observed,
            received,
            working_s,
            state,
            end,
            available,
            power,
        )
