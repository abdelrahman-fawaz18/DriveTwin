"""Source projection observes current plan/state and refuses ambiguous stops."""

import unittest
from dataclasses import asdict
from types import SimpleNamespace

from test_energy_route_admission import inputs

from evdt.energy.route_aware.context import parse_context
from evdt.energy.route_aware.wire import fingerprint
from evdt.monitoring.route_capture import RouteCapture


class Vehicle:
    def __init__(self):
        self.edges = ("road-a", "road-b")
        self.stops = []
        self.stop_state = 0
        self.charged = "0"
        self.station = ""
        self.queries = []

    def getRoute(self, asset):
        return self.edges

    def getRouteIndex(self, asset):
        return 0

    def getLaneID(self, asset):
        return "road-a_0"

    def getLanePosition(self, asset):
        return 10

    def getStops(self, asset):
        return self.stops

    def getStopState(self, asset):
        return self.stop_state

    def getParameter(self, asset, key):
        self.queries.append(key)
        return {
            "device.battery.energyCharged": self.charged,
            "device.battery.chargingStationId": self.station,
        }[key]


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.vehicle = Vehicle()
        self.connection = SimpleNamespace(
            vehicle=self.vehicle,
            lane=SimpleNamespace(getEdgeID=lambda lane: lane.rsplit("_", 1)[0]),
        )
        self.saved = []

        def publish(raw):
            self.saved.append(parse_context(raw))
            return {"context_sha256": fingerprint(self.saved[-1])}

        self.capture = RouteCapture(asdict(inputs()["scope"]), "b" * 64, "d" * 64, publish)

    def test_only_present_charging_and_assigned_plan_are_projected(self):
        annotations = self.capture.sample(self.connection, 30)
        self.assertEqual(annotations, {"energy_context": fingerprint(self.saved[0])})
        self.assertEqual(self.saved[0].charging_state, "not_charging")
        self.assertEqual(
            self.vehicle.queries,
            ["device.battery.energyCharged", "device.battery.chargingStationId"],
        )

    def test_indexed_journey_excludes_terminal_hold_from_forecast_dwell(self):
        self.capture.indexed_journey = SimpleNamespace(stop_occurrences=lambda edges, stops: [0, 1])
        self.vehicle.stops = [
            SimpleNamespace(lane="road-a_0", endPos=50, duration=20, until=-1, stopFlags=0),
            SimpleNamespace(lane="road-b_0", endPos=100, duration=14400, until=-1, stopFlags=0),
        ]
        self.capture.sample(self.connection, 1)
        self.assertEqual(len(self.saved[-1].plan.remaining_stops), 1)
        self.assertEqual(self.saved[-1].plan.remaining_stops[0].dwell_working_s, 20)

    def test_charge_or_station_presence_invalidates_driving_state(self):
        for value, station in (("1", ""), ("0", "station-a")):
            self.vehicle.charged, self.vehicle.station = value, station
            self.capture.sample(self.connection, 30)
            self.assertEqual(self.saved[-1].charging_state, "charging")

    def test_sumo_null_station_marker_means_no_station(self):
        self.vehicle.station = "NULL"
        self.capture.sample(self.connection, 30)
        self.assertEqual(self.saved[-1].charging_state, "not_charging")

    def test_current_stop_countdown_does_not_change_the_assigned_plan(self):
        stop = SimpleNamespace(
            lane="road-a_0", endPos=10, duration=20, until=-1, stopFlags=0, arrival=-1
        )
        self.vehicle.stops = [stop]
        self.capture.sample(self.connection, 21)
        plan = self.saved[-1].plan
        self.vehicle.stop_state = 1
        stop.arrival = 22
        for working, remaining in [(30, 12), (31, 11), (42, 0)]:
            with self.subTest(working=working):
                stop.duration = remaining
                self.capture.sample(self.connection, working)
                self.assertEqual(self.saved[-1].remaining_dwell_working_s, remaining)
                self.assertEqual(self.saved[-1].current_stop_id, plan.remaining_stops[0].stop_id)
                self.assertEqual(self.saved[-1].plan.revision, plan.revision)
        self.vehicle.stops = []
        self.vehicle.stop_state = 0
        self.capture.sample(self.connection, 43)
        self.assertNotEqual(self.saved[-1].plan.revision, plan.revision)

    def test_first_snapshot_can_bind_current_stop_on_a_repeated_road(self):
        self.vehicle.edges = ("road-a", "road-b", "road-a")
        self.vehicle.stops = [
            SimpleNamespace(
                lane="road-a_0", endPos=10, duration=12, until=-1, stopFlags=0, arrival=22
            )
        ]
        self.vehicle.stop_state = 1
        self.capture.sample(self.connection, 30)
        self.assertEqual(self.saved[-1].remaining_dwell_working_s, 12)
        self.assertEqual(self.saved[-1].plan.remaining_stops[0].external_index, 0)

    def test_active_stop_extension_changes_the_plan_once(self):
        stop = SimpleNamespace(lane="road-a_0", endPos=10, duration=5, until=-1, stopFlags=0)
        self.vehicle.stops = [stop]
        self.vehicle.stop_state = 1
        self.capture.sample(self.connection, 30)
        stop.duration = 14
        self.capture.sample(self.connection, 31)
        self.assertNotEqual(self.saved[-1].plan.revision, self.saved[-2].plan.revision)
        self.assertEqual(self.saved[-1].remaining_dwell_working_s, 14)
        stop.duration = 13
        self.capture.sample(self.connection, 32)
        self.assertEqual(self.saved[-1].plan.revision, self.saved[-2].plan.revision)

    def test_stop_order_disambiguates_repeated_road_visits(self):
        self.vehicle.edges = ("road-a", "road-b", "road-a")
        self.vehicle.stops = [
            SimpleNamespace(lane=lane, endPos=80, duration=5, until=-1, stopFlags=0)
            for lane in ("road-a_0", "road-b_0", "road-a_0")
        ]
        self.capture.sample(self.connection, 30)
        self.assertEqual([s.external_index for s in self.saved[-1].plan.remaining_stops], [0, 1, 2])

    def test_repeated_road_stop_ambiguity_is_explicit(self):
        self.vehicle.edges = ("road-a", "road-b", "road-a")
        self.vehicle.stops = [
            SimpleNamespace(
                lane="road-a_0", endPos=80, duration=20, until=-1, stopFlags=0, arrival=-1
            )
        ]
        with self.assertRaisesRegex(ValueError, "ambiguous_stop_occurrence"):
            self.capture.sample(self.connection, 30)
        self.assertEqual(self.saved, [])

    def test_confirmed_detour_changes_plan_revision(self):
        self.capture.sample(self.connection, 30)
        self.vehicle.edges = ("road-a", "detour", "road-b")
        self.capture.sample(self.connection, 31)
        self.assertNotEqual(self.saved[0].plan.revision, self.saved[1].plan.revision)

    def test_unknown_departure_condition_and_negative_charge_are_rejected(self):
        self.vehicle.stops = [
            SimpleNamespace(
                lane="road-b_0", endPos=80, duration=20, until=100, stopFlags=0, arrival=-1
            )
        ]
        with self.assertRaisesRegex(ValueError, "unsupported_departure_condition"):
            self.capture.sample(self.connection, 30)
        self.vehicle.stops = []
        self.vehicle.charged = "-1"
        with self.assertRaises(ValueError):
            self.capture.sample(self.connection, 30)

    def test_identical_plan_has_stable_revision_as_working_time_advances(self):
        self.capture.sample(self.connection, 30)
        self.capture.sample(self.connection, 31)
        self.assertEqual(self.saved[0].plan.revision, self.saved[1].plan.revision)
        self.assertNotEqual(fingerprint(self.saved[0]), fingerprint(self.saved[1]))
