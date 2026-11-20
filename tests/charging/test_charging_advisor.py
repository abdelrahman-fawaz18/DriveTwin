"""Regressions for advisory selection on the archived Phase 4 road network."""

import copy
import unittest
from unittest.mock import patch

from evdt.config import ROOT
from evdt.contract_profile import validator
from evdt.energy.charging_advisor import Recommendation, eligibility, rank
from evdt.energy.station_arrival import StationApproach
from scripts.charging.run_charging_advisor import SCHEMA, inputs, load, replay

SCENARIO = ROOT / "qualification/charging/advisory-scenario-1.0.0.json"


def update(station, observed=0, **changes):
    return {
        "station_id": station,
        "observed_sim_s": observed,
        "available_sim_s": observed,
        "state": "open",
        "occupied_bays": 0,
        "queued_vehicles": 0,
        "expected_wait_s": 0,
        **changes,
    }


def candidate(record, station):
    return next(row for row in record["candidates"] if row["station_id"] == station)


class AdvisorReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = inputs()
        cls.scenario = load(SCENARIO)

    def evaluate(self, scenario):
        validator(SCHEMA).validate(scenario)
        return replay(scenario, *self.inputs)

    def one_decision(self, sim_s=0, updates=None, **changes):
        scenario = copy.deepcopy(self.scenario)
        scenario["decisions"] = [
            {**scenario["decisions"][0], "sim_s": sim_s, "observed_sim_s": sim_s, **changes}
        ]
        scenario["station_updates"] = [] if updates is None else updates
        return self.evaluate(scenario)[0]

    def test_matched_timeline_and_delayed_outage(self):
        records = self.evaluate(self.scenario)
        self.assertEqual(records, self.evaluate(self.scenario))
        first = records[0]
        self.assertEqual(first["recommended_station"], "opportunity-b")
        self.assertEqual(first["nearest_feasible_station"], "west-c")
        self.assertEqual(records[1]["reason"], "await_sustained_improvement")
        self.assertEqual(records[3]["reason"], "cooldown")
        before_outage, after_outage, all_blocked, recovery = records[6:]
        self.assertEqual(before_outage["recommended_station"], "depot-a")
        self.assertEqual(candidate(before_outage, "depot-a")["status"]["observed_sim_s"], 60)
        self.assertEqual(after_outage["reason"], "incumbent_ineligible")
        self.assertEqual(after_outage["recommended_station"], "opportunity-b")
        self.assertEqual(candidate(after_outage, "depot-a")["status_age_s"], 5)
        self.assertEqual(all_blocked["reason"], "no_feasible_station")
        self.assertIsNone(all_blocked["recommended_station"])
        self.assertEqual(candidate(all_blocked, "opportunity-b")["reasons"], ["queue_full"])
        self.assertEqual(candidate(all_blocked, "west-c")["reasons"], ["queue_full"])
        self.assertEqual(recovery["recommended_station"], "depot-a")
        self.assertEqual(candidate(recovery, "depot-a")["score"]["wait_s"], 20)

    def test_freshness_causality_and_out_of_order_delivery(self):
        observations = [
            update("opportunity-b", 0),
            update("west-c", 10, state="closed"),
            update("west-c", 5, available_sim_s=15),
            update("depot-a", 5, available_sim_s=16),
        ]
        early = self.one_decision(14.9, observations)
        cutoff = self.one_decision(15, observations)
        self.assertTrue(candidate(early, "opportunity-b")["eligible"])
        self.assertIn("status_stale", candidate(cutoff, "opportunity-b")["reasons"])
        self.assertIn("status_missing", candidate(cutoff, "depot-a")["reasons"])
        self.assertIn("station_closed", candidate(cutoff, "west-c")["reasons"])
        self.assertIsNone(cutoff["recommended_station"])

    def test_keep_mandatory_stop_and_route_forward_to_a_station_behind_departure(self):
        before_stop = dict(departure_route_index=0, mission_stop_served=False)
        legal = self.one_decision(updates=[update("depot-a")], **before_stop)
        route = candidate(legal, "depot-a")["route"]
        self.assertEqual(route["rejoin_route_index"], 1)
        self.assertEqual(route["rejoin_lane"], "107583310#9_0")
        self.assertEqual(route["rejoin_edges"][-1], "107583310#9")
        behind = self.one_decision(updates=[update("depot-a")], departure_m=28, **before_stop)
        forward_loop = candidate(behind, "depot-a")["route"]["approach_edges"]
        self.assertEqual(forward_loop[0], forward_loop[-1])
        self.assertGreater(len(forward_loop), 1)
        invalid = self.one_decision(updates=[update("depot-a")], mission_stop_served=False)
        self.assertEqual(invalid["reason"], "mission_position_unavailable")

    def test_disconnected_approach_and_rejoin_are_excluded(self):
        net = self.inputs[0]
        shortest_path = net.getShortestPath

        def blocked_path(origin, target, **kwargs):
            if origin.getID() == "107583310#8" and kwargs["fromPos"] in (13, 26):
                return None, float("inf")
            return shortest_path(origin, target, **kwargs)

        before_stop = dict(departure_route_index=0, mission_stop_served=False)
        with patch.object(net, "getShortestPath", side_effect=blocked_path):
            record = self.one_decision(updates=[update("depot-a")], **before_stop)
        self.assertIn("approach_unreachable", candidate(record, "depot-a")["reasons"])

        def blocked_rejoin(origin, target, **kwargs):
            if origin.getID() == "107583310#8" and kwargs["fromPos"] == 26:
                return None, float("inf")
            return shortest_path(origin, target, **kwargs)

        with patch.object(net, "getShortestPath", side_effect=blocked_rejoin):
            record = self.one_decision(updates=[update("depot-a")], **before_stop)
        self.assertIn("mission_rejoin_unreachable", candidate(record, "depot-a")["reasons"])

    def test_reject_incompatible_and_unbounded_wait(self):
        record = self.one_decision(
            updates=[
                update("depot-a", occupied_bays=1, expected_wait_s=None),
                update("west-c", occupied_bays=2, expected_wait_s=46),
            ],
            connector_id="sim-dc-plug-b",
        )
        self.assertEqual(
            candidate(record, "depot-a")["reasons"],
            [
                "incompatible_connector",
                "wait_unknown",
            ],
        )
        self.assertEqual(candidate(record, "west-c")["reasons"], ["wait_outside_domain"])
        self.assertEqual(record["reason"], "no_feasible_station")

    def test_departure_and_continuation_gates(self):
        for changes, reason in (
            ({"observed_sim_s": 1}, "departure_observation_unavailable"),
            ({"speed_m_s": 1}, "departure_outside_forecast_domain"),
            ({"initial_energy_kwh": 34}, "reserve_violated"),
            ({"remaining_minimum_kwh": None}, "remaining_mission_forecast_unavailable"),
            ({"remaining_minimum_kwh": 35}, "no_diversion_needed"),
        ):
            with self.subTest(reason=reason):
                record = self.one_decision(updates=[update("depot-a")], **changes)
                self.assertEqual(record["reason"], reason)
                self.assertIsNone(record["recommended_station"])

    def test_duplicate_or_impossible_status_rejected(self):
        for observations in (
            [update("depot-a"), update("depot-a")],
            [update("depot-a", 5, available_sim_s=4)],
        ):
            with self.assertRaises(ValueError):
                self.one_decision(updates=observations)

    def test_wait_can_cross_reserve_and_charger_acceptance_limits(self):
        row = self.scenario["decisions"][0]
        decision = {
            **row,
            "initial_energy_kwh": 36.3,
            "auxiliary_power_w": 12000,
            "bus_acceptance_w": 100000,
        }
        approach = StationApproach(36.3, 12000, 10, 1, 0, 0)
        station = self.inputs[2]["stations"][0]
        model = self.inputs[-1]["model"]["candidate"]
        route = (approach, {"detour_s": 5})
        immediate = eligibility(station, update("depot-a"), decision, route, model, 1200)
        self.assertTrue(immediate["eligible"])
        self.assertEqual(immediate["score"]["net_charge_power_w"], 83000)
        waiting = eligibility(
            station,
            update("depot-a", occupied_bays=1, expected_wait_s=45),
            decision,
            route,
            model,
            1200,
        )
        self.assertEqual(waiting["reasons"], ["reserve_shortfall"])
        self.assertNotIn("score", waiting)
        limited = eligibility(
            station,
            update("depot-a"),
            {**decision, "bus_charge_limit_kwh": 85, "post_charge_draw_budget_kwh": 53},
            route,
            model,
            1200,
        )
        self.assertIn("target_above_bus_limit", limited["reasons"])
        self.assertIn("target_insufficient_for_remaining_mission", limited["reasons"])

    def test_noise_and_sampling_gap_reset_switch_evidence(self):
        recommendation = Recommendation()

        def ranked(a, b):
            rows = [
                {
                    "eligible": True,
                    "station_id": key,
                    "score": {"total_s": score},
                    "route": {"approach_distance_m": 100},
                }
                for key, score in (("b", b), ("a", a))
            ]
            return rank(rows)[0]

        self.assertEqual(recommendation.update(0, ranked(100, 100))["recommended_station"], "a")
        self.assertEqual(recommendation.update(10, ranked(100, 70))["challenger"], "b")
        self.assertEqual(recommendation.update(20, ranked(100, 71))["reason"], "retain_incumbent")
        self.assertEqual(recommendation.update(30, ranked(100, 70))["challenger_since_sim_s"], 30)
        self.assertEqual(recommendation.update(60, ranked(100, 70))["challenger_since_sim_s"], 60)
        self.assertEqual(recommendation.update(70, ranked(100, 70))["recommended_station"], "a")
        self.assertEqual(recommendation.update(80, ranked(100, 70))["recommended_station"], "b")
