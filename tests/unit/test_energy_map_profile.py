import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[2] / "scripts/geographic/road_profile.py"
spec = importlib.util.spec_from_file_location("road_profile", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProfileTests(unittest.TestCase):
    def test_bridge_endpoint_canopy_uses_stable_measured_abutment_only(self):
        deck = [(d, 0, 9 + (4 if d == 40 else 0)) for d in range(0, 41, 5)]
        ground = [(40 + d, 0, 8.8 - 0.02 * d) for d in range(0, 41, 5)]
        fixed = module.bridge_portal(deck, ground, surface_end=-1, approach_end=0)
        self.assertEqual(fixed[-1][2], 8.8)
        self.assertEqual(fixed[:7], deck[:7])
        depressed = [(p[0], p[1], p[2] - (3 if i == 0 else 0)) for i, p in enumerate(ground)]
        self.assertIsNone(module.bridge_portal(deck, depressed, surface_end=-1, approach_end=0))

    def test_ground_approach_joins_measured_bridge_surface_without_canopy(self):
        ground = [(i * 10, 0, 60 + i * 0.1 - (3 if i == 4 else 0)) for i in range(5)]
        surface = [(i * 10, 0, 60 + i * 0.1 + (10 if i < 3 else 0)) for i in range(5)]
        profile, audit = module.fuse_road_layers(ground, surface, surface_at_end=True)
        self.assertEqual(profile[0][2], 60)
        self.assertAlmostEqual(profile[-1][2], 60.4)
        self.assertLess(max(p[2] for p in profile), 61)
        self.assertTrue(audit["surface_at_end"])

    def test_ground_and_surface_fusion_retains_hill_without_canopy_or_underpass(self):
        ground = [(i * 10, 0, 30 + i * 0.5 - (7 if 4 <= i <= 6 else 0)) for i in range(11)]
        surface = [(i * 10, 0, 30 + i * 0.5 + (12 if 1 <= i <= 3 else 0)) for i in range(11)]
        profile, audit = module.fuse_road_layers(ground, surface)
        self.assertEqual(profile[0][2], 30)
        self.assertEqual(profile[-1][2], 35)
        self.assertGreater(profile[5][2], 32)
        self.assertLess(profile[2][2], 32)
        self.assertIn("dsm", audit["layers"])

    def test_bridge_end_uses_measured_portals_and_rejects_long_unknown_links(self):
        line = [(0, 0, 2), (10, 0, 99), (20, 0, 3)]
        self.assertEqual(
            module.junction_transition(line, 20, 21), [(0, 0, 20), (10, 0, 20.5), (20, 0, 21)]
        )
        with self.assertRaises(ValueError):
            module.junction_transition([(0, 0, 0), (101, 0, 0)], 20, 21)

    def test_source_selection_does_not_amplify_subpixel_junction_noise(self):
        points = [(0, 0, 26), (1, 0, 26.01), (1.1, 0, 26.1), (4, 0, 26.4)]
        self.assertAlmostEqual(module.selection_grade(points), 0.1)

    def test_source_selection_detects_a_broad_ground_under_bridge_depression(self):
        points = [(0, 0, 60), (10, 0, 60), (20, 0, 52), (40, 0, 52), (60, 0, 60)]
        self.assertGreater(module.selection_grade(points), 0.5)

    def test_grade_fit_retains_measured_constant_uphill_and_downhill(self):
        distance = [i * 2.5 for i in range(101)]
        for grade in (-0.08, 0.08):
            height = [30 + grade * d for d in distance]
            fitted = module.smooth_profile(distance, height)
            for actual, expected in zip(fitted, height):
                self.assertAlmostEqual(actual, expected, places=8)

    def test_grade_fit_retains_a_broad_hill_and_records_no_flat_assumption(self):
        distance = list(range(0, 401, 5))
        height = [20 + 0.1 * min(d, 400 - d) for d in distance]
        fitted = module.smooth_profile(distance, height)
        self.assertGreater(max(fitted) - min(fitted), 19)
        self.assertGreater(fitted[30], fitted[10])
        self.assertGreater(fitted[50], fitted[70])

    def test_isolated_surface_spike_is_audited(self):
        line = [(i, 0, 0.05 * i + (2 if i == 15 else 0)) for i in range(0, 31, 3)]
        fixed, changes = module.remove_spikes(line)
        self.assertEqual(len(changes), 1)
        self.assertAlmostEqual(fixed[5][2], 0.75)
        self.assertEqual(line[5][2], 2.75)

    def test_sustained_hill_and_descent_are_preserved(self):
        line = [(i, 0, 0.12 * min(i, 60 - i)) for i in range(0, 61, 3)]
        fixed, changes = module.remove_spikes(line)
        self.assertEqual(fixed, line)
        self.assertEqual(changes, [])
