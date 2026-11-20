"""Compare a temperature-corrected observer with the same open-loop network."""

import numpy as np
from thermal_network import HeatNetwork


def simulate(seed, mismatch):
    rng = np.random.default_rng(seed)
    time = np.arange(2400.0)
    ambient = np.full_like(time, 293.15)
    coolant = 289.15 + 3.0 * np.sin(time / 450.0)
    heat = np.column_stack(
        (
            35.0 + 65.0 * np.square(np.sin(time / 170.0)),
            12.0 + 14.0 * np.square(np.sin(time / 410.0)),
        )
    )
    assumed = HeatNetwork((900.0, 3600.0), (0.0, 1.0), (2.0, 8.0), 12.0).discrete(1.0)
    actual = (
        HeatNetwork((900.0, 4000.0), (0.0, 1.0), (2.0, 6.0), 10.0).discrete(1.0)
        if mismatch
        else assumed
    )
    truth = actual.rollout([304.0, 305.0], ambient, coolant, heat * (1.12 if mismatch else 1.0))
    initial = [304.0, 293.15]
    open_loop = assumed.rollout(initial, ambient, coolant, heat)
    readings = truth[:, 0] + rng.normal(0.0, 0.8, len(time))
    prior = []
    state = np.asarray(initial)
    covariance = np.diag([2.0**2, 10.0**2])
    transition = assumed.transition
    process_variance = np.diag([0.055**2, 0.035**2])
    forcing = assumed.increments(ambient, coolant, heat) @ assumed.from_modes
    for reading, increment in zip(readings, forcing, strict=True):
        state = transition @ state + increment
        prior.append(state.copy())  # Score before admitting this sample's temperature.
        covariance = transition @ covariance @ transition.T + process_variance
        gain = covariance[:, 0] / (covariance[0, 0] + 0.8**2)
        state += gain * (reading - state[0])
        jacobian = np.eye(2) - np.outer(gain, [1.0, 0.0])
        covariance = jacobian @ covariance @ jacobian.T + np.outer(gain, gain) * 0.8**2
    return {
        "seed": seed,
        "model_mismatch": mismatch,
        "open_loop_mae_k": np.mean(np.abs(open_loop - truth), axis=0).tolist(),
        "corrected_prior_mae_k": np.mean(np.abs(np.asarray(prior) - truth), axis=0).tolist(),
    }


def observability_study():
    measurement = np.array([1.0, 0.0])  # Measure winding only.
    ranks = {}
    for label, coupling in (("coupled", 12.0), ("disconnected", 0.0)):
        transition = (
            HeatNetwork((900.0, 3600.0), (0.0, 1.0), (2.0, 8.0), coupling).discrete(1.0).transition
        )
        matrix = np.stack((measurement, measurement @ transition))
        ranks[label] = int(np.linalg.matrix_rank(matrix))
    cases = [simulate(seed, mismatch) for seed in (551, 552, 553) for mismatch in (False, True)]
    passed = ranks == {"coupled": 2, "disconnected": 1} and all(
        all(
            corrected < opened
            for corrected, opened in zip(
                case["corrected_prior_mae_k"], case["open_loop_mae_k"], strict=True
            )
        )
        for case in cases
    )
    return {
        "passed": passed,
        "scope": "Synthetic reference networks with winding sensor updates, two states and one input stream",
        "observability_ranks": ranks,
        "columns": ["winding", "effective_body"],
        "measurement_noise_standard_deviation_k": 0.8,
        "cases": cases,
        "reserved_bench_mode": "open_loop_after_startup",
        "physical_hidden_temperatures_qualified": False,
    }
