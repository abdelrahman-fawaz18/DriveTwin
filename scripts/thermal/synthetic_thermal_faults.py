"""Generate separate observed and hidden traces for synthetic thermal fault cases."""

from dataclasses import dataclass

import numpy as np
from thermal_advisory import Reading, Sample
from thermal_network import HeatNetwork


@dataclass(frozen=True)
class Scenario:
    kind: str
    seed: int
    variant: int


@dataclass(frozen=True)
class Truth:
    onset_s: int | None
    cooling_started_s: int | None
    bias_started_s: int | None
    unavailable: tuple[int, int] | None
    hot_limit_s: int | None
    matched_healthy_limit_s: int | None
    severity: float | None


def scenarios(policy, partition):
    base = policy["seeds"][partition]
    return [
        Scenario(kind, base + 100 * i + j, j)
        for i, (kind, count) in enumerate(policy["cases_per_partition"].items())
        for j in range(count)
    ]


def first_crossing(curve, limit):
    matches = np.flatnonzero(curve[:, 0] >= limit)
    return int(matches[0]) if len(matches) else None


def generate(scenario, policy):
    rng = np.random.default_rng(scenario.seed)
    kind = scenario.kind
    duration = policy["duration_s"]
    time = np.arange(duration + 1)
    phase = rng.uniform(0, 2 * np.pi)
    ambient = rng.uniform(291.0, 298.0) + 1.7 * np.sin(time / 455 + phase)
    load = (np.sin(time / 80 + phase) + 1.0) / 2.0
    pulse = ((time + rng.integers(0, 90)) % 180 < 100).astype(float)
    heat = np.column_stack(
        (3800.0 + 1850.0 * load + 1400.0 * pulse, 580.0 + 510.0 * load)
    ) * rng.uniform(0.94, 1.06)
    capacity = tuple(np.array([36000.0, 64000.0]) * rng.uniform(0.96, 1.04, 2))
    coupling = 250.0 * rng.uniform(0.96, 1.04)
    body_cooling = 180.0 * rng.uniform(0.95, 1.05)
    heat_actual = heat * rng.uniform(0.96, 1.04, 2)
    initial = np.array([ambient[0] + 38.0, ambient[0] + 24.0]) + rng.uniform(-0.8, 0.8, 2)
    baseline = HeatNetwork(capacity, (15.0, body_cooling), (0.0, 0.0), coupling).discrete(1.0)

    def advance(model, start, end, state):
        return model.rollout(state, ambient[start:end], ambient[start:end], heat_actual[start:end])

    healthy = np.vstack((initial, advance(baseline, 0, duration, initial)))
    onset = None if kind == "healthy" else int(rng.integers(540, 650))
    cooling = (
        onset
        if kind in {"cooling_loss", "cooling_dropout", "cooling_bias", "single_probe_cooling"}
        else None
    )
    bias = (
        onset
        if kind in {"step_bias", "slow_bias"}
        else (onset + 32 if kind == "cooling_bias" else None)
    )
    unavailable_start = (
        onset
        if kind in {"dropout", "stale"}
        else (onset + 38 if kind == "cooling_dropout" else None)
    )
    unavailable = (
        None
        if unavailable_start is None
        else (unavailable_start, unavailable_start + 118 + scenario.variant * 4)
    )
    severity = None
    latent = healthy
    if cooling is not None:
        severity = float(rng.uniform(0.18, 0.34))  # Remaining housing-to-sink conductance.
        degraded = HeatNetwork(
            capacity, (15.0, body_cooling * severity), (0.0, 0.0), coupling
        ).discrete(1.0)
        latent = np.vstack(
            (healthy[: cooling + 1], advance(degraded, cooling, duration, healthy[cooling]))
        )

    measured = latent + rng.normal(0, [0.16, 0.19], latent.shape)
    if bias is not None:
        if kind == "slow_bias":
            measured[bias:, 0] += np.minimum(np.arange(duration + 1 - bias) / 135, 1.0) * 7.5
        else:
            measured[bias:, 0] += 6.5 * (-1 if scenario.variant % 2 else 1)

    nominal = HeatNetwork((36000.0, 64000.0), (15.0, 180.0), (0.0, 0.0), 250.0).discrete(1.0)
    housing_present = kind != "single_probe_cooling"
    startup = [
        float(measured[0, 0]),
        float(measured[0, 1]) if housing_present else ambient[0] + 24.0,
    ]
    expected = np.vstack((startup, nominal.rollout(startup, ambient[:-1], ambient[:-1], heat[:-1])))
    observations = []
    for tick in range(duration + 1):
        motor = Reading(float(measured[tick, 0]), tick)
        if unavailable is not None and unavailable[0] <= tick < unavailable[1]:
            if kind == "stale":
                last = unavailable[0] - 1
                motor = Reading(float(measured[last, 0]), last)
            else:
                motor = None
        housing = Reading(float(measured[tick, 1]), tick) if housing_present else None
        observations.append(
            Sample(tick, motor, housing, float(expected[tick, 0]), float(expected[tick, 1]))
        )
    return observations, Truth(
        onset,
        cooling,
        bias,
        unavailable,
        first_crossing(latent, policy["reference_hot_limit_k"]),
        first_crossing(healthy, policy["reference_hot_limit_k"]),
        severity,
    )
