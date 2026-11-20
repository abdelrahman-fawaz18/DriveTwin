"""Closed-form physics and conservation checks for a separately supplied plan."""

import math
import unittest
from dataclasses import replace

from test_energy_route_contracts import vehicle
from test_energy_route_geometry import case
from test_energy_route_motion import motion_case
from test_energy_route_wire import request

from evdt.energy.route_aware.contracts import RoadPoint
from evdt.energy.route_aware.geometry import RouteUnavailable, remaining_route
from evdt.energy.route_aware.ledger import StableSum, integrate
from evdt.energy.route_aware.motion import MotionPhase, MotionPlan, plan_motion, prefix_plan
from evdt.energy.route_aware.policy import ForecastPolicy


def constant_case(*, rise=0, speed=10, distance=100, auxiliary=0, soc=0.5):
    context, state = case((RoadPoint(0, 0, 0), RoadPoint(distance, 0, rise)))
    state = replace(state, speed_m_s=speed, auxiliary_power_w=auxiliary, soc=soc)
    route = remaining_route(context, state)
    duration = route.distance_m / speed
    phase = MotionPhase(0, "cruise", state.working_s, duration, route.distance_m, speed, speed, 0)
    plan = MotionPlan((phase,), route.distance_m, duration, 0, duration, ())
    return route, plan, state


def conditions(**kwargs):
    return (replace(request().conditions[0], **kwargs),)


class LedgerTests(unittest.TestCase):
    def test_flat_cruise_matches_closed_form(self):
        route, plan, state = constant_case(auxiliary=8000)
        profile = vehicle()
        result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        rolling = profile.rolling_coefficient * profile.loaded_mass_kg * profile.gravity_m_s2 * 100
        drag = (
            0.5
            * profile.air_density_kg_m3
            * profile.drag_coefficient
            * profile.frontal_area_m2
            * 10**2
            * 100
        )
        expected = (rolling + drag) / profile.propulsion_efficiency + 8000 * 10
        self.assertAlmostEqual(result.net_battery_j, expected, places=6)
        self.assertAlmostEqual(result.rolling_work_j, rolling, places=6)
        self.assertAlmostEqual(result.air_work_j, drag, places=6)
        self.assertAlmostEqual(result.final_energy_j, result.initial_energy_j - expected, places=5)
        self.assertTrue(result.motion_feasible)

    def test_uphill_and_downhill_gravitational_work(self):
        profile = replace(vehicle(), drag_coefficient=0, rolling_coefficient=0)
        for rise in (5, -5):
            with self.subTest(rise=rise):
                route, plan, state = constant_case(rise=rise)
                result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
                work = profile.loaded_mass_kg * profile.gravity_m_s2 * rise
                self.assertAlmostEqual(result.grade_work_j, work, places=5)
                expected = (
                    work / profile.propulsion_efficiency
                    if work > 0
                    else work * profile.regeneration_efficiency
                )
                self.assertAlmostEqual(result.net_battery_j, expected, places=5)

    def test_acceleration_and_braking_close_without_resistance(self):
        route, state = motion_case(100)
        state = replace(state, auxiliary_power_w=0)
        profile = replace(
            vehicle(),
            drag_coefficient=0,
            rolling_coefficient=0,
            propulsion_efficiency=1,
            regeneration_efficiency=1,
            max_discharge_w=5_000_000,
            max_regeneration_w=5_000_000,
        )
        plan = plan_motion(route, state, profile, ForecastPolicy())
        result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        self.assertAlmostEqual(result.net_battery_j, 0, places=5)
        self.assertAlmostEqual(result.acceleration_work_j, 0, places=5)
        self.assertGreater(result.battery_draw_j, 0)
        self.assertGreater(result.battery_recovery_j, 0)

    def test_euler_braking_keeps_position_and_kinetic_energy_separate(self):
        route, state = motion_case(2.71, speed=3)
        state = replace(state, auxiliary_power_w=0)
        profile = replace(
            vehicle(), max_deceleration_m_s2=1.5, drag_coefficient=0, rolling_coefficient=0
        )
        policy = ForecastPolicy(sumo_euler_step_working_s=0.2)
        plan = plan_motion(route, state, profile, policy)
        self.assertAlmostEqual(plan.distance_m, 2.71)
        first_half = prefix_plan(plan, 0.1)
        self.assertAlmostEqual(first_half.distance_m, 0.27)
        braking = prefix_plan(plan, 2)
        self.assertAlmostEqual(braking.distance_m, 2.7)
        self.assertAlmostEqual(braking.phases[-1].end_speed_m_s, 0)
        self.assertAlmostEqual(braking.phases[-1].distance_m, 0)
        mass = profile.loaded_mass_kg + profile.rotating_mass_kg
        for step in (0.2, 0.1):
            result = integrate(
                route,
                braking,
                state,
                profile,
                conditions(),
                replace(policy, integration_step_working_s=step),
            )
            self.assertAlmostEqual(result.acceleration_work_j, -0.5 * mass * 3**2)
            self.assertAlmostEqual(result.energy_balance_error_j, 0)

    def test_full_battery_rejects_recovery_but_can_feed_equipment(self):
        profile = replace(vehicle(), drag_coefficient=0, rolling_coefficient=0)
        route, plan, state = constant_case(rise=-5, soc=1, auxiliary=1000)
        result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        self.assertAlmostEqual(result.net_battery_j, 0)
        self.assertAlmostEqual(result.regeneration_used_j, result.auxiliary_required_j)
        self.assertGreater(result.rejected_regeneration_j, 0)
        self.assertAlmostEqual(result.final_energy_j, result.capacity_j)

    def test_power_limited_plan_is_infeasible(self):
        route, plan, state = constant_case(auxiliary=20_000)
        profile = replace(
            vehicle(), drag_coefficient=0, rolling_coefficient=0, max_discharge_w=8000
        )
        result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        self.assertAlmostEqual(result.battery_draw_j, 80_000)
        self.assertAlmostEqual(result.unserved_energy_j, 120_000)
        self.assertFalse(result.motion_feasible)

    def test_later_recovery_does_not_hide_depletion_or_reserve_breach(self):
        context, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 20), RoadPoint(200, 0, 0)))
        state = replace(state, soc=0.05, speed_m_s=10, auxiliary_power_w=0)
        route = remaining_route(context, state)
        duration = route.pieces[0].distance_m / 10
        plan = MotionPlan(
            tuple(
                MotionPhase(
                    i, "cruise", state.working_s + i * duration, duration, p.distance_m, 10, 10, 0
                )
                for i, p in enumerate(route.pieces)
            ),
            route.distance_m,
            2 * duration,
            0,
            2 * duration,
            (),
        )
        profile = replace(
            vehicle(),
            capacity_kwh=1,
            drag_coefficient=0,
            rolling_coefficient=0,
            reserve_soc=0.1,
            max_regeneration_w=5_000_000,
            max_discharge_w=5_000_000,
        )
        result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        self.assertAlmostEqual(result.minimum_energy_j, 0, places=5)
        self.assertGreater(result.final_energy_j / result.capacity_j, 0.1)
        self.assertTrue(result.reserve_breached)
        self.assertFalse(result.motion_feasible)

    def test_long_horizon_requires_forward_equipment_assumption(self):
        route, plan, state = constant_case(distance=3000, auxiliary=8000)
        profile = replace(vehicle(), drag_coefficient=0, rolling_coefficient=0)
        with self.assertRaisesRegex(RouteUnavailable, "forward_auxiliary_assumption_missing"):
            integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        result = integrate(
            route, plan, state, profile, conditions(auxiliary_power_w=4000), ForecastPolicy()
        )
        self.assertAlmostEqual(result.auxiliary_required_j, 120 * 8000 + 180 * 4000, places=4)

    def test_wind_vector_preserves_sign_and_crosswind(self):
        route, plan, state = constant_case()
        profile = replace(vehicle(), rolling_coefficient=0)
        energies = []
        for east, north in ((0, 0), (-5, 0), (5, 0), (20, 0), (0, 5)):
            result = integrate(
                route,
                plan,
                state,
                profile,
                conditions(wind_east_m_s=east, wind_north_m_s=north, wind_basis="synthetic"),
                ForecastPolicy(),
            )
            expected = (
                0.5
                * profile.air_density_kg_m3
                * profile.drag_coefficient
                * profile.frontal_area_m2
                * math.hypot(10 - east, north)
                * (10 - east)
                * 100
            )
            self.assertAlmostEqual(result.air_work_j, expected, places=5)
            energies.append(result.air_work_j)
        self.assertGreater(energies[1], energies[0])
        self.assertLess(energies[2], energies[0])
        self.assertLess(energies[3], 0)
        self.assertGreater(energies[4], energies[0])

    def test_no_future_gap_fill_and_step_budget(self):
        route, plan, state = constant_case()
        with self.assertRaisesRegex(RouteUnavailable, "conditions_do_not_cover_plan"):
            integrate(
                route,
                plan,
                state,
                vehicle(),
                conditions(until_working_s=state.working_s + 1),
                ForecastPolicy(),
            )
        with self.assertRaisesRegex(RouteUnavailable, "step_budget"):
            integrate(
                route,
                plan,
                state,
                vehicle(),
                conditions(),
                replace(ForecastPolicy(), max_integration_steps=2),
            )

    def test_convergence_on_acceleration_drag(self):
        route, state = motion_case()
        policy = ForecastPolicy()
        plan = plan_motion(route, state, vehicle(), policy)
        coarse = integrate(route, plan, state, vehicle(), conditions(), policy)
        fine = integrate(
            route,
            plan,
            state,
            vehicle(),
            conditions(),
            replace(policy, integration_step_working_s=0.1),
        )
        self.assertLess(
            abs(coarse.net_battery_j - fine.net_battery_j), max(1, abs(fine.net_battery_j) * 0.01)
        )

    def test_stable_sum_keeps_small_contributions(self):
        total = StableSum()
        for value in (1e16, 1, -1e16):
            total.add(value)
        self.assertEqual(total.value, 1)


if __name__ == "__main__":
    unittest.main()
