"""Fixed schedules: independent timing and continuous stop/movement checks."""

import unittest
from dataclasses import replace

from test_energy_route_contracts import vehicle
from test_energy_route_motion import motion_case
from test_energy_route_sumo import NETWORK

from evdt.energy.route_aware.contracts import FixedSignal, SignalWindow, StopRef
from evdt.energy.route_aware.geometry import RemainingStop, RouteUnavailable
from evdt.energy.route_aware.motion import plan_motion
from evdt.energy.route_aware.policy import ForecastPolicy
from evdt.energy.route_aware.signals import _extend_wait, plan_fixed_signals
from evdt.energy.route_aware.sumo_routes import read_network
from evdt.energy.route_aware.wire import canonical_bytes


class FixedSignalTests(unittest.TestCase):
    def test_extending_stationary_wait_matches_full_kinematic_recalculation(self):
        route, state = motion_case(1000)
        reference = StopRef(0, "declared-light")
        stop = RemainingStop(reference, 12)
        first = replace(route.pieces[0], distance_m=500, stops_after=(stop,))
        second = replace(route.pieces[0], distance_m=500, occurrence_index=1)
        route = replace(route, pieces=(first, second))
        policy = ForecastPolicy()
        original = plan_motion(route, state, vehicle(), policy)
        changed = replace(
            route,
            pieces=(replace(first, stops_after=(replace(stop, dwell_working_s=29),)), second),
        )
        expected = plan_motion(changed, state, vehicle(), policy)
        actual = _extend_wait(original, reference, 17, state, policy)
        self.assertEqual(actual, expected)
        self.assertEqual(actual.moving_working_s, original.moving_working_s)
        with self.assertRaisesRegex(RouteUnavailable, "motion_duration_budget_exceeded"):
            _extend_wait(original, reference, 14400, state, policy)

    def test_static_program_link_selection_and_unknown_programs(self):
        logic = b'<tlLogic id="j" type="static" offset="10"><phase duration="20" state="Gr"/><phase duration="40" state="rG"/></tlLogic>'
        raw = NETWORK.replace(b'via=":j_0"', b'via=":j_0" tl="j" linkIndex="1"').replace(
            b"</net>", logic + b"</net>"
        )
        network = read_network(raw)
        self.assertEqual(network.fixed_signals[("a_0", ":j_0")].wait(10), 20)
        for unknown in (
            raw.replace(b'type="static"', b'type="actuated"'),
            raw.replace(logic, logic + logic),
            raw.replace(logic, b""),
        ):
            with self.subTest(raw=unknown):
                parsed = read_network(unknown)
                self.assertIn(("a_0", ":j_0"), parsed.signal_connections)
                self.assertNotIn(("a_0", ":j_0"), parsed.fixed_signals)

    def test_optional_policy_fields_preserve_legacy_wire(self):
        raw = canonical_bytes(ForecastPolicy())
        self.assertNotIn(b"fixed_signal", raw)
        self.assertNotIn(b"signal_deceleration", raw)
        self.assertNotIn(b"sumo_yellow_clearance", raw)

    def test_network_import_distinguishes_yellow_from_red_and_red_yellow(self):
        logic = (
            b'<tlLogic id="j" type="static" offset="10">'
            b'<phase duration="20" state="Gr"/><phase duration="3" state="yr"/>'
            b'<phase duration="10" state="rG"/><phase duration="2" state="ur"/>'
            b"</tlLogic>"
        )
        raw = NETWORK.replace(b'via=":j_0"', b'via=":j_0" tl="j" linkIndex="0"').replace(
            b"</net>", logic + b"</net>"
        )
        key = ("a_0", ":j_0")
        legacy = read_network(raw).fixed_signals[key]
        signal = read_network(raw, include_yellow=True).fixed_signals[key]
        self.assertNotIn(b"yellow", canonical_bytes(legacy))
        self.assertEqual(signal.green, legacy.green)
        self.assertEqual(signal.yellow, (SignalWindow(20, 23),))
        self.assertTrue(signal.is_yellow(30))
        self.assertFalse(signal.is_yellow(33))
        self.assertFalse(signal.is_yellow(43))

    def test_yellow_clearance_preserves_observed_speed_and_requires_yellow(self):
        route, state = motion_case(100, speed=6)
        state = replace(state, working_s=10)
        first = replace(
            route.pieces[0],
            distance_m=2,
            fixed_signal=FixedSignal(
                "s", 40, 0, (SignalWindow(0, 10.1),), (SignalWindow(10.1, 12),)
            ),
            signal_controlled=True,
        )
        second = replace(route.pieces[0], distance_m=98, occurrence_index=1)
        route = replace(route, pieces=(first, second))
        policy = ForecastPolicy(
            fixed_signal_timing=True, signal_deceleration_m_s2=3, sumo_yellow_clearance=True
        )

        def project(current, observed, active_policy):
            return plan_fixed_signals(
                current,
                observed,
                vehicle(),
                active_policy,
                speed_factors=(1, 1),
                accelerations_m_s2=None,
            )

        plan = project(route, state, policy)
        self.assertIn("sumo_yellow_clearance_when_stop_infeasible", plan.assumptions)
        self.assertEqual(plan.phases[0].start_speed_m_s, state.speed_m_s)
        self.assertAlmostEqual(plan.distance_m, route.distance_m)
        self.assertEqual(plan.dwell_working_s, 0)

        for observed, active_policy in (
            (state, replace(policy, sumo_yellow_clearance=None)),
            (replace(state, working_s=12), policy),
        ):
            with self.assertRaisesRegex(RouteUnavailable, "current_speed_cannot_meet_plan"):
                project(route, observed, active_policy)

        can_stop = project(route, replace(state, speed_m_s=2), policy)
        self.assertGreater(can_stop.dwell_working_s, 0)
        self.assertNotIn("sumo_yellow_clearance_when_stop_infeasible", can_stop.assumptions)
        passenger_stop = RemainingStop(StopRef(0, "passenger-stop"), 15)
        route_with_stop = replace(
            route, pieces=(replace(first, stops_after=(passenger_stop,)), second)
        )
        with self.assertRaisesRegex(RouteUnavailable, "current_speed_cannot_meet_plan"):
            project(route_with_stop, state, policy)

    def test_declared_signal_braking_preserves_measured_speed(self):
        route, state = motion_case(1000)
        state = replace(state, speed_m_s=3.735)
        first = replace(
            route.pieces[0],
            distance_m=3,
            occurrence_index=0,
            fixed_signal=FixedSignal("s", 90, 0, (SignalWindow(40, 80),)),
            signal_controlled=True,
        )
        second = replace(route.pieces[0], distance_m=997, occurrence_index=1)
        route = replace(route, pieces=(first, second))
        policy = ForecastPolicy(fixed_signal_timing=True, signal_deceleration_m_s2=3)
        result = plan_fixed_signals(
            route, state, vehicle(), policy, speed_factors=(1, 1), accelerations_m_s2=None
        )
        self.assertEqual(result.phases[0].start_speed_m_s, state.speed_m_s)
        self.assertAlmostEqual(result.distance_m, 1000)
        self.assertTrue(any(p.acceleration_m_s2 == -3 for p in result.phases if p.piece_index == 0))
        self.assertTrue(
            all(
                p.acceleration_m_s2 >= -vehicle().max_deceleration_m_s2
                for p in result.phases
                if p.piece_index == 1
            )
        )
        with self.assertRaisesRegex(RouteUnavailable, "current_speed_cannot_meet_plan"):
            plan_fixed_signals(
                route,
                state,
                vehicle(),
                replace(policy, signal_deceleration_m_s2=None),
                speed_factors=(1, 1),
                accelerations_m_s2=None,
            )

    def test_cycle_boundaries_offset_and_wrapping(self):
        signal = FixedSignal("junction", 60, 10, (SignalWindow(20, 40),))
        for working, wait in ((10, 20), (29, 1), (30, 0), (49.9, 0), (50, 40), (90, 0)):
            with self.subTest(working=working):
                self.assertAlmostEqual(signal.wait(working), wait)

    def test_schedule_validation(self):
        for windows in ((), (SignalWindow(0, 61),), (SignalWindow(0, 20), SignalWindow(10, 30))):
            with self.subTest(windows=windows), self.assertRaises(ValueError):
                FixedSignal("s", 60, 0, windows)

    def test_red_stop_wait_distance_and_continuity(self):
        route, state = motion_case(1000)
        first = replace(
            route.pieces[0],
            distance_m=500,
            fixed_signal=FixedSignal("s", 100, 0, (SignalWindow(95, 99),)),
            signal_controlled=True,
        )
        second = replace(route.pieces[0], distance_m=500)
        route = replace(route, pieces=(first, second))
        result = plan_fixed_signals(
            route, state, vehicle(), ForecastPolicy(), speed_factors=(1, 1), accelerations_m_s2=None
        )
        crossing = max(
            p.start_working_s + p.duration_working_s for p in result.phases if p.piece_index == 0
        )
        self.assertAlmostEqual(first.fixed_signal.wait(crossing), 0)
        self.assertAlmostEqual(result.distance_m, 1000)
        self.assertGreater(result.dwell_working_s, 0)
        for before, after in zip(result.phases, result.phases[1:]):
            self.assertAlmostEqual(before.end_speed_m_s, after.start_speed_m_s)
            self.assertAlmostEqual(
                before.start_working_s + before.duration_working_s, after.start_working_s
            )

    def test_unknown_program_does_not_become_a_green_light(self):
        route, state = motion_case()
        route = replace(route, pieces=(replace(route.pieces[0], signal_controlled=True),))
        with self.assertRaisesRegex(RouteUnavailable, "fixed_signal_schedule_unavailable"):
            plan_fixed_signals(
                route,
                state,
                vehicle(),
                ForecastPolicy(),
                speed_factors=(1,),
                accelerations_m_s2=None,
            )


if __name__ == "__main__":
    unittest.main()
