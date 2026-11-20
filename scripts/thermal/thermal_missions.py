"""Run matched closed-loop thermal missions with separate sensor and truth channels."""

import sqlite3
import time
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
from thermal_advisory import Reading, Sample
from thermal_intervention import Interlocks, MissionBoundary
from thermal_network import HeatNetwork

from evdt.command_timing import HostClock
from evdt.contract_profile import Rejected

TRACE_COLUMNS = ("sim_s", "motor_k", "housing_k", "base_w", "pump_w", "derated", "mission_work")
ARMS = ("local_only", "threshold", "predictive")
PROCESS_EPOCH = str(uuid4())


@dataclass(frozen=True)
class Scenario:
    kind: str
    seed: int


def scenarios(policy, partition):
    base = policy["seeds"][partition]
    return [
        Scenario(kind, base + 100 * i + j)
        for i, (kind, count) in enumerate(policy["cases_per_partition"].items())
        for j in range(count)
    ]


def runtime_clock():
    return HostClock(PROCESS_EPOCH, time.monotonic_ns(), datetime.now(timezone.utc))


class TrendForecast:
    """Estimate a short linear rise using consecutive, already observed motor samples."""

    def __init__(self, settings):
        self.settings = settings
        lookback = settings["lookback_s"]
        self.recent = deque(maxlen=lookback + 1)
        offsets = np.arange(lookback + 1) - lookback / 2
        self.weights = offsets / (offsets @ offsets)

    def update(self, sample):
        reading = sample.motor
        if reading is None or reading.observed_s != sample.time_s:
            self.recent.clear()
            return None
        self.recent.append(reading.temperature_k)
        if len(self.recent) != self.recent.maxlen:
            return None
        slope = np.clip(self.weights @ self.recent, 0, self.settings["maximum_rise_k_per_s"])
        return float(reading.temperature_k + slope * self.settings["horizon_s"])


class Plant:
    """Synthetic plant. The controller sees noisy readings and a heat-input estimate."""

    def __init__(self, scenario, policy):
        rng = np.random.default_rng(scenario.seed)
        ticks = np.arange(policy["mission_deadline_s"])
        phase = rng.uniform(0, 2 * np.pi)
        self.ambient = rng.uniform(291.5, 297.5) + 1.6 * np.sin(ticks / 440 + phase)
        load = (np.sin(ticks / 85 + phase) + 1) / 2
        pulse = ((ticks + rng.integers(0, 85)) % 175 < 100).astype(float)
        heat = np.column_stack((3900 + 1850 * load + 1400 * pulse, 600 + 500 * load))
        self.traction_w = 46000 + 40000 * load
        self.noise = rng.normal(0, [0.16, 0.19], heat.shape)
        self.onset_s = int(rng.integers(465, 570))
        self.bad_since_s = self.onset_s + (32 if scenario.kind == "cooling_bias" else 45)
        capacity = tuple(np.array([36000, 64000]) * rng.uniform(0.96, 1.04, 2))
        coupling = 250 * rng.uniform(0.96, 1.04)
        body = 180 * rng.uniform(0.95, 1.05)
        fault_body = body * rng.uniform(0.18, 0.32)
        self.temperature = self.ambient[0] + np.array([38.0, 24.0])
        self.expected = self.temperature + self.noise[0]
        self.stale_motor = None
        self.kind = scenario.kind
        self.models = {}
        aux = policy["boost"]["housing_conductance_w_per_k"]
        derate = policy["protection"]["heat_factor"]
        coolant = self.ambient
        actual_heat = heat * rng.uniform(0.96, 1.04, 2)
        for degraded, pumped in ((False, False), (True, False), (True, True), (False, True)):
            extra = aux if pumped else 0
            network = HeatNetwork(
                capacity, (15, (fault_body if degraded else body) + extra), (0, 0), coupling
            ).discrete(1)
            for throttled in (False, True):
                factor = derate if throttled else 1
                self.models[degraded, pumped, throttled] = (
                    network.transition,
                    network.increments(self.ambient, coolant, actual_heat * factor)
                    @ network.from_modes,
                )
        self.reference = {}
        for pumped in (False, True):
            extra = aux if pumped else 0
            network = HeatNetwork((36000, 64000), (15, 180 + extra), (0, 0), 250).discrete(1)
            for throttled in (False, True):
                factor = derate if throttled else 1
                self.reference[pumped, throttled] = (
                    network.transition,
                    network.increments(self.ambient, coolant, heat * factor) @ network.from_modes,
                )

    def readings(self, tick):
        motor, housing = self.temperature + self.noise[tick]
        if self.kind == "slow_bias":
            motor += np.clip((tick - self.onset_s) / 135, 0, 1) * 7.5
        if (self.kind == "step_bias" and tick >= self.onset_s) or (
            self.kind == "cooling_bias" and tick >= self.bad_since_s
        ):
            motor += 6.5
        motor_probe = Reading(float(motor), tick)
        if self.kind == "cooling_dropout" and tick >= self.bad_since_s:
            motor_probe = None
        if self.kind == "cooling_stale":
            if tick < self.bad_since_s:
                self.stale_motor = motor_probe
            else:
                motor_probe = self.stale_motor
        housing_probe = None if self.kind == "single_probe" else Reading(float(housing), tick)
        return Sample(tick, motor_probe, housing_probe, *map(float, self.expected))

    def advance(self, tick, pumped, derated):
        degraded = tick >= self.onset_s and (
            self.kind.startswith("cooling_") or self.kind == "single_probe"
        )
        transition, increments = self.models[degraded, pumped, derated]
        self.temperature = transition @ self.temperature + increments[tick]
        reference, increments = self.reference[pumped, derated]
        self.expected = reference @ self.expected + increments[tick]


def simulate(scenario, arm, policy, advisory_rules, warmup_s, *, forecast_qualified):
    if arm not in ARMS or (arm == "predictive" and not forecast_qualified):
        raise ValueError("Controller arm has no passing forecast gate")
    plant = Plant(scenario, policy)
    forecast = TrendForecast(policy["forecast"])
    limits = policy["protection"]
    boost = policy["boost"]
    trace, predictions = [], []
    derated = False
    progress = 0.0
    reservation = granted_s = command_error = None
    scope = (f"{arm}-{scenario.seed}", "synthetic-bus")
    with closing(sqlite3.connect(":memory:")) as journal:
        boundary = MissionBoundary(scope, journal, advisory_rules, warmup_s, boost["duration_s"])
        for tick in range(policy["mission_deadline_s"]):
            motor, housing = plant.temperature
            derated = motor > limits["release_k"] if derated else motor >= limits["engage_k"]
            interlocks = Interlocks(local_protection=derated)
            sample = plant.readings(tick)
            now = runtime_clock()
            advice = boundary.observe(sample, now)
            predicted = forecast.update(sample)
            eligible = not derated and advice.state == "cooling_consistent"
            can_forecast = (
                eligible
                and predicted is not None
                and sample.motor.temperature_k >= policy["forecast"]["minimum_anchor_k"]
            )
            if arm == "local_only" and can_forecast:
                predictions.append((tick, predicted))
            if reservation is not None:
                try:
                    boundary.grant(reservation, tick, runtime_clock(), interlocks)
                except Rejected as error:
                    command_error = str(error)
                else:
                    granted_s = tick
                reservation = None
            elif granted_s is None and command_error is None:
                trigger = (
                    arm == "threshold"
                    and eligible
                    and sample.motor.temperature_k >= policy["threshold_k"]
                ) or (arm == "predictive" and can_forecast and predicted >= policy["threshold_k"])
                if trigger:
                    try:
                        reservation = boundary.reserve(scope, tick, runtime_clock(), interlocks)
                    except Rejected as error:
                        command_error = str(error)
            pumped = boundary.pump_on(tick, runtime_clock(), interlocks)
            pace = limits["pace"] if derated else 1.0
            progress += pace
            trace.append(
                (
                    tick,
                    motor,
                    housing,
                    plant.traction_w[tick] * pace + 4500,
                    boost["aux_power_w"] if pumped else 0,
                    derated,
                    progress,
                )
            )
            plant.advance(tick, pumped, derated)
            if progress >= policy["mission_work_s"]:
                break
    if reservation is not None:
        command_error = "mission_ended_before_handoff"
    return np.asarray(trace), predictions, granted_s, command_error
