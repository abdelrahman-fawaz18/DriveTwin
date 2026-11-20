"""Weather causality, declared HVAC loads, and factor-specific references."""

import math
import unittest
from dataclasses import replace

from test_energy_route_contracts import vehicle
from test_energy_route_ledger import conditions, constant_case
from test_energy_route_wire import request

from evdt.energy.route_aware.contracts import Curve, CurvePoint, Origin
from evdt.energy.route_aware.environment import (
    EquipmentProfile,
    TimeMapping,
    WeatherCell,
    equipment_demand,
    weather_conditions,
    wind_vector,
)
from evdt.energy.route_aware.ledger import integrate
from evdt.energy.route_aware.policy import ForecastPolicy


def equipment():
    return EquipmentProfile(
        "synthetic-equipment",
        vehicle().origin,
        3000,
        1000,
        500,
        1000,
        293.15,
        2,
        3,
        20_000,
        15_000,
        100,
    )


def weather():
    return WeatherCell(
        Origin("forecast", "test-provider", "issued-series-1"),
        90,
        91,
        100,
        160,
        -74,
        45,
        -73,
        46,
        10,
        270,
        10,
        "open terrain",
        280,
        100_000,
    )


class EnvironmentTests(unittest.TestCase):
    def test_direction_means_where_wind_comes_from(self):
        for bearing, expected in ((0, (0, -10)), (90, (-10, 0)), (180, (0, 10)), (270, (10, 0))):
            with self.subTest(bearing=bearing):
                actual = wind_vector(10, bearing)
                self.assertAlmostEqual(actual[0], expected[0])
                self.assertAlmostEqual(actual[1], expected[1])
        for speed, direction in ((-1, 0), (10, 361), (True, 0), (math.nan, 0)):
            with self.assertRaises(ValueError):
                wind_vector(speed, direction)

    def test_weather_has_explicit_time_location_and_issue_scope(self):
        base = replace(request().conditions[0], from_working_s=0, until_working_s=120)
        selected = weather_conditions(
            base,
            (weather(),),
            TimeMapping(0, 100, 0.5),
            latitude_deg=45.5,
            longitude_deg=-73.5,
            cutoff_real_life_s=100,
            max_issue_age_real_life_s=60,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].until_working_s, 120)
        self.assertAlmostEqual(selected[0].wind_east_m_s, 10)
        self.assertEqual(selected[0].wind_basis, "forecast")
        kwargs = dict(
            latitude_deg=45.5,
            longitude_deg=-73.5,
            cutoff_real_life_s=100,
            max_issue_age_real_life_s=60,
        )
        for cells, changes in (
            ((weather(),), {"longitude_deg": -72}),
            ((replace(weather(), available_real_life_s=101),), {}),
            ((weather(), weather()), {}),
            ((replace(weather(), valid_until_real_life_s=150),), {}),
            ((weather(),), {"max_issue_age_real_life_s": 5}),
        ):
            with self.assertRaises(ValueError):
                weather_conditions(base, cells, TimeMapping(0, 100, 0.5), **(kwargs | changes))
        with self.assertRaises(ValueError):
            replace(weather(), origin=Origin("observed", "test", "future-truth"))

    def test_equipment_accounts_for_doors_people_solar_and_power_caps(self):
        baseline = equipment_demand(
            equipment(),
            ambient_temperature_k=273.15,
            occupants=0,
            door_open_fraction=0,
            absorbed_solar_w=0,
        )
        self.assertAlmostEqual(baseline.total_electrical_w, 9000)  # 4k base + 10k thermal / 2
        doors = equipment_demand(
            equipment(),
            ambient_temperature_k=273.15,
            occupants=20,
            door_open_fraction=1,
            absorbed_solar_w=1000,
        )
        self.assertAlmostEqual(doors.total_electrical_w, 17_500)  # (30k - 2k - 1k) / 2 + 4k
        cold = equipment_demand(
            equipment(),
            ambient_temperature_k=233.15,
            occupants=0,
            door_open_fraction=1,
            absorbed_solar_w=0,
        )
        self.assertEqual(cold.total_electrical_w, 24_000)
        self.assertAlmostEqual(cold.unserved_heating_thermal_w, 50_000)
        hot = equipment_demand(
            equipment(),
            ambient_temperature_k=303.15,
            occupants=50,
            door_open_fraction=0,
            absorbed_solar_w=2000,
        )
        self.assertAlmostEqual(hot.total_electrical_w, 8000)  # 12k thermal / 3 + 4k

    def test_wind_density_and_mass_have_independent_closed_form_effects(self):
        route, plan, state = constant_case(distance=1000, speed=10)
        profile = vehicle()
        baseline = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        for east, north in ((-10, 0), (5, 0), (0, 10)):
            result = integrate(
                route,
                plan,
                state,
                profile,
                conditions(wind_east_m_s=east, wind_north_m_s=north, wind_basis="synthetic"),
                ForecastPolicy(),
            )
            air = (
                0.5 * profile.air_density_kg_m3 * profile.drag_coefficient * profile.frontal_area_m2
            )
            expected = air * math.hypot(10 - east, north) * (10 - east) * 1000
            self.assertAlmostEqual(result.air_work_j, expected, places=5)
        dense = integrate(
            route,
            plan,
            state,
            profile,
            conditions(ambient_temperature_k=250, pressure_pa=100_000),
            ForecastPolicy(),
        )
        self.assertAlmostEqual(
            dense.air_work_j / baseline.air_work_j,
            100_000 / (287.05 * 250) / profile.air_density_kg_m3,
        )
        loaded = integrate(
            route,
            plan,
            state,
            profile,
            conditions(loaded_mass_kg=profile.loaded_mass_kg + 1000),
            ForecastPolicy(),
        )
        delta = profile.rolling_coefficient * 1000 * profile.gravity_m_s2 * 1000
        self.assertAlmostEqual(
            loaded.net_battery_j - baseline.net_battery_j,
            delta / profile.propulsion_efficiency,
            places=5,
        )

    def test_forward_equipment_replaces_observed_total_only_after_near_horizon(self):
        route, plan, state = constant_case(distance=2000, speed=10, auxiliary=8000)
        profile = replace(vehicle(), drag_coefficient=0, rolling_coefficient=0)
        result = integrate(
            route, plan, state, profile, conditions(auxiliary_power_w=10_000), ForecastPolicy()
        )
        self.assertAlmostEqual(result.net_battery_j, 8000 * 120 + 10_000 * 80, places=5)

    def test_declared_cold_limits_preserve_energy_and_report_unserved_demand(self):
        route, plan, state = constant_case(distance=100, auxiliary=10_000, soc=0.1)
        curve = Curve("K", "1", (CurvePoint(250, 0.8), CurvePoint(300, 1)), vehicle().origin)
        profile = replace(
            vehicle(),
            drag_coefficient=0,
            rolling_coefficient=0,
            usable_fraction_by_temperature=curve,
        )
        state = replace(state, battery_temperature_k=250)
        result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        self.assertAlmostEqual(result.final_energy_j, result.initial_energy_j)
        self.assertAlmostEqual(result.unserved_energy_j, 100_000)
        self.assertFalse(result.motion_feasible)

    def test_efficiency_curve_changes_conversion_loss_only(self):
        route, plan, state = constant_case(distance=100, auxiliary=0)
        curve = Curve("W", "1", (CurvePoint(0, 0.5), CurvePoint(600_000, 0.5)), vehicle().origin)
        profile = replace(vehicle(), propulsion_by_power=curve)
        result = integrate(route, plan, state, profile, conditions(), ForecastPolicy())
        wheel = result.rolling_work_j + result.air_work_j
        self.assertAlmostEqual(result.propulsion_required_j, wheel * 2, places=5)
        self.assertAlmostEqual(result.drive_conversion_loss_j, wheel, places=5)
