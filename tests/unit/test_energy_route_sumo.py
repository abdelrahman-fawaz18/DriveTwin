"""Static network tests do not import SUMO or run a vehicle."""

import unittest

from evdt.energy.route_aware.contracts import PlannedStop, RoadPoint
from evdt.energy.route_aware.geometry import RouteUnavailable, true_heading
from evdt.energy.route_aware.sumo_routes import _lane_change, expand_route, read_network

NETWORK = b"""<net>
<location netOffset="10,20" projParameter="!"/>
<edge id="a" type="urban"><lane id="a_0" index="0" speed="10" length="100" shape="0,0 100,0"/></edge>
<edge id=":j" function="internal"><lane id=":j_0" index="0" speed="5" length="10" shape="100,0 110,0"/></edge>
<edge id="b" type="urban"><lane id="b_0" index="0" speed="10" length="100" shape="110,0 210,0"/></edge>
<connection from="a" to="b" fromLane="0" toLane="0" via=":j_0"/>
<connection from=":j" to="b" fromLane="0" toLane="0"/>
</net>"""


class SumoRouteTests(unittest.TestCase):
    def test_lane_change_collapses_coincident_samples(self):
        raw = b"""<net>
        <location netOffset="0,0" projParameter="!"/>
        <edge id="road">
        <lane id="road_0" index="0" speed="10" length="142.40"
        shape="13948.49,7074.62 13947.28,7078.26 13945.50,7084.31 13943.86,7090.33
        13941.72,7099.75 13939.82,7109.75 13937.96,7119.99 13935.06,7136.25 13921.22,7213.82"/>
        <lane id="road_1" index="1" speed="10" length="142.40"
        shape="13945.46,7073.61 13944.23,7077.30 13942.42,7083.44 13940.75,7089.55
        13938.59,7099.10 13936.67,7109.16 13934.81,7119.42 13931.91,7135.69 13918.07,7213.26"/>
        </edge></net>"""
        lanes = read_network(raw).lanes
        path = _lane_change(lanes["road_0"], lanes["road_1"])
        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(path.shape[0], lanes["road_0"].shape[0])
        self.assertEqual(path.shape[-1], lanes["road_1"].shape[-1])
        self.assertTrue(all(a != b for a, b in zip(path.shape, path.shape[1:])))

    def test_assumed_lane_change_preserves_endpoint_and_permission(self):
        raw = NETWORK.replace(
            b"</net>",
            b"""
        <edge id="c"><lane id="c_0" index="0" speed="10" length="100" shape="210,3 310,3"/></edge>
        <connection from="b" to="c" fromLane="1" toLane="0"/>
        </net>""",
        ).replace(
            b'<edge id="b" type="urban">',
            b"""<edge id="b" type="urban"><lane id="b_1" index="1" speed="10" length="100" shape="110,3 210,3"/>""",
        )
        result = expand_route(read_network(raw), ("a", "b", "c"), revision="r1")
        changed = result.context.occurrences[2]
        self.assertEqual(changed.geometry_basis, "assumed_lane_change")
        self.assertEqual(changed.alternative_lane_ids, ("b_0", "b_1"))
        self.assertEqual(changed.shape[0], RoadPoint(110, 0, None))
        self.assertEqual(changed.shape[-1], RoadPoint(210, 3, None))
        self.assertTrue(all(0 <= p.y_m <= 3 for p in changed.shape))
        for blocked in (
            raw.replace(b'id="b_0"', b'id="b_0" changeLeft="passenger"'),
            raw.replace(b'length="100" shape="110,0', b'length="20" shape="110,0'),
        ):
            with self.assertRaises(RouteUnavailable):
                expand_route(read_network(blocked), ("a", "b", "c"), revision="r1")

    def test_internal_lane_and_stop_are_preserved(self):
        network = read_network(NETWORK)
        route = expand_route(
            network,
            ("a", "b"),
            revision="r1",
            stops_by_external_index={1: (PlannedStop("s", 20, 10),)},
            required_lanes={1: "b_0"},
        )
        self.assertEqual([o.lane_id for o in route.context.occurrences], ["a_0", ":j_0", "b_0"])
        self.assertEqual(route.external_occurrences, (0, 2))
        self.assertEqual(route.context.occurrences[2].stops[0].offset_m, 20)
        self.assertEqual(network.offset_x_m, 10)
        self.assertEqual(network.offset_y_m, 20)

    def test_current_junction_lane_pins_the_outgoing_connection(self):
        raw = NETWORK.replace(
            b"</net>",
            b"""<edge id=":other" function="internal">
            <lane id=":other_0" index="0" speed="5" length="11" shape="100,0 105,1 110,0"/>
            </edge>
            <connection from="a" to="b" fromLane="0" toLane="0" via=":other_0"/>
            <connection from=":other" to="b" fromLane="0" toLane="0"/>
            </net>""",
        )
        network = read_network(raw)
        result = expand_route(network, ("a", "b"), revision="r1", current_junction_lane=":other_0")
        self.assertEqual(
            [o.lane_id for o in result.context.occurrences], ["a_0", ":other_0", "b_0"]
        )
        self.assertEqual(result.external_occurrences, (0, 2))
        with self.assertRaises(RouteUnavailable):
            expand_route(network, ("a", "b"), revision="r1", current_junction_lane=":absent_0")

    def test_permission_or_unconnected_path_does_not_invent_a_join(self):
        for raw in (
            NETWORK.replace(b'via=":j_0"', b'via="absent"'),
            NETWORK.replace(b'id="b_0"', b'id="b_0" disallow="bus"'),
        ):
            with self.assertRaises(RouteUnavailable):
                expand_route(read_network(raw), ("a", "b"), revision="r1")

    def test_shapes_cannot_jump_across_a_junction(self):
        raw = NETWORK.replace(b'shape="110,0 210,0"', b'shape="120,0 210,0"')
        with self.assertRaisesRegex(RouteUnavailable, "geometry_disconnected"):
            expand_route(read_network(raw), ("a", "b"), revision="r1")

    def test_small_rounding_snap_is_counted(self):
        raw = NETWORK.replace(b'shape="110,0 210,0"', b'shape="110.01,0 210,0"')
        result = expand_route(read_network(raw), ("a", "b"), revision="r1")
        self.assertEqual(result.endpoint_snaps, 1)
        self.assertEqual(result.context.occurrences[2].shape[0].x_m, 110)

    def test_structure_flag_never_becomes_terrain_road_height(self):
        result = expand_route(
            read_network(NETWORK), ("a", "b"), revision="r1", structures={"b": "bridge"}
        )
        self.assertEqual(result.context.occurrences[-1].structure, "bridge")
        self.assertEqual(result.context.occurrences[-1].elevation_quality, "missing")

    def test_missing_lane_pin_and_invalid_stop_index_are_rejected(self):
        with self.assertRaises(RouteUnavailable):
            expand_route(
                read_network(NETWORK), ("a", "b"), revision="r1", required_lanes={1: "b_1"}
            )
        with self.assertRaises(ValueError):
            expand_route(
                read_network(NETWORK), ("a", "b"), revision="r1", stops_by_external_index={2: ()}
            )

    def test_document_type_is_not_accepted(self):
        with self.assertRaises(ValueError):
            read_network(b"<!DOCTYPE net>" + NETWORK)

    def test_compass_heading_uses_geography_not_rotated_grid(self):
        a = RoadPoint(0, 0, 0, 45, -73)
        b = RoadPoint(100, 0, 0, 45.001, -73)
        heading, basis = true_heading(a, b, "rotated-projection")
        self.assertAlmostEqual(heading, 0)
        self.assertEqual(basis, "geographic_bearing")
        with self.assertRaisesRegex(RouteUnavailable, "geographic_coordinates"):
            true_heading(RoadPoint(0, 0, 0), RoadPoint(100, 0, 0), "rotated-projection")


if __name__ == "__main__":
    unittest.main()
