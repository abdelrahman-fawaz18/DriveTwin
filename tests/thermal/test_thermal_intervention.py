"""Verify exact handoff boundaries, actuator effects, and charging precedence."""

import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
from qualify_thermal_intervention import PROTOCOL, advisory_rules, read, score
from thermal_advisory import Reading, Sample
from thermal_intervention import Interlocks, MissionBoundary
from thermal_missions import Scenario, TrendForecast, simulate
from thermal_network import HeatNetwork

from evdt.command_timing import ClockLease, HostClock
from evdt.contract_profile import Rejected
from evdt.energy.charging_dispatch import (
    CHARGING_COMMAND_VERSION,
    DiversionContext,
    DiversionProposal,
)

SCOPE = ("local-qualification", "bus")
CLEAR = Interlocks()
PROTECTED = Interlocks(local_protection=True)
EPOCH = "00000000-0000-0000-0000-000000000001"
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def clock(seconds=0, epoch=EPOCH):
    return HostClock(epoch, 10_000_000_000 + round(seconds * 1e9), UTC + timedelta(seconds=seconds))


def observation(tick):
    return Sample(tick, Reading(350, tick), Reading(331, tick), 344, 326)


def departure(tick, now):
    data = {
        "run_id": SCOPE[0],
        "vehicle_id": SCOPE[1],
        "station_id": "fixture-station",
        "policy_version": "fixture",
        "release_sha256": "a" * 64,
        "decision_sha256": "b" * 64,
        "departure_observation_id": "fixture-observation",
    }
    return (
        DiversionProposal(
            **data,
            version=CHARGING_COMMAND_VERSION,
            correlation_id="fixture-diversion",
            valid_from_sim_s=tick,
            expires_sim_s=tick + 1,
            wall_lease=ClockLease.until(now, now.utc + timedelta(seconds=3)),
        ),
        DiversionContext(
            **data,
            now_sim_s=tick,
            observation_sim_s=tick,
            observation_elapsed_ns=now.elapsed_ns,
            station_observed_sim_s=tick - 1,
            station_available_sim_s=tick,
            station_max_age_s=15,
            automatic_approved=True,
            operator_fixture_approved=False,
            operator_override=False,
            thermal_block=False,
            local_protection_block=False,
            station_eligible=True,
        ),
    )


class CoordinationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules, _ = advisory_rules()
        cls.persistence = cls.rules["persistence_s"]

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.boundary = self.ready(self.db)
        self.tick = self.persistence

    def ready(self, db, *, through=None):
        boundary = MissionBoundary(SCOPE, db, self.rules, 0, 3)
        for tick in range((self.persistence if through is None else through) + 1):
            boundary.observe(observation(tick), clock())
        return boundary

    def activate(self):
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        self.boundary.observe(observation(self.tick + 1), clock(1))
        self.boundary.grant(permit, self.tick + 1, clock(1), CLEAR)
        return permit

    def test_corroboration_and_vehicle_scope(self):
        for scope in (("another-run", SCOPE[1]), (SCOPE[0], "another-bus")):
            with self.subTest(scope=scope), self.assertRaisesRegex(Rejected, "thermal_scope"):
                self.boundary.reserve(scope, self.tick, clock(), CLEAR)
        early = self.ready(self.db, through=self.tick - 1)
        with self.assertRaisesRegex(Rejected, "thermal_evidence_ineligible"):
            early.reserve(SCOPE, self.tick - 1, clock(), CLEAR)
        self.assertIsNotNone(self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR))
        self.assertFalse(self.boundary.pump_on(self.tick, clock(), CLEAR))

    def test_last_valid_sim_second_and_exact_boost_expiry(self):
        permit = self.activate()
        for tick in range(self.tick + 1, self.tick + 5):
            if tick > self.tick + 1:
                self.boundary.observe(observation(tick), clock(1))
            self.assertEqual(self.boundary.pump_on(tick, clock(1), CLEAR), tick < self.tick + 4)
        with self.assertRaisesRegex(Rejected, "thermal_reservation_invalid"):
            self.boundary.grant(permit, self.tick + 1, clock(1), CLEAR)
        with self.assertRaisesRegex(Rejected, "thermal_grant_spent"):
            self.boundary.reserve(SCOPE, self.tick + 4, clock(1), CLEAR)

    def test_sim_handoff_expiry_and_early_execution(self):
        for invalid in (self.tick - 1, self.tick + 2):
            with self.subTest(tick=invalid):
                permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
                with self.assertRaisesRegex(Rejected, "thermal_sim_deadline"):
                    self.boundary.grant(permit, invalid, clock(), CLEAR)
                self.assertIsNone(self.boundary.pending)
                self.assertFalse(self.boundary.pump_on(invalid, clock(), CLEAR))

    def test_real_deadline_and_process_epoch(self):
        next_tick = self.tick + 1
        for reason, current in (
            ("elapsed_deadline", clock(3)),
            ("clock_epoch_changed", clock(2, "00000000-0000-0000-0000-000000000002")),
            ("clock_skew", replace(clock(2.999999), utc=UTC)),
        ):
            with self.subTest(reason=reason):
                boundary = self.ready(self.db)
                permit = boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
                boundary.observe(observation(next_tick), clock(2))
                with self.assertRaisesRegex(Rejected, reason):
                    boundary.grant(permit, next_tick, current, CLEAR)
                self.assertFalse(boundary.pump_on(next_tick, clock(2), CLEAR))
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        self.boundary.observe(observation(next_tick), clock(2.999999))
        self.boundary.grant(permit, next_tick, clock(2.999999), CLEAR)
        self.assertTrue(self.boundary.pump_on(next_tick, clock(2.999999), CLEAR))

    def test_observation_freshness_boundary(self):
        with self.assertRaisesRegex(Rejected, "thermal_stale_observation"):
            self.boundary.reserve(SCOPE, self.tick, clock(2), CLEAR)
        self.boundary.reserve(SCOPE, self.tick, clock(1.999999), CLEAR)

    def test_actuator_rechecks_expiry_after_grant_before_first_effect(self):
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        self.boundary.observe(observation(self.tick + 1), clock(2.9))
        self.boundary.grant(permit, self.tick + 1, clock(2.9), CLEAR)
        self.assertFalse(self.boundary.pump_on(self.tick + 1, clock(3), CLEAR))
        with self.assertRaisesRegex(Rejected, "thermal_grant_spent"):
            self.boundary.reserve(SCOPE, self.tick + 1, clock(3), CLEAR)

    def test_updated_readings_required_before_grant_and_to_continue(self):
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        with self.assertRaisesRegex(Rejected, "thermal_stale_observation"):
            self.boundary.grant(permit, self.tick + 1, clock(1), CLEAR)
        self.assertIsNone(self.boundary.pending)
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        self.boundary.observe(observation(self.tick + 1), clock(1))
        self.boundary.grant(permit, self.tick + 1, clock(1), CLEAR)
        self.boundary.observe(replace(observation(self.tick + 2), motor=None), clock(1))
        self.assertFalse(self.boundary.pump_on(self.tick + 2, clock(1), CLEAR))
        self.boundary.observe(observation(self.tick + 3), clock(1))
        self.assertFalse(self.boundary.pump_on(self.tick + 3, clock(1), CLEAR))

    def test_protection_wins_after_reservation_and_during_active_boost(self):
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        with self.assertRaisesRegex(Rejected, "local_protection"):
            self.boundary.grant(permit, self.tick, clock(), PROTECTED)
        proposal, context = departure(self.tick, clock())
        with self.assertRaisesRegex(Rejected, "local_protection"):
            self.boundary.divert(proposal, context, clock(), PROTECTED)
        self.assertIsNone(self.boundary.station_id)
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        self.boundary.grant(permit, self.tick, clock(), CLEAR)
        self.assertFalse(self.boundary.pump_on(self.tick, clock(), PROTECTED))
        self.assertFalse(self.boundary.pump_on(self.tick, clock(), CLEAR))

    def test_operator_override_before_grant_and_during_active_boost(self):
        for granted in (False, True):
            with self.subTest(granted=granted), closing(sqlite3.connect(":memory:")) as db:
                boundary = self.ready(db)
                permit = boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
                if granted:
                    boundary.grant(permit, self.tick, clock(), CLEAR)
                boundary.override()
                with self.assertRaisesRegex(Rejected, "thermal_reservation_invalid"):
                    boundary.grant(permit, self.tick, clock(), CLEAR)
                with self.assertRaisesRegex(Rejected, "operator_override"):
                    boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
                proposal, context = departure(self.tick, clock())
                with self.assertRaisesRegex(Rejected, "operator_override"):
                    boundary.divert(proposal, context, clock(), CLEAR)
                self.assertIsNone(boundary.station_id)
                self.assertFalse(boundary.pump_on(self.tick, clock(), CLEAR))

    def test_pending_then_active_cooling_blocks_charging(self):
        permit = self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        proposal, context = departure(self.tick, clock())
        with self.assertRaisesRegex(Rejected, "thermal_handoff_pending"):
            self.boundary.divert(proposal, context, clock(), CLEAR)
        self.boundary.grant(permit, self.tick, clock(), CLEAR)
        with self.assertRaisesRegex(Rejected, "local_protection"):
            self.boundary.divert(proposal, context, clock(), CLEAR)
        self.assertIsNone(self.boundary.station_id)
        self.assertTrue(self.boundary.pump_on(self.tick, clock(), CLEAR))
        later = self.tick + 3
        self.boundary.divert(*departure(later, clock(1)), clock(1), CLEAR)
        self.assertEqual(self.boundary.station_id, "fixture-station")
        self.assertFalse(self.boundary.pump_on(later, clock(1), CLEAR))

    def test_accepted_charging_blocks_cooling_and_expired_thermal_does_not_block_route(self):
        self.boundary.reserve(SCOPE, self.tick, clock(), CLEAR)
        later = self.tick + 2
        self.boundary.divert(*departure(later, clock(1)), clock(1), CLEAR)
        self.boundary.observe(observation(later), clock(1))
        with self.assertRaisesRegex(Rejected, "cooling_unavailable"):
            self.boundary.reserve(SCOPE, later, clock(1), CLEAR)
        self.assertIsNone(self.boundary.pending)
        self.assertEqual(self.boundary.station_id, "fixture-station")

    def test_phase4_freshness_eligibility_and_exact_charging_expiry(self):
        proposal, context = departure(self.tick, clock())
        for changes, reason in (
            ({"station_max_age_s": 1}, "charging_stale_station"),
            (
                {"observation_elapsed_ns": clock().elapsed_ns - 5_000_000_000},
                "charging_stale_departure",
            ),
            ({"station_eligible": False}, "charging_station_ineligible"),
            ({"automatic_approved": False}, "automatic_dispatch_not_approved"),
            ({"now_sim_s": self.tick + 1}, "charging_expired_or_early"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(Rejected, reason):
                self.boundary.divert(proposal, replace(context, **changes), clock(), CLEAR)
        self.assertIsNone(self.boundary.station_id)
        self.boundary.divert(proposal, context, clock(), CLEAR)
        self.assertEqual(self.boundary.station_id, "fixture-station")

    def test_restart_discards_pending_and_preserves_spent_grant(self):
        runtime = Path(__file__).resolve().parents[2] / ".runtime"
        runtime.mkdir(exist_ok=True)
        path = runtime / f"thermal-restart-{uuid4().hex}.sqlite"
        self.addCleanup(path.unlink, missing_ok=True)
        with closing(sqlite3.connect(path)) as db:
            first = self.ready(db)
            old = first.reserve(SCOPE, self.tick, clock(), CLEAR)
        with closing(sqlite3.connect(path)) as db:
            second = self.ready(db)
            with self.assertRaisesRegex(Rejected, "thermal_reservation_invalid"):
                second.grant(old, self.tick, clock(), CLEAR)
            new = second.reserve(SCOPE, self.tick, clock(), CLEAR)
            second.grant(new, self.tick, clock(), CLEAR)
        with closing(sqlite3.connect(path)) as db:
            restarted = self.ready(db)
            self.assertFalse(restarted.pump_on(self.tick, clock(), CLEAR))
            with self.assertRaisesRegex(Rejected, "thermal_grant_spent"):
                restarted.reserve(SCOPE, self.tick, clock(), CLEAR)
            restarted.override()
        with closing(sqlite3.connect(path)) as db:
            restarted = self.ready(db)
            with self.assertRaisesRegex(Rejected, "operator_override"):
                restarted.divert(*departure(self.tick, clock()), clock(), CLEAR)


class MeasurementTest(unittest.TestCase):
    def test_incremental_plant_matches_batch_recurrence(self):
        network = HeatNetwork((36000, 64000), (15, 180), (0, 0), 250).discrete(1)
        state = np.array([334.0, 321.0])
        ambient = np.array([296.0, 297.0, 298.0])
        heat = np.array([[6000, 900], [5800, 800], [6000, 1100]])
        expected = network.rollout(state, ambient, ambient, heat)
        for tick, increment in enumerate(
            network.increments(ambient, ambient, heat) @ network.from_modes
        ):
            state = network.transition @ state + increment
            np.testing.assert_allclose(state, expected[tick], atol=1e-10, rtol=0)

    def test_forecast_causal_and_disabled_before_pass(self):
        policy = read(PROTOCOL)
        forecast = TrendForecast(policy["forecast"])
        for tick in range(25):
            sample = replace(observation(tick), motor=Reading(345 + 0.1 * tick, tick))
            self.assertIsNone(forecast.update(sample))
        sample = replace(observation(25), motor=Reading(347.5, 25))
        self.assertAlmostEqual(forecast.update(sample), 353.5)
        self.assertIsNone(forecast.update(replace(sample, time_s=26)))
        rules, warmup = advisory_rules()
        with self.assertRaisesRegex(ValueError, "no passing forecast gate"):
            simulate(
                Scenario("healthy", 7),
                "predictive",
                policy,
                rules,
                warmup,
                forecast_qualified=False,
            )

    def test_scorer_distinguishes_failures_and_incomplete_outcomes(self):
        policy = read(PROTOCOL)
        trace = np.array([[0, 352, 331, 3600, 1200, 0, 1]])
        self.assertEqual(score(trace, policy, None, None)["status"], "failed")
        result = score(trace, policy, 0, None)
        self.assertEqual(result["status"], "incomplete")
        self.assertAlmostEqual(result["exposure_k_min"], 2 / 60)
        self.assertAlmostEqual(result["energy_kwh"], 4800 / 3_600_000)
        self.assertIsNone(result["mission_delay_s"])


if __name__ == "__main__":
    unittest.main()
