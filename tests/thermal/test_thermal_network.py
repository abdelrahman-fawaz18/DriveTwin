import math
import unittest

import numpy as np

from scripts.thermal.thermal_network import HeatNetwork


def run(network, step, initial, ambient, coolant, heat, count):
    return network.discrete(step).rollout(
        initial, np.full(count, ambient), np.full(count, coolant), np.tile(heat, (count, 1))
    )


class ThermalNetworkTest(unittest.TestCase):
    def test_step_and_steady_state(self):
        network = HeatNetwork((1000,), (5,), (15,))
        # Equilibrium is (5*300 + 15*280 + 200) / 20 = 295 K.
        curve = run(network, 5, [320], 300, 280, [200], 240)
        self.assertAlmostEqual(curve[0, 0], 295 + 25 * math.exp(-0.1), places=11)
        self.assertAlmostEqual(curve[-1, 0], 295, places=7)
        self.assertTrue(np.all(np.diff(curve[:, 0]) < 0))

    def test_insulated_heat_capacity(self):
        curve = run(HeatNetwork((600,), (0,), (0,)), 2, [300], 280, 290, [30], 30)
        self.assertAlmostEqual(curve[-1, 0], 303, places=11)

    def test_heat_exchange_conserves_energy_in_isolation(self):
        network = HeatNetwork((1000, 3000), (0, 0), (0, 0), 10)
        curve = run(network, 2, [330, 290], 300, 300, [0, 0], 500)
        np.testing.assert_allclose(curve @ np.array([1000, 3000]), 1200000, rtol=1e-12)
        np.testing.assert_allclose(curve[-1], [300, 300], atol=5e-5)

    def test_zero_coupling_separates_nodes(self):
        curve = run(
            HeatNetwork((1000, 1000), (0, 5), (10, 0)), 1, [290, 305], 305, 290, [40, 0], 200
        )
        np.testing.assert_allclose(curve[:, 1], 305, rtol=1e-12)
        self.assertGreater(curve[-1, 0], 290)

    def test_constant_forcing_invariant_to_step_size(self):
        network = HeatNetwork((1000, 2500), (2, 5), (5, 7), 11)
        coarse = run(network, 2, [315, 300], 295, 285, [200, 30], 100)
        fine = run(network, 0.25, [315, 300], 295, 285, [200, 30], 800)
        np.testing.assert_allclose(coarse, fine[7::8], rtol=1e-12)

    def test_column_transition_agrees_with_exact_rollout(self):
        network = HeatNetwork((800, 3000), (1, 3), (5, 9), 12).discrete(1)
        initial, ambient, coolant, heat = [325, 290], np.array([298]), np.array([285]), [[36, 8]]
        expected = network.rollout(initial, ambient, coolant, heat)[0]
        increment = (network.increments(ambient, coolant, np.asarray(heat)) @ network.from_modes)[0]
        np.testing.assert_allclose(network.transition @ initial + increment, expected, rtol=1e-12)

    def test_ramp_forcing_refinement_converges(self):
        network = HeatNetwork((1000,), (10,), (0,))
        # With a linear ambient ramp at 0.05 K/s, solve the scalar equation analytically.
        expected = 290 + 0.05 * (100 - 100 * (1 - math.exp(-1)))
        errors = []
        for dt in (4, 2, 1):
            times = np.arange(0, 100, dt)
            out = network.discrete(dt).rollout(
                [290], 290 + 0.05 * times, 290 + 0 * times, np.zeros((len(times), 1))
            )
            errors.append(abs(float(out[-1, 0]) - expected))
        self.assertLess(errors[1], 0.51 * errors[0])
        self.assertLess(errors[2], 0.51 * errors[1])

    def test_invalid_inputs(self):
        for network, dt in (
            (HeatNetwork((0,), (1,), (1,)), 1),
            (HeatNetwork((1,), (-1,), (1,)), 1),
            (HeatNetwork((1,), (1,), (1,)), 0),
            (HeatNetwork((1,), (1,), (1,)), math.inf),
        ):
            with self.assertRaises(ValueError):
                network.discrete(dt)
        with self.assertRaises(ValueError):
            run(HeatNetwork((1,), (1,), (1,)), 1, [300], 300, 300, [-1], 1)


if __name__ == "__main__":
    unittest.main()
