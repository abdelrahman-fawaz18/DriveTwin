"""Versioned ceilings and initial refresh policy for the new forecasting domain."""

from dataclasses import dataclass, field

from .validation import integer, number


@dataclass(frozen=True)
class ForecastPolicy:
    period_working_s: float = 30
    condition_persistence_working_s: float = 5
    condition_cooldown_working_s: float = 10
    condition_energy_kwh: float = 0.25
    condition_energy_fraction: float = 0.02
    condition_soc_fraction: float = 0.005
    hysteresis_reset_fraction: float = 0.5
    near_horizon_working_s: float = 120
    source_max_age_real_life_s: float = 5
    forecast_max_age_working_s: float = 60
    forecast_max_age_real_life_s: float = 90
    worker_deadline_real_life_s: float = 30
    lease_real_life_s: float = 45
    integration_step_working_s: float = 0.2
    max_plan_working_s: float = 14_400
    max_integration_steps: int = 100_000
    max_segments: int = 10_000
    max_request_bytes: int = 4_194_304
    sumo_euler_step_working_s: float | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )
    sumo_braking_source_distance: bool | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )
    power_limited_motion: bool | None = field(default=None, metadata={"omit_none_on_wire": True})
    road_limits_include_turns: bool | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )
    fixed_signal_timing: bool | None = field(default=None, metadata={"omit_none_on_wire": True})
    signal_deceleration_m_s2: float | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )
    sumo_yellow_clearance: bool | None = field(default=None, metadata={"omit_none_on_wire": True})
    max_observation_lag_working_s: float | None = field(
        default=None, metadata={"omit_none_on_wire": True}
    )

    @property
    def observation_lag_limit_working_s(self) -> float:
        return (
            self.period_working_s
            if self.max_observation_lag_working_s is None
            else self.max_observation_lag_working_s
        )

    def __post_init__(self) -> None:
        if self.sumo_braking_source_distance is not None and (
            type(self.sumo_braking_source_distance) is not bool
            or (self.sumo_braking_source_distance and self.sumo_euler_step_working_s is None)
        ):
            raise ValueError("Source-distance braking requires the SUMO position step")
        if self.max_observation_lag_working_s is not None:
            number(self.max_observation_lag_working_s, "observation lag limit", 0.01, 600)
        if self.signal_deceleration_m_s2 is not None:
            number(self.signal_deceleration_m_s2, "declared signal braking", 0.1, 4.5)
            if not self.fixed_signal_timing:
                raise ValueError("Signal braking requires the fixed-signal profile")
        if self.fixed_signal_timing is not None and type(self.fixed_signal_timing) is not bool:
            raise ValueError("Fixed signal timing must be a boolean")
        if self.sumo_yellow_clearance is not None and (
            type(self.sumo_yellow_clearance) is not bool
            or (self.sumo_yellow_clearance and not self.fixed_signal_timing)
        ):
            raise ValueError("SUMO yellow clearance requires fixed signal timing")
        if self.power_limited_motion is not None and type(self.power_limited_motion) is not bool:
            raise ValueError("Power-limited movement must be a boolean")
        if (
            self.road_limits_include_turns is not None
            and type(self.road_limits_include_turns) is not bool
        ):
            raise ValueError("Turn-limit provenance must be a boolean")
        for name in (
            "period_working_s",
            "condition_persistence_working_s",
            "condition_cooldown_working_s",
            "near_horizon_working_s",
            "source_max_age_real_life_s",
            "forecast_max_age_working_s",
            "forecast_max_age_real_life_s",
            "worker_deadline_real_life_s",
            "lease_real_life_s",
        ):
            number(getattr(self, name), name, 0.01, 600)
        number(self.condition_energy_kwh, "condition energy", 0.001, 100)
        number(self.condition_energy_fraction, "condition energy fraction", 0.001, 1)
        number(self.condition_soc_fraction, "condition charge fraction", 0.0001, 1)
        number(self.hysteresis_reset_fraction, "hysteresis reset fraction", 0.01, 0.99)
        number(self.integration_step_working_s, "integration step", 0.01, 0.2)
        if self.sumo_euler_step_working_s is not None:
            number(self.sumo_euler_step_working_s, "SUMO position step", 0.05, 1)
        number(self.max_plan_working_s, "plan duration", 0.01, 14_400)
        integer(self.max_integration_steps, "step limit", 1, 100_000)
        integer(self.max_segments, "segment limit", 1, 10_000)
        integer(self.max_request_bytes, "request byte limit", 1, 4_194_304)
        if self.worker_deadline_real_life_s > 30:
            raise ValueError("Worker deadline exceeds the reviewed ceiling")
        if self.lease_real_life_s <= self.worker_deadline_real_life_s:
            raise ValueError("The scheduling lease must outlast the worker deadline")
        if self.forecast_max_age_working_s < self.period_working_s:
            raise ValueError("Forecast lifetime must cover its normal refresh period")
