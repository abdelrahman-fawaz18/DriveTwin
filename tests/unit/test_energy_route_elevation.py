"""Terrain interpolation, coverage and provenance tests with independent grids."""

import unittest
from dataclasses import replace

from test_energy_route_geometry import case

from evdt.energy.route_aware.contracts import RoadPoint
from evdt.energy.route_aware.elevation import (
    ElevationGrid,
    ElevationMetadata,
    densify,
    enrich_route,
)
from evdt.energy.route_aware.geometry import remaining_route


def grid():
    return ElevationGrid(
        ElevationMetadata(
            "synthetic-grid",
            "analytical-plane",
            "a" * 64,
            "local-east-north-meters",
            "synthetic-height",
            50,
            "2026-11-12",
            "MIT",
            "synthetic",
            "synthetic",
        ),
        3,
        2,
        0,
        0,
        50,
        50,
        (0, 5, 0, 0, 5, 0),
    )


class ElevationTests(unittest.TestCase):
    def test_bilinear_interpolation_and_boundaries(self):
        terrain = grid()
        self.assertEqual(terrain.sample(0, 0), 0)
        self.assertEqual(terrain.sample(25, 25), 2.5)
        self.assertEqual(terrain.sample(50, 50), 5)
        self.assertEqual(terrain.sample(100, 0), 0)
        self.assertIsNone(terrain.sample(101, 0))

    def test_negative_row_direction(self):
        terrain = replace(grid(), origin_y_m=50, step_y_m=-50)
        self.assertEqual(terrain.sample(25, 25), 2.5)
        self.assertEqual(terrain.sample(50, 0), 5)

    def test_no_data_is_not_interpolated_away(self):
        terrain = replace(grid(), values=(0, None, 0, 0, 5, 0))
        self.assertIsNone(terrain.sample(25, 0))
        self.assertEqual(terrain.sample(0, 0), 0)
        self.assertEqual(terrain.sample(50, 50), 5)

    def test_densification_keeps_original_crests_and_adds_samples(self):
        shape = (RoadPoint(0, 0, 0), RoadPoint(30, 0, 3), RoadPoint(100, 0, 0))
        sampled = densify(shape, 20)
        self.assertIn(shape[1], sampled)
        self.assertEqual(sampled[0], shape[0])
        self.assertEqual(sampled[-1], shape[-1])

    def test_terrain_adds_intermediate_hill_to_route(self):
        route, state = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        enriched = enrich_route(route, grid(), spacing_m=50)
        result = remaining_route(enriched, state)
        self.assertEqual(result.ascent_m, 5)
        self.assertEqual(result.descent_m, 5)
        self.assertEqual(enriched.elevation_origin.reference, "a" * 64)
        self.assertIn("elevation_synthetic", result.assumptions)
        self.assertEqual(enriched.elevation_origin.kind, "synthetic")

    def test_frame_datum_and_resolution_must_match_policy(self):
        route, _ = case((RoadPoint(0, 0, 0), RoadPoint(100, 0, 0)))
        for changed in (
            replace(route, horizontal_frame="other-frame"),
            replace(route, vertical_datum="other-datum"),
        ):
            with self.assertRaises(ValueError):
                enrich_route(changed, grid(), spacing_m=50)
        with self.assertRaises(ValueError):
            enrich_route(route, grid(), spacing_m=1)

    def test_structure_preserves_separately_verified_road_height(self):
        route, _ = case((RoadPoint(0, 0, 20), RoadPoint(100, 0, 20)))
        bridge = replace(
            route.occurrences[0], structure="bridge", elevation_quality="verified_road"
        )
        enriched = enrich_route(replace(route, occurrences=(bridge,)), grid(), spacing_m=50)
        self.assertEqual(enriched.occurrences[0].shape[0].elevation_m, 20)

    def test_uncovered_terrain_marks_route_missing(self):
        route, _ = case((RoadPoint(0, 0, 0), RoadPoint(150, 0, 0)))
        enriched = enrich_route(route, grid(), spacing_m=50)
        self.assertEqual(enriched.occurrences[0].elevation_quality, "missing")
        self.assertIsNone(enriched.occurrences[0].shape[-1].elevation_m)

    def test_allocation_budgets_reject_before_densification(self):
        with self.assertRaises(ValueError):
            replace(grid(), width=10_000, height=10_000)
        with self.assertRaises(ValueError):
            densify((RoadPoint(0, 0, 0), RoadPoint(1_000_000, 0, 0)), 0.1)


if __name__ == "__main__":
    unittest.main()
