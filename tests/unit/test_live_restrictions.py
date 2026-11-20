"""Live assignments retain road restrictions across stop and suffix changes."""

import json
import sys
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from test_energy_route_product import RUN, product_case

from evdt.energy.route_aware.admission import InputUnavailable
from evdt.energy.route_aware.product import prepare_input
from evdt.energy.route_aware.wire import fingerprint

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/geographic"))
from live_assignments import RestrictionUpdater, indexed_restrictions  # noqa: E402


class LiveRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.args, self.base, self.snapshot = product_case()
        self.context = self.args["context"]
        self.provider = MagicMock(return_value=self.args["prepared"])
        self.recipe = {
            "speed_zones": [{"start_fraction": 0, "end_fraction": 1, "speed_factor": 0.55}]
        }
        self.updater = RestrictionUpdater(
            self.base, self.recipe, self.context.plan.edges, self.provider
        )
        self.bind(self.updater.current)

    def bind(self, assignment):
        self.snapshot["assignment"] = json.loads(json.dumps(asdict(assignment)))
        self.snapshot["assignment_sha256"] = fingerprint(assignment)

    def test_first_forecast_waits_for_correct_restriction_assignment(self):
        with self.assertRaisesRegex(InputUnavailable, "updated_route_assignment"):
            prepare_input(
                self.snapshot,
                run_id=RUN,
                asset_id=self.base.asset_id,
                prepare_route=self.provider,
            )
        self.provider.assert_not_called()
        candidate = self.updater.candidate(self.snapshot)
        self.assertEqual(candidate.revision, 2)
        self.assertEqual(self.updater.current.revision, 1)
        self.bind(candidate)
        _, request = prepare_input(
            self.snapshot,
            run_id=RUN,
            asset_id=self.base.asset_id,
            prepare_route=self.provider,
        )
        self.assertTrue(request.scenario.roads)
        self.assertTrue(all(r.intervals[0].speed_factor == 0.55 for r in request.scenario.roads))
        self.assertEqual(candidate.scenario_route_revision, self.context.plan.revision)
        self.updater.committed(candidate)
        self.assertIsNone(self.updater.candidate(self.snapshot))

    def test_stop_change_rebinds_without_losing_the_restriction(self):
        first = self.updater.candidate(self.snapshot)
        self.updater.committed(first)
        self.bind(first)
        changed = replace(self.context, plan=replace(self.context.plan, remaining_stops=()))
        self.snapshot["context"] = json.loads(json.dumps(asdict(changed)))
        prepared = self.args["prepared"]
        self.provider.return_value = replace(
            prepared,
            plan_sha256=changed.plan.revision,
            route=replace(
                prepared.route,
                revision=changed.plan.revision,
                occurrences=tuple(replace(r, stops=()) for r in prepared.route.occurrences),
            ),
        )
        second = self.updater.candidate(self.snapshot)
        self.assertEqual(second.revision, 3)
        self.assertNotEqual(second.scenario_route_revision, first.scenario_route_revision)
        self.assertEqual(second.scenario.roads, first.scenario.roads)
        # A second read before commit returns the same candidate, not revision 4.
        self.assertEqual(second, self.updater.candidate(self.snapshot))

    def test_repeated_edge_and_connectors_keep_their_own_visit(self):
        # The middle visit to 'a' is restricted; the first and final visits are not.
        edges = ("a", "b", "a", "c", "a")
        updater = RestrictionUpdater(
            self.base,
            {
                "speed_zones": [
                    {"start_fraction": 0.4, "end_fraction": 0.8, "speed_factor": 0.7},
                    {"start_fraction": 0.6, "end_fraction": 0.8, "speed_factor": 0.4},
                ]
            },
            edges,
            self.provider,
        )
        plan = replace(self.context.plan, edges=edges, remaining_stops=())
        self.snapshot["context"] = json.loads(json.dumps(asdict(replace(self.context, plan=plan))))
        self.bind(updater.current)
        self.provider.return_value = SimpleNamespace(
            validate=MagicMock(),
            first_external_index=0,
            external_occurrences=(0, 2, 4, 6, 8),
            route=SimpleNamespace(occurrences=tuple(range(9))),
        )
        candidate = updater.candidate(self.snapshot)
        self.assertEqual(
            [(r.occurrence_index, r.intervals[0].speed_factor) for r in candidate.scenario.roads],
            [(4, 0.7), (5, 0.7), (6, 0.4), (7, 0.4)],
        )
        # Existing projection discards passed roads and keeps future ones aligned.
        suffix = replace(
            self.args["prepared"],
            first_external_index=2,
            scenario_indices=(4, 5, 6, 7, 8),
            route=SimpleNamespace(occurrences=tuple(range(5))),
        )
        projected = suffix.project_scenario(candidate.scenario)
        self.assertEqual([r.occurrence_index for r in projected.roads], [0, 1, 2, 3])

    def test_foreign_route_source_assignment_and_unready_input_are_rejected(self):
        self.snapshot["reason"] = "source_stale_or_future"
        self.assertIsNone(self.updater.candidate(self.snapshot))
        self.provider.assert_not_called()
        self.snapshot["reason"] = None
        self.snapshot["assignment_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.updater.candidate(self.snapshot)
        self.bind(self.updater.current)
        for context in (
            replace(self.context, scope=replace(self.context.scope, source_id="other")),
            replace(self.context, plan=replace(self.context.plan, network_sha256="e" * 64)),
            replace(
                self.context, plan=replace(self.context.plan, edges=(*self.context.plan.edges, "x"))
            ),
        ):
            with self.subTest(context=context):
                self.snapshot["context"] = json.loads(json.dumps(asdict(context)))
                with self.assertRaisesRegex(ValueError, "original assigned"):
                    self.updater.candidate(self.snapshot)

    def test_zone_bounds_and_invalid_values(self):
        self.assertEqual(
            indexed_restrictions(self.recipe, 5),
            [{"start_index": 0, "end_index": 5, "speed_factor": 0.55}],
        )
        for start, end, factor in [
            (0, 0, 0.5),
            (-0.1, 0.5, 0.5),
            (0, 1.1, 0.5),
            (0, 1, 0),
            (0, 1, float("nan")),
            (0, 1, True),
            (0.01, 0.02, 0.5),
        ]:
            with self.subTest(values=(start, end, factor)), self.assertRaises(ValueError):
                indexed_restrictions(
                    {
                        "speed_zones": [
                            {"start_fraction": start, "end_fraction": end, "speed_factor": factor}
                        ]
                    },
                    5,
                )
