"""Causal, evidence-gated advisory states for the synthetic thermal fixture."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    temperature_k: float
    observed_s: int


@dataclass(frozen=True)
class Sample:
    time_s: int
    motor: Reading | None
    housing: Reading | None
    expected_motor_k: float
    expected_housing_k: float


@dataclass(frozen=True)
class Advice:
    state: str
    reason: str
    motor_residual_k: float | None = None
    housing_residual_k: float | None = None


def invalid_reason(reading, now, maximum_age, last_seen):
    if reading is None:
        return "missing"
    if not math.isfinite(reading.temperature_k) or reading.temperature_k <= 0:
        return "invalid"
    age = now - reading.observed_s
    if age < 0:
        return "future"
    if age > maximum_age:
        return "stale"
    if last_seen is not None and reading.observed_s <= last_seen:
        return "repeated_or_out_of_order"
    return None


class ThermalAdvisory:
    def __init__(self, rules, warmup_s):
        self.rules = rules
        self.warmup_s = warmup_s
        self.last_tick = None
        self.last_motor_stamp = None
        self.last_housing_stamp = None
        self.previous_motor = None
        self.sensor_suspect = False
        self.reset_persistence()

    def reset_persistence(self):
        self.hot_since = None
        self.support_since = None
        self.clear_since = None
        self.active = False

    def step(self, sample):
        rules, now = self.rules, sample.time_s
        if (
            not isinstance(now, int)
            or now < 0
            or (self.last_tick is not None and now <= self.last_tick)
        ):
            raise ValueError("Expected an increasing integer scenario clock")
        if self.last_tick is not None and now - self.last_tick != 1:
            self.previous_motor = None
            self.reset_persistence()
        self.last_tick = now
        motor_error = invalid_reason(
            sample.motor, now, rules["maximum_age_s"], self.last_motor_stamp
        )
        housing_error = invalid_reason(
            sample.housing, now, rules["maximum_age_s"], self.last_housing_stamp
        )
        housing = sample.housing if housing_error is None else None
        if housing is not None:
            if (
                self.last_housing_stamp is not None
                and housing.observed_s - self.last_housing_stamp != 1
            ):
                self.support_since = None
            self.last_housing_stamp = housing.observed_s
        if motor_error is not None:
            self.previous_motor = None
            self.reset_persistence()
            return Advice("data_unavailable", motor_error)
        motor = sample.motor
        if self.last_motor_stamp is not None and motor.observed_s - self.last_motor_stamp != 1:
            self.previous_motor = None
            self.reset_persistence()
        self.last_motor_stamp = motor.observed_s
        if (
            self.previous_motor is not None
            and abs(motor.temperature_k - self.previous_motor) >= rules["sensor_step_k"]
        ):
            self.sensor_suspect = True
        self.previous_motor = motor.temperature_k
        if self.sensor_suspect:
            return Advice("sensor_suspect", "temperature_step_latched_for_session")
        if not all(
            math.isfinite(x) and x > 0 for x in (sample.expected_motor_k, sample.expected_housing_k)
        ):
            self.reset_persistence()
            return Advice("data_unavailable", "prediction_unavailable")
        if now < self.warmup_s:
            return Advice("warming_up", "thermal_reference_initialization")

        residual = motor.temperature_k - sample.expected_motor_k
        case = (
            housing.temperature_k - sample.expected_housing_k
            if housing is not None and housing.observed_s == motor.observed_s
            else None
        )
        high = residual >= rules["hot_residual_k"]
        supporting = (
            high
            and case is not None
            and case >= rules["case_residual_k"]
            and residual - case <= rules["max_positive_hot_case_disagreement_k"]
        )
        self.hot_since = self.timer(self.hot_since, high, now)
        self.support_since = self.timer(self.support_since, supporting, now)
        self.clear_since = self.timer(self.clear_since, residual <= rules["clear_residual_k"], now)
        if self.elapsed(self.clear_since, now, rules["clear_persistence_s"]):
            self.active = False
        if self.elapsed(self.hot_since, now, rules["persistence_s"]):
            self.active = True
        if not self.active:
            return Advice("normal", "no_persistent_deviation", residual, case)
        if self.elapsed(self.support_since, now, rules["persistence_s"]):
            return Advice("cooling_consistent", "two_temperature_deviations", residual, case)
        reason = (
            "housing_unavailable_or_unsynchronized"
            if case is None
            else "deviation_without_corroboration"
        )
        return Advice("ambiguous", reason, residual, case)

    @staticmethod
    def timer(start, predicate, now):
        return (now if start is None else start) if predicate else None

    @staticmethod
    def elapsed(start, now, seconds):
        return start is not None and now - start >= seconds
