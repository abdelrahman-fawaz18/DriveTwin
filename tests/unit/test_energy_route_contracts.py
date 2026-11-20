"""Domain rejection and physical-unit tests for the new version only."""

import json
import unittest
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

from evdt.energy.route_aware.contracts import (
    Conditions,
    Curve,
    CurvePoint,
    LiveState,
    Origin,
    PlannedStop,
    RoadPoint,
    RouteContext,
    RouteOccurrence,
    SourceScope,
    VehicleProfile,
)
from evdt.energy.route_aware.policy import ForecastPolicy
from evdt.energy.route_aware.validation import digest, integer, number, sequence, text


def vehicle() -> VehicleProfile:
    return VehicleProfile(
        profile_id="synthetic-bus/2.0.0",
        origin=Origin("synthetic", "analytical-tests", "declared-example"),
        capacity_kwh=350,
        reserve_soc=0.1,
        loaded_mass_kg=18_000,
        max_loaded_mass_kg=25_000,
        rotating_mass_kg=200,
        frontal_area_m2=8.16,
        drag_coefficient=0.7,
        rolling_coefficient=0.01,
        air_density_kg_m3=1.2,
        propulsion_efficiency=0.9,
        regeneration_efficiency=0.55,
        max_discharge_w=600_000,
        max_regeneration_w=250_000,
        max_acceleration_m_s2=1,
        max_deceleration_m_s2=1.5,
        gravity_m_s2=9.81,
    )


def occurrence(index: int = 0) -> RouteOccurrence:
    return RouteOccurrence(
        index=index,
        edge_id="revisited-street",
        lane_id="lane-0",
        shape=(RoadPoint(0, 0, 10), RoadPoint(100, 0, 15)),
        speed_limit_m_s=10,
        road_class="urban",
        structure="surface",
        elevation_quality="synthetic",
        stops=(PlannedStop("stop", 80, 20),),
    )


class ValidationTests(unittest.TestCase):
    def test_numeric_values_are_finite_and_bounded(self):
        for value in (True, False, "1", None, float("nan"), float("inf"), 10**1000, -1, 11):
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                number(value, "example", 0, 10)
        self.assertEqual(number(0, "example", 0, 10), 0)

    def test_integer_does_not_accept_bool_or_float(self):
        for value in (True, 1.0, 0, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                integer(value, "epoch", 1, 2)

    def test_identifiers_are_bounded_and_have_no_controls(self):
        for value in ("", " leading", "trailing ", "a\nb", "a\x7fb", "x" * 257):
            with self.subTest(value=value), self.assertRaises(ValueError):
                text(value, "identity")
        self.assertEqual(text("bus-001", "identity"), "bus-001")

    def test_digest_is_exact_lowercase_hex(self):
        for value in ("a" * 63, "A" * 64, "g" * 64, "a" * 64 + "\n", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                digest(value, "input")
        self.assertEqual(digest("a" * 64, "input"), "a" * 64)

    def test_mutable_or_wrong_type_sequences_are_rejected(self):
        for value in ([1], ("a",), (), (1, 2, 3)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sequence(value, "items", int, 1, 2)


class ProfileTests(unittest.TestCase):
    def test_valid_profile_is_immutable(self):
        profile = vehicle()
        with self.assertRaises(FrozenInstanceError):
            profile.capacity_kwh = 400
        self.assertEqual(asdict(profile)["capacity_kwh"], 350)

    def test_physical_impossibilities_are_rejected(self):
        for field, value in (
            ("capacity_kwh", 0),
            ("loaded_mass_kg", 26_000),
            ("rotating_mass_kg", -1),
            ("propulsion_efficiency", 1.01),
            ("regeneration_efficiency", -0.1),
            ("max_discharge_w", False),
            ("reserve_soc", float("nan")),
            ("origin", None),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(vehicle(), **{field: value})

    def test_curve_interpolation_and_domain(self):
        curve = Curve("K", "W", (CurvePoint(250, 0), CurvePoint(290, 100_000)), vehicle().origin)
        self.assertEqual(curve.at(250), 0)
        self.assertEqual(curve.at(270), 50_000)
        self.assertEqual(curve.at(290), 100_000)
        for value in (249, 291, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                curve.at(value)
        self.assertEqual(replace(vehicle(), discharge_by_temperature=curve).capacity_kwh, 350)

    def test_curve_order_units_and_limits(self):
        with self.assertRaises(ValueError):
            Curve("K", "W", (CurvePoint(270, 1), CurvePoint(270, 2)), vehicle().origin)
        wrong = Curve("K", "1", (CurvePoint(250, 0.1), CurvePoint(300, 0.9)), vehicle().origin)
        with self.assertRaises(ValueError):
            replace(vehicle(), discharge_by_temperature=wrong)
        too_large = Curve("1", "W", (CurvePoint(0, 300_000), CurvePoint(1, 0)), vehicle().origin)
        with self.assertRaises(ValueError):
            replace(vehicle(), regeneration_by_soc=too_large)
        bad_soc = replace(too_large, points=(CurvePoint(-0.1, 10), CurvePoint(1, 0)))
        with self.assertRaises(ValueError):
            replace(vehicle(), regeneration_by_soc=bad_soc)


class RouteTests(unittest.TestCase):
    def test_repeated_edges_remain_distinct_occurrences(self):
        context = RouteContext(
            "a" * 64,
            "revision-1",
            "synthetic-local-meters",
            "synthetic-sea-level",
            vehicle().origin,
            (occurrence(), occurrence(1)),
        )
        self.assertEqual([o.index for o in context.occurrences], [0, 1])
        with self.assertRaises(ValueError):
            replace(context, occurrences=(occurrence(1),))

    def test_ground_height_is_not_accepted_for_structures(self):
        for structure in ("bridge", "tunnel"):
            with self.subTest(structure=structure), self.assertRaises(ValueError):
                replace(occurrence(), structure=structure, elevation_quality="terrain")
        self.assertEqual(
            replace(occurrence(), structure="bridge", elevation_quality="verified_road").structure,
            "bridge",
        )

    def test_missing_height_cannot_be_labeled_known(self):
        shape = (RoadPoint(0, 0, None), RoadPoint(100, 0, None))
        with self.assertRaises(ValueError):
            replace(occurrence(), shape=shape)
        unknown = replace(occurrence(), shape=shape, elevation_quality="missing")
        self.assertIsNone(unknown.shape[0].elevation_m)
        with self.assertRaises(ValueError):
            replace(occurrence(), elevation_quality="missing")

    def test_duplicate_vertices_and_stop_order(self):
        with self.assertRaises(ValueError):
            replace(occurrence(), shape=(RoadPoint(0, 0, 1), RoadPoint(0, 0, 2)))
        for stops in (
            (PlannedStop("a", 90, 1), PlannedStop("b", 10, 1)),
            (PlannedStop("a", 10, 1), PlannedStop("a", 90, 1)),
        ):
            with self.subTest(stops=stops), self.assertRaises(ValueError):
                replace(occurrence(), stops=stops)


class StateAndConditionTests(unittest.TestCase):
    def test_zero_speed_charge_and_working_time_are_valid(self):
        state = LiveState(
            SourceScope("run", "bus", "source", 1, "session"),
            "event",
            "a" * 64,
            0,
            10,
            11,
            "running",
            "route-1",
            0,
            0,
            0,
            0,
            0,
            "not_charging",
        )
        self.assertEqual(state.speed_m_s, 0)
        with self.assertRaises(ValueError):
            replace(state, source_state="connected")
        with self.assertRaises(ValueError):
            replace(state.scope, source_epoch=True)

    def test_wind_has_direction_and_explicit_basis(self):
        conditions = Conditions(vehicle().origin, 0, 0, 100)
        self.assertEqual(conditions.wind_basis, "declared_calm")
        with self.assertRaises(ValueError):
            replace(conditions, wind_east_m_s=10)
        directional = replace(conditions, wind_east_m_s=-10, wind_basis="synthetic")
        self.assertEqual(directional.wind_east_m_s, -10)
        with self.assertRaises(ValueError):
            replace(directional, wind_east_m_s=50, wind_north_m_s=50)

    def test_invalid_conditions_and_empty_validity_are_rejected(self):
        conditions = Conditions(vehicle().origin, 0, 0, 100)
        for name, value in (
            ("until_working_s", 0),
            ("pressure_pa", 1),
            ("ambient_temperature_k", -10),
            ("loaded_mass_kg", 0),
            ("rolling_multiplier", -1),
            ("speed_factor", 2),
            ("extra_auxiliary_power_w", -1),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(conditions, **{name: value})


class PolicyTests(unittest.TestCase):
    def test_resource_policy_agrees_with_domain_ceilings(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "qualification/energy/route-aware/runtime-policy.json"
        )
        value = json.loads(path.read_bytes())
        policy = ForecastPolicy()
        self.assertEqual(value["worker"]["timeout_real_life_s"], policy.worker_deadline_real_life_s)
        self.assertEqual(value["worker"]["max_request_bytes"], policy.max_request_bytes)
        self.assertEqual(value["worker"]["max_integration_steps"], policy.max_integration_steps)
        self.assertEqual(value["controller"]["period_working_s"], policy.period_working_s)
        self.assertEqual(value["qualification"]["live_windows_reserve_bytes"], 2 * 1024**3)

    def test_caller_cannot_increase_worker_limits(self):
        for name, value in (
            ("worker_deadline_real_life_s", 31),
            ("max_request_bytes", 4_194_305),
            ("max_integration_steps", 100_001),
            ("max_segments", 10_001),
            ("integration_step_working_s", 1),
            ("lease_real_life_s", 20),
            ("forecast_max_age_working_s", 20),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                replace(ForecastPolicy(), **{name: value})


if __name__ == "__main__":
    unittest.main()
