"""Closed-edge suggestions preserve a directed route and required stops."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

from test_energy_route_admission import inputs

from evdt.energy.route_aware.context import SourceContext, SourcePlan, SourceStop
from evdt.energy.route_aware.contracts import RoadPoint
from evdt.energy.route_aware.sumo_routes import Network, NetworkLane

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from energy_detour import propose  # noqa: E402


class DetourTests(unittest.TestCase):
    def setUp(self):
        roads = {
            "start": [(0, 0), (100, 0)],
            "closed": [(100, 0), (300, 0)],
            "bypass-a": [(100, 0), (200, 80)],
            "bypass-b": [(200, 80), (300, 0)],
            "destination": [(300, 0), (400, 0)],
        }
        lanes = {
            name: NetworkLane(
                name,
                name,
                False,
                "urban",
                tuple(RoadPoint(x, y, None) for x, y in coordinates),
                100,
                10,
                0,
                False,
                False,
            )
            for name, coordinates in roads.items()
        }
        self.network = Network(
            "a" * 64,
            "!",
            0,
            0,
            lanes,
            {
                "start": ("closed", "bypass-a"),
                "closed": ("destination",),
                "bypass-a": ("bypass-b",),
                "bypass-b": ("destination",),
            },
        )
        self.context = SourceContext(
            "source-route-context/2.0.0",
            inputs()["scope"],
            "b" * 64,
            1,
            10,
            SourcePlan(
                "a" * 64,
                ("start", "closed", "destination"),
                (SourceStop("scheduled", 2, "destination", 20, 15),),
            ),
            0,
            "start",
            10,
            "not_charging",
            None,
            None,
        )

    def test_bypass_preserves_stop_and_requires_confirmed_assignment(self):
        proposal = propose(self.network, self.context, {"closed"})
        self.assertEqual(proposal["route_edges"], ["start", "bypass-a", "bypass-b", "destination"])
        self.assertEqual(proposal["stops"][0]["external_occurrence_index"], 3)
        self.assertEqual(proposal["stops"][0]["dwell_working_s"], 15)
        self.assertTrue(proposal["requires_confirmed_source_assignment"])
        self.assertEqual(proposal["based_on_route_revision"], self.context.plan.revision)

    def test_refuses_missing_bypass_or_closed_stop(self):
        with self.assertRaisesRegex(ValueError, "No bus-connected detour"):
            propose(self.network, self.context, {"closed", "bypass-a"})
        context = replace(
            self.context,
            plan=replace(
                self.context.plan, remaining_stops=(SourceStop("s", 1, "closed", 20, 15),)
            ),
        )
        with self.assertRaisesRegex(ValueError, "stop is on a closed road"):
            propose(self.network, context, {"closed"})
