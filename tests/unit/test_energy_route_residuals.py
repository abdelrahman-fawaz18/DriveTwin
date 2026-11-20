"""Past predictions are assessed with later facts, never rebuilt with those facts."""

import unittest
from dataclasses import replace

from test_energy_route_wire import request

from evdt.energy.route_aware.contracts import RoadPoint
from evdt.energy.route_aware.residuals import EnergyObservation, compare_prefix, persistent_mismatch


def residual_case():
    job = request()
    road = replace(
        job.route.occurrences[0],
        shape=(RoadPoint(0, 0, 0), RoadPoint(1000, 0, 0)),
        speed_limit_m_s=5,
        stops=(),
        reference_length_m=1000,
    )
    job = replace(
        job,
        route=replace(job.route, occurrences=(road,)),
        vehicle=replace(job.vehicle, drag_coefficient=0, rolling_coefficient=0),
    )
    initial = job.state.soc * job.vehicle.capacity_kwh * 3_600_000
    observations = tuple(
        EnergyObservation(
            job.state.scope,
            f"event-{i}",
            f"{i:064x}",
            job.route.revision,
            job.state.working_s + i,
            100 + i,
            initial - 8000 * i,
            "not_charging",
            "good",
        )
        for i in range(6)
    )
    return job, observations


class ResidualTests(unittest.TestCase):
    def test_equal_elapsed_time_cruise_matches_known_equipment_energy(self):
        job, readings = residual_case()
        result = compare_prefix(job, readings, assessed_real_life_s=106)
        self.assertAlmostEqual(result.predicted_net_j, 40_000)
        self.assertAlmostEqual(result.observed_net_j, 40_000)
        self.assertAlmostEqual(result.signed_error_j, 0)
        changed = readings[:-1] + (
            replace(readings[-1], stored_energy_j=readings[-1].stored_energy_j - 5000),
        )
        error = compare_prefix(job, changed, assessed_real_life_s=106)
        self.assertAlmostEqual(error.signed_error_j, -5000)
        self.assertEqual(error.input_sha256, result.input_sha256)
        self.assertNotEqual(error.observation_sha256, result.observation_sha256)

    def test_mixed_sources_charging_gaps_and_future_observations_rejected(self):
        job, readings = residual_case()
        for changes in (
            {"scope": replace(job.state.scope, source_epoch=2)},
            {"route_revision": "detour"},
            {"quality": "suspect"},
            {"charging_state": "charging"},
            {"received_real_life_s": 107},
            {"working_s": job.state.working_s + 100},
            {"event_id": readings[0].event_id},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compare_prefix(
                    job,
                    readings[:-1] + (replace(readings[-1], **changes),),
                    assessed_real_life_s=106,
                )
        with self.assertRaises(ValueError):
            compare_prefix(job, readings[1:], assessed_real_life_s=106)
        with self.assertRaises(ValueError):
            compare_prefix(
                job,
                (replace(readings[0], stored_energy_j=1), *readings[1:]),
                assessed_real_life_s=106,
            )

    def test_persistent_error_requires_independent_same_sign_intervals(self):
        job, readings = residual_case()
        original = compare_prefix(job, readings, assessed_real_life_s=106)
        intervals = tuple(
            replace(
                original,
                from_working_s=i * 10,
                until_working_s=i * 10 + 5,
                predicted_net_j=original.observed_net_j + 2000,
                signed_error_j=2000,
            )
            for i in range(3)
        )
        self.assertTrue(persistent_mismatch(intervals, threshold_j=1000))
        self.assertFalse(persistent_mismatch(intervals[:2], threshold_j=1000))
        self.assertFalse(
            persistent_mismatch(
                intervals[:-1]
                + (
                    replace(
                        intervals[-1],
                        predicted_net_j=original.observed_net_j - 2000,
                        signed_error_j=-2000,
                    ),
                ),
                threshold_j=1000,
            )
        )
        with self.assertRaises(ValueError):
            persistent_mismatch((original, original, original), threshold_j=1000)
