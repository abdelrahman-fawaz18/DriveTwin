"""Occurrence-local assumptions, stop transitions, and equipment integration."""

import unittest
from dataclasses import replace

from test_energy_route_environment import equipment
from test_energy_route_wire import request

from evdt.energy.route_aware.contracts import PlannedStop, RoadPoint, StopRef
from evdt.energy.route_aware.forecast import _motion, forecast
from evdt.energy.route_aware.geometry import remaining_route
from evdt.energy.route_aware.ledger import integrate
from evdt.energy.route_aware.scenarios import (
    RoadConditions,
    RouteScenario,
    StopEffect,
    apply_stop_assumptions,
    conditions_by_road_class,
)
from evdt.energy.route_aware.scheduling import context_key
from evdt.energy.route_aware.wire import canonical_bytes, parse_request


def scenario_case():
    job = request()
    first = replace(
        job.route.occurrences[0],
        shape=(RoadPoint(0, 0, 0), RoadPoint(1000, 0, 0)),
        stops=(PlannedStop("boarding", 1000, 20),),
        reference_length_m=1000,
        speed_limit_m_s=10,
        road_class="urban",
    )
    second = replace(
        first,
        index=1,
        edge_id="b",
        lane_id="b_0",
        stops=(),
        shape=(RoadPoint(1000, 0, 0), RoadPoint(2000, 0, 0)),
        road_class="arterial",
    )
    return replace(
        job,
        state=replace(job.state, lane_offset_m=0, speed_m_s=0),
        route=replace(job.route, occurrences=(first, second)),
        conditions=(
            replace(job.conditions[0], auxiliary_power_w=8000, ambient_temperature_k=273.15),
        ),
    )


def scenario(job, **kwargs):
    return RouteScenario("declared-case", job.vehicle.origin, 90, **kwargs)


class ScenarioTests(unittest.TestCase):
    def test_road_class_speed_and_surface_are_local_and_roundtrip(self):
        job = scenario_case()
        local = replace(job.conditions[0], speed_factor=0.5, rolling_multiplier=1.3)
        planned = scenario(
            job,
            roads=conditions_by_road_class(job.route, road_class="arterial", intervals=(local,)),
        )
        changed = replace(job, scenario=planned)
        self.assertEqual(parse_request(canonical_bytes(changed)), changed)
        baseline, result = forecast(job), forecast(changed)
        self.assertEqual(result.status, "available")
        self.assertGreater(result.summary.duration_working_s, baseline.summary.duration_working_s)
        expected = (
            job.vehicle.rolling_coefficient
            * job.vehicle.loaded_mass_kg
            * job.vehicle.gravity_m_s2
            * 1000
            * 0.3
        )
        self.assertAlmostEqual(
            result.summary.ledger.rolling_work_j - baseline.summary.ledger.rolling_work_j,
            expected,
            places=5,
        )
        self.assertNotEqual(context_key(job), context_key(changed))

    def test_local_wind_affects_only_selected_road(self):
        job = scenario_case()
        local = replace(job.conditions[0], wind_east_m_s=-10, wind_basis="synthetic")
        changed = replace(job, scenario=scenario(job, roads=(RoadConditions(1, (local,)),)))
        route = remaining_route(job.route, job.state)
        baseline_plan, local_plan = _motion(job, route), _motion(changed, route)
        self.assertEqual(baseline_plan, local_plan)
        baseline = forecast(job).summary.ledger
        result = forecast(changed).summary.ledger
        self.assertGreater(result.air_work_j, baseline.air_work_j)
        self.assertAlmostEqual(result.rolling_work_j, baseline.rolling_work_j)
        self.assertAlmostEqual(result.auxiliary_required_j, baseline.auxiliary_required_j)

    def test_passenger_mass_changes_at_departure_and_persists(self):
        job = scenario_case()
        effect = StopEffect(StopRef(0, "boarding"), loaded_mass_on_departure_kg=20_000)
        changed = replace(job, scenario=scenario(job, stops=(effect,)))
        baseline, result = forecast(job), forecast(changed)
        expected = (
            (20_000 - job.vehicle.loaded_mass_kg)
            * job.vehicle.gravity_m_s2
            * job.vehicle.rolling_coefficient
            * 1000
        )
        self.assertEqual(result.status, "available")
        self.assertAlmostEqual(
            result.summary.ledger.rolling_work_j - baseline.summary.ledger.rolling_work_j,
            expected,
            places=5,
        )
        # A later forecast retains the declared completed boarding event.
        progressed = replace(
            changed,
            state=replace(
                changed.state,
                occurrence_index=1,
                completed_stops=(effect.reference,),
                working_s=200,
            ),
        )
        result = forecast(progressed)
        self.assertEqual(result.status, "available")
        self.assertAlmostEqual(
            result.summary.ledger.rolling_work_j,
            20_000 * job.vehicle.gravity_m_s2 * job.vehicle.rolling_coefficient * 1000,
            places=5,
        )

    def test_dwell_extension_consumes_declared_equipment_energy(self):
        job = scenario_case()
        effect = StopEffect(StopRef(0, "boarding"), additional_dwell_working_s=30)
        changed = replace(job, scenario=scenario(job, stops=(effect,)))
        baseline, result = forecast(job), forecast(changed)
        self.assertAlmostEqual(
            result.summary.duration_working_s - baseline.summary.duration_working_s, 30
        )
        self.assertAlmostEqual(
            result.summary.ledger.net_battery_j - baseline.summary.ledger.net_battery_j,
            30 * 8000,
            places=4,
        )

    def test_equipment_model_replaces_far_aggregate_with_doors_and_solar(self):
        job = scenario_case()
        effect = StopEffect(StopRef(0, "boarding"), occupants_on_departure=30, door_open_fraction=1)
        planned = scenario(
            job, stops=(effect,), equipment=equipment(), initial_occupants=10, absorbed_solar_w=1000
        )
        changed = replace(
            job, scenario=planned, policy=replace(job.policy, near_horizon_working_s=1)
        )
        result = forecast(changed)
        self.assertEqual(result.status, "available")
        self.assertIn("far_horizon_equipment_profile_aggregate", result.summary.ledger.assumptions)
        # Removing a modeled source lowers heating power; it is never added to
        # the existing 8kW aggregate as a second copy of the same equipment.
        self.assertLess(
            result.summary.ledger.auxiliary_required_j,
            (8000 + 9000) * result.summary.duration_working_s,
        )
        missing = replace(
            changed, conditions=(replace(changed.conditions[0], ambient_temperature_k=None),)
        )
        self.assertEqual(forecast(missing).reason, "equipment_model_ambient_temperature_missing")

    def test_closure_demands_confirmed_route_change(self):
        job = scenario_case()
        changed = replace(job, scenario=scenario(job, closed_occurrences=(1,)))
        self.assertEqual(
            forecast(changed).reason,
            "assigned_route_contains_closed_road_requires_confirmed_detour",
        )

    def test_invalid_coverage_future_source_and_stop_identity_rejected(self):
        job = scenario_case()
        for planned in (
            scenario(job, roads=(RoadConditions(9, job.conditions),)),
            scenario(job, stops=(StopEffect(StopRef(0, "missing")),)),
            replace(scenario(job), available_real_life_s=101),
            scenario(
                job, stops=(StopEffect(StopRef(0, "boarding"), loaded_mass_on_departure_kg=99_000),)
            ),
        ):
            with self.assertRaises(ValueError):
                replace(job, scenario=planned)
        with self.assertRaises(ValueError):
            scenario(job, absorbed_solar_w=1000)
        with self.assertRaises(ValueError):
            replace(
                job,
                scenario=scenario(job, equipment=equipment()),
                conditions=(replace(job.conditions[0], extra_auxiliary_power_w=1000),),
            )

    def test_current_stop_keeps_source_remaining_dwell(self):
        job = scenario_case()
        effect = StopEffect(StopRef(0, "boarding"), additional_dwell_working_s=100)
        job = replace(
            job,
            scenario=scenario(job, stops=(effect,)),
            state=replace(
                job.state,
                lane_offset_m=1000,
                current_stop=effect.reference,
                remaining_dwell_working_s=5,
            ),
        )
        route = apply_stop_assumptions(remaining_route(job.route, job.state), job.scenario)
        self.assertEqual(route.initial_stops[0].dwell_working_s, 5)
        motion = _motion(job, route)
        result = integrate(
            route, motion, job.state, job.vehicle, job.conditions, job.policy, scenario=job.scenario
        )
        self.assertTrue(result.motion_feasible)
