"""Diversion authority and clocks are checked before a simulator action."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from evdt.command_timing import ClockLease, HostClock
from evdt.contract_profile import Rejected
from evdt.energy.charging_dispatch import (
    CHARGING_COMMAND_VERSION,
    DiversionContext,
    DiversionProposal,
    check_diversion,
)


class ChargingDispatchTest(unittest.TestCase):
    def setUp(self):
        self.clock = HostClock(
            "00000000-0000-0000-0000-000000000001",
            12_000_000_000,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        scope = {
            "run_id": "fixture",
            "vehicle_id": "bus",
            "station_id": "depot-a",
            "policy_version": "offline-charging-advisor/1.0.0",
            "release_sha256": "a" * 64,
            "decision_sha256": "b" * 64,
            "departure_observation_id": "observation-1",
        }
        self.proposal = DiversionProposal(
            **scope,
            version=CHARGING_COMMAND_VERSION,
            correlation_id="diversion-1",
            valid_from_sim_s=10,
            expires_sim_s=12,
            wall_lease=ClockLease.until(self.clock, self.clock.utc + timedelta(seconds=3)),
        )
        self.context = DiversionContext(
            **scope,
            now_sim_s=10,
            observation_sim_s=10,
            observation_elapsed_ns=self.clock.elapsed_ns,
            station_observed_sim_s=9,
            station_available_sim_s=9.5,
            station_max_age_s=15,
            automatic_approved=False,
            operator_fixture_approved=True,
            operator_override=False,
            thermal_block=False,
            local_protection_block=False,
            station_eligible=True,
        )

    def test_operator_fixture_and_automatic_approval_are_distinct(self):
        check_diversion(self.proposal, self.context, self.clock, automatic=False)
        with self.assertRaisesRegex(Rejected, "automatic_dispatch_not_approved"):
            check_diversion(self.proposal, self.context, self.clock, automatic=True)
        approved = replace(self.context, automatic_approved=True)
        check_diversion(self.proposal, approved, self.clock, automatic=True)
        with self.assertRaisesRegex(Rejected, "operator_override"):
            check_diversion(
                self.proposal, replace(approved, operator_override=True), self.clock, automatic=True
            )

    def test_scope_and_evidence_cannot_be_replaced(self):
        for change, reason in (
            ({"run_id": "other-run"}, "charging_scope_mismatch"),
            ({"vehicle_id": "other-bus"}, "charging_scope_mismatch"),
            ({"station_id": "west-c"}, "charging_lineage_mismatch"),
            ({"decision_sha256": "c" * 64}, "charging_lineage_mismatch"),
            ({"departure_observation_id": "another-frame"}, "charging_lineage_mismatch"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(Rejected, reason):
                check_diversion(
                    replace(self.proposal, **change), self.context, self.clock, automatic=False
                )

    def test_expiry_and_freshness_boundaries(self):
        for change, reason in (
            ({"now_sim_s": 12}, "charging_expired_or_early"),
            ({"observation_elapsed_ns": 7_000_000_000}, "charging_stale_departure"),
            ({"station_available_sim_s": 10.1}, "charging_stale_station"),
            ({"station_max_age_s": 1}, "charging_stale_station"),
            ({"station_eligible": False}, "charging_station_ineligible"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(Rejected, reason):
                check_diversion(
                    self.proposal, replace(self.context, **change), self.clock, automatic=False
                )
        expired_wall_clock = replace(
            self.clock,
            elapsed_ns=15_000_000_000,
            utc=self.clock.utc + timedelta(seconds=3),
        )
        with self.assertRaisesRegex(Rejected, "elapsed_deadline"):
            check_diversion(self.proposal, self.context, expired_wall_clock, automatic=False)
