"""Analytical movement checks independent of the energy integrator."""

import math
import unittest
from dataclasses import replace

from test_energy_route_contracts import vehicle
from test_energy_route_geometry import case

from evdt.energy.route_aware.contracts import PlannedStop, RoadPoint, StopRef
from evdt.energy.route_aware.geometry import RouteUnavailable, remaining_route
from evdt.energy.route_aware.motion import plan_motion
from evdt.energy.route_aware.policy import ForecastPolicy


def motion_case(length=1000, *, speed=0, limit=10, stops=()):
    context, state = case((RoadPoint(0, 0, 0), RoadPoint(length, 0, 0)), stops=stops)
    context = replace(
        context, occurrences=(replace(context.occurrences[0], speed_limit_m_s=limit),)
    )
    state = replace(state, speed_m_s=speed)
    return remaining_route(context, state), state


class MotionTests(unittest.TestCase):
    def test_minor_junction_preserves_distance_and_recovers_after_visibility_point(self):
        context, state = case((RoadPoint(0, 0, 0), RoadPoint(500, 0, 0)))
        context = replace(
            context, occurrences=(replace(context.occurrences[0], yield_visibility_m=4.5),)
        )
        profile = replace(vehicle(), max_acceleration_m_s2=1, max_deceleration_m_s2=1.5)
        route = remaining_route(context, state)
        self.assertAlmostEqual(route.pieces[0].distance_m, 495.5)
        self.assertEqual(route.pieces[0].yield_visibility_m, 4.5)
        # Add a downstream road so that arrival braking does not obscure the yield.
        road = replace(route.pieces[-1], distance_m=500)
        route = replace(route, pieces=(*route.pieces, road), distance_m=1000)
        motion = plan_motion(route, state, profile, ForecastPolicy())
        at_visibility = [p for p in motion.phases if p.piece_index == 0][-1]
        self.assertAlmostEqual(at_visibility.end_speed_m_s, math.sqrt(13.5))
        self.assertAlmostEqual(motion.distance_m, 1000)
        self.assertGreater(
            next(p for p in motion.phases if p.piece_index == 1).end_speed_m_s,
            at_visibility.end_speed_m_s,
        )
        past = remaining_route(context, replace(state, lane_offset_m=498))
        self.assertTrue(all(p.yield_visibility_m is None for p in past.pieces))

    def test_trapezoid_distance_duration_and_continuity(self):
        route, state = motion_case()
        profile = replace(vehicle(), max_acceleration_m_s2=1, max_deceleration_m_s2=1)
        result = plan_motion(route, state, profile, ForecastPolicy())
        self.assertEqual([p.kind for p in result.phases], ["accelerate", "cruise", "brake"])
        self.assertEqual([p.distance_m for p in result.phases], [50, 900, 50])
        self.assertEqual(result.duration_working_s, 110)
        for before, after in zip(result.phases, result.phases[1:]):
            self.assertEqual(before.end_speed_m_s, after.start_speed_m_s)
            self.assertAlmostEqual(
                before.start_working_s + before.duration_working_s, after.start_working_s
            )

    def test_short_triangle_never_reaches_limit(self):
        route, state = motion_case(10)
        profile = replace(vehicle(), max_acceleration_m_s2=1, max_deceleration_m_s2=1)
        result = plan_motion(route, state, profile, ForecastPolicy())
        self.assertEqual(len(result.phases), 2)
        self.assertAlmostEqual(result.phases[0].end_speed_m_s, math.sqrt(10))
        self.assertAlmostEqual(result.duration_working_s, 2 * math.sqrt(10))
        self.assertAlmostEqual(result.distance_m, 10)

    def test_stop_dwell_and_departure(self):
        route, state = motion_case(stops=(PlannedStop("s1", 400, 17),))
        result = plan_motion(route, state, vehicle(), ForecastPolicy())
        dwell = next(p for p in result.phases if p.kind == "dwell")
        self.assertEqual(dwell.stop, StopRef(0, "s1"))
        self.assertEqual(dwell.duration_working_s, 17)
        self.assertEqual(result.dwell_working_s, 17)
        self.assertAlmostEqual(result.distance_m, 1000)
        for before, after in zip(result.phases, result.phases[1:]):
            self.assertAlmostEqual(before.end_speed_m_s, after.start_speed_m_s)

    def test_braking_envelope_preserves_observed_speed_or_rejects(self):
        route, state = motion_case(1, speed=10)
        for step in (None, 0.2):
            with (
                self.subTest(step=step),
                self.assertRaisesRegex(RouteUnavailable, "current_speed_cannot_meet_plan"),
            ):
                plan_motion(route, state, vehicle(), ForecastPolicy(sumo_euler_step_working_s=step))
        self.assertEqual(state.speed_m_s, 10)

    def test_euler_braking_obeys_limit_on_first_step_into_lower_speed_road(self):
        route, state = motion_case(100, speed=6, limit=8)
        road = route.pieces[0]
        route = replace(
            route,
            pieces=(replace(road, distance_m=12), replace(road, distance_m=88, speed_limit_m_s=2)),
        )
        profile = replace(vehicle(), max_deceleration_m_s2=1)
        policy = ForecastPolicy(sumo_euler_step_working_s=1)
        plan = plan_motion(route, state, profile, policy)
        self.assertEqual([p.distance_m for p in plan.phases[:3]], [5, 4, 3])
        self.assertEqual(plan.phases[2].end_speed_m_s, 3)
        self.assertEqual(plan.phases[3].position_rate_m_s, 2)
        self.assertAlmostEqual(plan.distance_m, 100)
        self.assertTrue(all(p.acceleration_m_s2 >= -1 - 1e-8 for p in plan.phases))

    def test_euler_braking_uses_source_length_and_preserves_physical_stop(self):
        context, state = case((RoadPoint(0, 0, 0), RoadPoint(99, 0, 0.5)))
        lane = replace(
            context.occurrences[0], reference_length_m=100, stops=(PlannedStop("s1", 6, 12),)
        )
        state = replace(state, speed_m_s=4)
        profile = replace(vehicle(), max_deceleration_m_s2=1)
        route = remaining_route(replace(context, occurrences=(lane,)), state)
        policy = ForecastPolicy(sumo_euler_step_working_s=1)

        with self.assertRaisesRegex(RouteUnavailable, "current_speed_cannot_meet_plan"):
            plan_motion(route, state, profile, policy)
        policy = replace(policy, sumo_braking_source_distance=True)
        plan = plan_motion(route, state, profile, policy)
        dwell_index = next(
            i for i, phase in enumerate(plan.phases) if phase.stop == StopRef(0, "s1")
        )
        self.assertEqual(plan.phases[dwell_index - 1].end_speed_m_s, 0)
        self.assertEqual(plan.phases[dwell_index].duration_working_s, 12)
        self.assertAlmostEqual(
            sum(p.distance_m for p in plan.phases[:dwell_index]), route.pieces[0].distance_m
        )
        self.assertAlmostEqual(plan.distance_m, route.distance_m)
        with self.assertRaisesRegex(RouteUnavailable, "current_speed_cannot_meet_plan"):
            plan_motion(route, replace(state, speed_m_s=4.01), profile, policy)

    def test_traffic_fraction_reduces_cruising_speed(self):
        route, state = motion_case()
        fast = plan_motion(route, state, vehicle(), ForecastPolicy())
        slow = plan_motion(route, state, vehicle(), ForecastPolicy(), speed_factors=(0.5,))
        self.assertGreater(slow.duration_working_s, fast.duration_working_s)
        self.assertLessEqual(max(p.end_speed_m_s for p in slow.phases), 5)
        self.assertEqual(slow.distance_m, fast.distance_m)

    def test_observed_speed_brakes_into_new_traffic_assumption(self):
        route, state = motion_case(1000, speed=10, limit=10)
        profile = replace(vehicle(), max_deceleration_m_s2=1)
        result = plan_motion(route, state, profile, ForecastPolicy(), speed_factors=(0.5,))
        first = result.phases[0]
        self.assertEqual(first.kind, "brake")
        self.assertEqual((first.start_speed_m_s, first.end_speed_m_s), (10, 5))
        self.assertEqual(first.distance_m, 37.5)
        self.assertEqual(first.duration_working_s, 5)
        self.assertAlmostEqual(result.distance_m, 1000)
        self.assertIn("observed_speed_brakes_toward_assumed_motion", result.assumptions)
        for before, after in zip(result.phases, result.phases[1:]):
            self.assertAlmostEqual(before.end_speed_m_s, after.start_speed_m_s)

    def test_corner_comfort_recovery_preserves_hard_stop_and_acceleration(self):
        context, state = case((RoadPoint(0, 0, 0), RoadPoint(20, 0, 0), RoadPoint(20, 100, 0)))
        context = replace(
            context, occurrences=(replace(context.occurrences[0], speed_limit_m_s=12),)
        )
        state = replace(state, speed_m_s=10)
        profile = replace(vehicle(), max_deceleration_m_s2=1)
        result = plan_motion(remaining_route(context, state), state, profile, ForecastPolicy())
        self.assertEqual(result.phases[0].start_speed_m_s, 10)
        self.assertEqual(result.phases[0].kind, "brake")
        self.assertEqual(result.phases[-1].end_speed_m_s, 0)
        self.assertAlmostEqual(result.distance_m, 120)
        self.assertTrue(all(p.acceleration_m_s2 >= -1 for p in result.phases))
        self.assertIn("observed_speed_brakes_toward_assumed_motion", result.assumptions)

    def test_legal_speed_drop_remains_a_hard_constraint(self):
        route, state = motion_case(1000, speed=12, limit=10)
        with self.assertRaisesRegex(RouteUnavailable, "current_speed_cannot_meet_plan"):
            plan_motion(route, state, vehicle(), ForecastPolicy())

    def test_curvature_caps_corner_speed(self):
        context, state = case((RoadPoint(0, 0, 0), RoadPoint(10, 0, 0), RoadPoint(10, 10, 0)))
        route = remaining_route(context, state)
        result = plan_motion(route, state, vehicle(), ForecastPolicy())
        final_first = [p for p in result.phases if p.piece_index == 0][-1]
        self.assertLessEqual(final_first.end_speed_m_s, math.sqrt(10 / (math.pi / 2)))

    def test_initial_remaining_dwell(self):
        context, state = case(
            (RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)), stops=(PlannedStop("s", 0, 20),)
        )
        state = replace(state, current_stop=StopRef(0, "s"), remaining_dwell_working_s=3)
        result = plan_motion(remaining_route(context, state), state, vehicle(), ForecastPolicy())
        self.assertEqual(result.phases[0].kind, "dwell")
        self.assertEqual(result.dwell_working_s, 3)

    def test_duration_budget_and_invalid_factors(self):
        route, state = motion_case()
        with self.assertRaisesRegex(RouteUnavailable, "duration_budget"):
            plan_motion(route, state, vehicle(), replace(ForecastPolicy(), max_plan_working_s=10))
        with self.assertRaises(ValueError):
            plan_motion(route, state, vehicle(), ForecastPolicy(), speed_factors=(0,))
        with self.assertRaises(ValueError):
            plan_motion(route, state, vehicle(), ForecastPolicy(), speed_factors=())


if __name__ == "__main__":
    unittest.main()
