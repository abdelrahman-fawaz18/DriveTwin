"""Prepared geographic trips retain exact inputs and indexed repeated-road stops."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evdt.monitoring.prepared_journey import PreparedJourney


class PreparedJourneyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        (self.path / "network.net.xml").write_text("<net/>")
        (self.path / "routes.rou.xml").write_text('<routes><vehicle id="bus"/></routes>')
        self.spec = {
            "version": "prepared-live-journey/1.0.0",
            "sha256": {
                name: hashlib.sha256((self.path / name).read_bytes()).hexdigest()
                for name in ("network.net.xml", "routes.rou.xml")
            },
            "route_edges": ["a", "b", "a"],
            "speed_factor": 1,
            "endpoint": {"external_index": 2, "lane_id": "a_0", "offset_m": 80},
            "stops": [
                {"external_index": 0, "lane_id": "a_0", "offset_m": 50},
                {"external_index": 2, "lane_id": "a_0", "offset_m": 50},
                {"external_index": 2, "lane_id": "a_0", "offset_m": 80},
            ],
        }
        self.save()

    def save(self):
        (self.path / "prepared-journey.json").write_text(json.dumps(self.spec))

    def test_changed_input_is_refused(self):
        (self.path / "network.net.xml").write_text("<net changed='true'/>")
        with self.assertRaisesRegex(ValueError, "input changed"):
            PreparedJourney(self.path)

    def test_exact_network_and_vehicle_identity_are_installed(self):
        work = self.path / "work"
        work.mkdir()
        PreparedJourney(self.path).install(work, "bus-001")
        self.assertEqual((work / "network.net.xml").read_bytes(), b"<net/>")
        self.assertIn('id="bus-001"', (work / "routes.rou.xml").read_text())

    def test_repeated_stops_remain_bound_to_their_visit(self):
        journey = PreparedJourney(self.path)
        stops = [SimpleNamespace(lane="a_0", endPos=50), SimpleNamespace(lane="a_0", endPos=80)]
        self.assertEqual(journey.stop_occurrences(("a", "b", "a"), stops), [2, 2])
        with self.assertRaises(ValueError):
            journey.stop_occurrences(("a", "a", "b"), stops)
        stops[0].endPos = 51
        with self.assertRaises(ValueError):
            journey.stop_occurrences(("a", "b", "a"), stops)

    def test_arrival_requires_the_endpoint_visit_position_and_a_stop(self):
        journey = PreparedJourney(self.path)
        current = {"index": 0, "offset": 80, "speed": 0}
        factors = []
        vehicle = SimpleNamespace(
            getRouteIndex=lambda _: current["index"],
            getLaneID=lambda _: "a_0",
            getLanePosition=lambda _: current["offset"],
            getSpeed=lambda _: current["speed"],
            setSpeedFactor=lambda _, v: factors.append(v),
        )
        connection = SimpleNamespace(vehicle=vehicle)
        journey.observe(connection, "bus-001")
        self.assertFalse(journey.arrived)
        current.update(index=2, speed=1)
        journey.observe(connection, "bus-001")
        self.assertFalse(journey.arrived)
        current.update(speed=0)
        journey.observe(connection, "bus-001")
        self.assertTrue(journey.arrived)
        self.assertEqual(factors, [1, 1, 1])

    def test_local_speed_restriction_ends_at_its_assigned_road_boundary(self):
        self.spec["speed_zones"] = [{"start_index": 1, "end_index": 2, "speed_factor": 0.65}]
        self.save()
        journey = PreparedJourney(self.path)
        factors = []
        current = {"index": 0}
        vehicle = SimpleNamespace(
            getRouteIndex=lambda _: current["index"],
            getLaneID=lambda _: "a_0",
            getLanePosition=lambda _: 0,
            getSpeed=lambda _: 1,
            setSpeedFactor=lambda _, value: factors.append(value),
        )
        for index in (0, 1, 2):
            current["index"] = index
            journey.observe(SimpleNamespace(vehicle=vehicle), "bus-001")
        self.assertEqual(factors, [1, 0.65, 1])
