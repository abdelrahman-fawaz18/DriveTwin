"""Independent distance, signed-height, occurrence and stop-progress expectations."""

import math
import unittest
from dataclasses import replace

from test_energy_route_contracts import occurrence
from test_energy_route_wire import request

from evdt.energy.route_aware.contracts import PlannedStop, RoadPoint, StopRef
from evdt.energy.route_aware.geometry import RouteUnavailable, remaining_route


def case(shape, *, stops=()):
    value = request()
    route = replace(value.route, occurrences=(replace(occurrence(), shape=shape, stops=stops),))
    return route, replace(value.state, lane_offset_m=0, speed_m_s=0)


class GeometryTests(unittest.TestCase):
    def test_known_uphill_geometry_and_trim(self):
        route, state = case((RoadPoint(0, 0, 10), RoadPoint(100, 0, 15)))
        result = remaining_route(route, replace(state, lane_offset_m=40))
        self.assertAlmostEqual(result.horizontal_m, 60)
        self.assertAlmostEqual(result.distance_m, math.sqrt(60**2 + 3**2))
        self.assertAlmostEqual(result.ascent_m, 3)
        self.assertEqual(result.descent_m, 0)
        self.assertEqual(result.pieces[0].grade_ratio, 0.05)
        self.assertAlmostEqual(result.pieces[0].heading_rad, math.pi / 2)
        self.assertAlmostEqual(result.pieces[0].start.elevation_m, 12)

    def test_reversing_route_reverses_grade_and_heading(self):
        shape = (RoadPoint(0, 0, 10), RoadPoint(100, 0, 15))
        route, state = case(tuple(reversed(shape)))
        result = remaining_route(route, state)
        self.assertEqual(result.pieces[0].grade_ratio, -0.05)
        self.assertAlmostEqual(result.pieces[0].heading_rad, 3 * math.pi / 2)
        self.assertEqual(result.descent_m, 5)

    def test_intermediate_crest_is_preserved(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(50, 0, 5), RoadPoint(100, 0, 0)))
        result = remaining_route(route, state)
        self.assertEqual((result.ascent_m, result.descent_m), (5, 5))
        self.assertEqual([p.grade_ratio for p in result.pieces], [0.1, -0.1])
        self.assertAlmostEqual(result.distance_m, 2 * math.hypot(50, 5))

    def test_second_visit_is_trimmed_independently(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        outbound = route.occurrences[0]
        inbound = replace(
            outbound, index=1, edge_id="return", shape=tuple(reversed(outbound.shape))
        )
        again = replace(outbound, index=2)
        route = replace(route, occurrences=(outbound, inbound, again))
        result = remaining_route(route, replace(state, occurrence_index=2, lane_offset_m=25))
        self.assertEqual(result.distance_m, 75)
        self.assertEqual(result.pieces[0].occurrence_index, 2)

    def test_missing_junction_geometry_is_rejected(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        next_edge = replace(route.occurrences[0], index=1)
        with self.assertRaisesRegex(RouteUnavailable, "route_connection_missing"):
            remaining_route(replace(route, occurrences=(*route.occurrences, next_edge)), state)

    def test_elevation_jump_is_rejected(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        next_edge = replace(
            route.occurrences[0], index=1, shape=(RoadPoint(100, 0, 2), RoadPoint(200, 0, 2))
        )
        with self.assertRaisesRegex(RouteUnavailable, "route_elevation_discontinuous"):
            remaining_route(replace(route, occurrences=(*route.occurrences, next_edge)), state)

    def test_stop_splits_geometry_without_changing_distance(self):
        route, state = case(
            (RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)), stops=(PlannedStop("s1", 40, 20),)
        )
        result = remaining_route(route, state)
        self.assertEqual([p.distance_m for p in result.pieces], [40, 60])
        self.assertEqual(result.pieces[0].stops_after[0].dwell_working_s, 20)
        self.assertEqual(result.distance_m, 100)

    def test_remaining_current_dwell_and_completed_stop(self):
        route, state = case(
            (RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)), stops=(PlannedStop("s1", 40, 20),)
        )
        at_stop = replace(
            state,
            lane_offset_m=40,
            current_stop=StopRef(0, "s1"),
            remaining_dwell_working_s=7,
        )
        result = remaining_route(route, at_stop)
        self.assertEqual(result.initial_stops[0].dwell_working_s, 7)
        departed = replace(state, lane_offset_m=45, completed_stops=(StopRef(0, "s1"),))
        result = remaining_route(route, departed)
        self.assertEqual(result.distance_m, 55)
        self.assertEqual(result.initial_stops, ())
        self.assertFalse(any(p.stops_after for p in result.pieces))

    def test_passed_stop_requires_an_explicit_source_update(self):
        route, state = case(
            (RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)), stops=(PlannedStop("s1", 40, 20),)
        )
        with self.assertRaisesRegex(RouteUnavailable, "passed_stop_without_update"):
            remaining_route(route, replace(state, lane_offset_m=50))
        with self.assertRaisesRegex(RouteUnavailable, "completed_stop_ahead"):
            remaining_route(route, replace(state, completed_stops=(StopRef(0, "s1"),)))

    def test_source_offset_is_explicitly_scaled_to_horizontal_geometry(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        route = replace(route, occurrences=(replace(route.occurrences[0], reference_length_m=200),))
        result = remaining_route(route, replace(state, lane_offset_m=50))
        self.assertEqual(result.horizontal_m, 75)
        self.assertIn("source_offset_scaled_to_geometry", result.assumptions)

    def test_complete_route_has_no_remaining_motion(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        result = remaining_route(route, replace(state, lane_offset_m=100))
        self.assertEqual(result.distance_m, 0)
        self.assertEqual(result.pieces, ())

    def test_stop_at_final_vertex_and_shared_occurrence_boundary(self):
        route, state = case(
            (RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)), stops=(PlannedStop("s1", 100, 3),)
        )
        next_edge = replace(
            route.occurrences[0],
            index=1,
            shape=(RoadPoint(100, 0, 0), RoadPoint(200, 0, 0)),
            stops=(PlannedStop("s2", 0, 4),),
        )
        result = remaining_route(replace(route, occurrences=(*route.occurrences, next_edge)), state)
        self.assertEqual(sum(s.dwell_working_s for s in result.pieces[0].stops_after), 7)

    def test_known_missing_excessive_grade_and_outside_progress(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        with self.assertRaisesRegex(RouteUnavailable, "progress_outside"):
            remaining_route(route, replace(state, lane_offset_m=101))
        steep = replace(route.occurrences[0], shape=(RoadPoint(0, 0, 0), RoadPoint(100, 0, 50)))
        with self.assertRaisesRegex(RouteUnavailable, "grade_outside"):
            remaining_route(replace(route, occurrences=(steep,)), state)
        missing = replace(
            route.occurrences[0],
            shape=(RoadPoint(0, 0, None), RoadPoint(100, 0, None)),
            elevation_quality="missing",
        )
        with self.assertRaisesRegex(RouteUnavailable, "elevation_missing"):
            remaining_route(replace(route, occurrences=(missing,)), state)

    def test_piece_limit_accounts_for_stop_splits(self):
        route, state = case(
            (RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)), stops=(PlannedStop("s1", 40, 3),)
        )
        with self.assertRaisesRegex(RouteUnavailable, "piece_budget"):
            remaining_route(route, state, max_pieces=1)


if __name__ == "__main__":
    unittest.main()
