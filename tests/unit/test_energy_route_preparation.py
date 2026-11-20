"""Confirmed plan changes retain junctions and independent height provenance."""

import unittest
from dataclasses import replace

from test_energy_route_sumo import NETWORK

from evdt.energy.route_aware.admission import InputUnavailable
from evdt.energy.route_aware.context import SourcePlan, SourceStop
from evdt.energy.route_aware.contracts import Origin
from evdt.energy.route_aware.preparation import prepare_plan
from evdt.energy.route_aware.sumo_routes import read_network


def heights(occurrence):
    return replace(
        occurrence,
        elevation_quality="synthetic",
        shape=tuple(replace(p, elevation_m=p.x_m / 100) for p in occurrence.shape),
    )


class PreparationTests(unittest.TestCase):
    def test_suffix_keeps_original_plan_binding_and_stop_occurrences(self):
        network = read_network(NETWORK)
        plan = SourcePlan(network.sha256, ("a", "b"), (SourceStop("s", 1, "b_0", 20, 10),))
        result = prepare_plan(
            network,
            plan,
            enrich=heights,
            vertical_datum="test",
            elevation_origin=Origin("declared", "test", "profile"),
            first_external_index=1,
            current_lanes={1: "b_0"},
        )
        result.validate(plan)
        self.assertEqual(result.route.revision, plan.revision)
        self.assertEqual([r.edge_id for r in result.route.occurrences], ["b"])
        self.assertEqual(result.occurrence_for(1), 0)
        with self.assertRaisesRegex(InputUnavailable, "position_outside_prepared_suffix"):
            result.occurrence_for(0)
        passed = replace(plan, remaining_stops=(SourceStop("missed", 0, "a_0", 20, 10),))
        with self.assertRaisesRegex(InputUnavailable, "passed_stop_without_update"):
            prepare_plan(
                network,
                passed,
                enrich=heights,
                vertical_datum="test",
                elevation_origin=Origin("declared", "test", "profile"),
                first_external_index=1,
            )

    def test_new_plan_is_bound_to_exact_roads_stops_and_network(self):
        network = read_network(NETWORK)
        plan = SourcePlan(network.sha256, ("a", "b"), (SourceStop("s", 1, "b_0", 20, 10),))
        result = prepare_plan(
            network,
            plan,
            enrich=heights,
            vertical_datum="synthetic",
            elevation_origin=Origin("synthetic", "test", "linear-hill"),
        )
        self.assertEqual(result.external_occurrences, (0, 2))
        self.assertEqual(result.route.revision, plan.revision)
        self.assertEqual(result.route.occurrences[1].edge_id, ":j")
        self.assertEqual(result.route.occurrences[2].stops[0].dwell_working_s, 10)
        changed = replace(plan, remaining_stops=())
        self.assertNotEqual(changed.revision, result.route.revision)
        with self.assertRaisesRegex(InputUnavailable, "route_revision_mismatch"):
            result.validate(changed)

    def test_terrain_processing_cannot_change_stop_or_speed_semantics(self):
        network = read_network(NETWORK)
        plan = SourcePlan(network.sha256, ("a", "b"), ())
        with self.assertRaisesRegex(InputUnavailable, "elevation_changed_route_semantics"):
            prepare_plan(
                network,
                plan,
                enrich=lambda occurrence: replace(heights(occurrence), speed_limit_m_s=1),
                vertical_datum="synthetic",
                elevation_origin=Origin("synthetic", "test", "linear-hill"),
            )

    def test_missing_heights_and_unassigned_network_are_unavailable(self):
        network = read_network(NETWORK)
        plan = SourcePlan(network.sha256, ("a", "b"), ())
        with self.assertRaisesRegex(InputUnavailable, "missing_route_elevation"):
            prepare_plan(
                network,
                plan,
                enrich=lambda o: o,
                vertical_datum="unknown",
                elevation_origin=Origin("declared", "test", "missing"),
            )
        with self.assertRaisesRegex(InputUnavailable, "unassigned_network"):
            prepare_plan(
                network,
                replace(plan, network_sha256="a" * 64),
                enrich=heights,
                vertical_datum="synthetic",
                elevation_origin=Origin("synthetic", "test", "linear-hill"),
            )
