"""A saved prediction is compared only with a complete later observed window."""

import unittest
from dataclasses import replace

from test_energy_route_residuals import residual_case

from evdt.energy.route_aware.comparisons import Comparisons
from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.residuals import assess_saved_window, compare_prefix


class ComparisonTests(unittest.TestCase):
    def case(self):
        request, readings = residual_case()
        request = replace(
            request, conditions=(replace(request.conditions[0], auxiliary_power_w=8000),)
        )
        initial = readings[0]
        observations = tuple(
            replace(
                initial,
                event_id=f"event-{i}",
                wire_sha256=f"{i:064x}",
                working_s=initial.working_s + i,
                received_real_life_s=100 + i,
                stored_energy_j=initial.stored_energy_j - 8000 * i,
            )
            for i in range(31)
        )
        return request, forecast(request), observations

    def test_frozen_window_matches_independently_known_cruise_demand(self):
        request, result, observations = self.case()
        residual = assess_saved_window(request, result, observations, assessed_real_life_s=131)
        self.assertAlmostEqual(residual.predicted_net_j, 30 * 8000)
        self.assertAlmostEqual(residual.signed_error_j, 0)
        recalculated = compare_prefix(request, observations, assessed_real_life_s=131)
        self.assertEqual(residual, recalculated)

    def test_missing_seconds_charging_foreign_and_future_inputs_rejected(self):
        request, result, observations = self.case()
        for selected in (
            observations[1:],
            observations[:-1],
            observations[:8] + observations[9:],
            observations[:-1] + (replace(observations[-1], charging_state="charging"),),
            observations[:-1] + (replace(observations[-1], route_revision="different"),),
            observations[:-1] + (replace(observations[-1], received_real_life_s=132),),
        ):
            with self.subTest(selected=selected[-1].event_id), self.assertRaises(ValueError):
                assess_saved_window(request, result, selected, assessed_real_life_s=131)

    def test_tracker_waits_then_produces_once_and_discards_route_changes(self):
        request, result, observations = self.case()
        tracker = Comparisons()
        tracker.add(1, request, result)
        for observation in observations[:-1]:
            tracker.observe(observation)
        self.assertEqual(tracker.assess(130), [])
        tracker.observe(observations[-1])
        tracker.observe(observations[-1])
        self.assertEqual(len(tracker.assess(131)), 1)
        self.assertEqual(tracker.assess(132), [])
        tracker.add(2, request, result)
        tracker.observe(replace(observations[-1], event_id="changed", route_revision="detour"))
        self.assertEqual(tracker.assess(132), [])
        self.assertEqual(tracker.skipped, {"source_or_route_changed": 1})
