"""Selected pace preserves sensor ticks and forecast clock boundaries."""

import unittest
from pathlib import Path
from unittest.mock import patch

from test_energy_route_scheduling import snapshot

from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.scheduling import Scheduler
from evdt.monitoring.pacing import TIME_SCALES, WorkingClock, time_scale
from evdt.monitoring.source import SumoVehicleSource


class TripPacingTests(unittest.TestCase):
    def test_all_speeds_preserve_every_tick_and_one_second_samples(self):
        for speed in TIME_SCALES:
            now = [100.0]

            def sleep(seconds):
                self.assertGreaterEqual(seconds, 0)
                now[0] += seconds

            with (
                self.subTest(speed=speed),
                patch("evdt.monitoring.pacing.time.monotonic", side_effect=lambda: now[0]),
                patch("evdt.monitoring.pacing.time.sleep", side_effect=sleep),
            ):
                clock = WorkingClock(speed)
                samples = []
                for tick in range(1, 301):
                    self.assertAlmostEqual(clock.wait_until(tick * 0.2), 0)
                    if tick % 5 == 0:
                        samples.append((tick * 0.2, now[0]))
                self.assertEqual([s[0] for s in samples], list(range(1, 61)))
                self.assertAlmostEqual(now[0] - 100, 60 / speed)
                for first, second in zip(samples, samples[1:]):
                    self.assertAlmostEqual(second[1] - first[1], 1 / speed)

    def test_pause_delays_real_clock_and_slow_host_never_skips_steps(self):
        with (
            patch("evdt.monitoring.pacing.time.monotonic", return_value=110),
            patch("evdt.monitoring.pacing.time.sleep") as sleep,
        ):
            clock = WorkingClock(20, 100)
            self.assertEqual(clock.wait_until(20), 9)
            sleep.assert_called_with(0)
            clock.delay(10)
            self.assertEqual(clock.wait_until(20), 0)
            sleep.assert_called_with(1)

    def test_invalid_scales_and_qualified_wall_time_workloads_are_rejected(self):
        for value in (True, False, 0, -1, 3, 21, "5", None, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                time_scale(value)
        for kwargs in ({"paced": False}, {"start_clock": lambda: 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SumoVehicleSource(
                    Path("a"), Path("b"), Path("c"), "bus", 30, time_scale_factor=2, **kwargs
                )

    def test_fast_forecasts_keep_newest_input_and_never_relabel_stale_results(self):
        for speed in TIME_SCALES:
            scheduler = Scheduler()
            scheduler.observe(snapshot(30, 100))
            claim = scheduler.claim(fence=1, owner="controller", monotonic_s=10)
            # A four-real-life-second worker. The vehicle continues producing readings.
            for real_step in range(1, 5):
                scheduler.observe(snapshot(30 + speed * real_step, 100 + real_step))
                self.assertIsNone(
                    scheduler.claim(fence=2, owner="controller", monotonic_s=10 + real_step)
                )
            completion = scheduler.finish(
                forecast(claim.request),
                fence=1,
                owner="controller",
                monotonic_s=14,
                now_real_life_s=104,
            )
            with self.subTest(speed=speed):
                self.assertEqual(
                    completion.classification, "historical" if speed == 20 else "current"
                )
                if speed == 20:
                    self.assertEqual(completion.reason, "forecast_working_age_exceeded")
                    next_claim = scheduler.claim(fence=2, owner="controller", monotonic_s=14)
                    self.assertEqual(next_claim.request.state.working_s, 110)
                self.assertEqual(claim.deadline_monotonic_s, 40)
