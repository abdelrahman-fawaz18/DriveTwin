"""Save admitted suffix projections without accepting a replacement job request."""

import json
import unittest
from dataclasses import asdict, replace
from unittest.mock import MagicMock

from test_energy_route_admission import rebind
from test_energy_route_product import RUN, product_case

from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.product import prepare_input
from evdt.energy.route_aware.scenarios import RoadConditions, RouteScenario
from evdt.energy.route_aware.scheduling import Scheduler
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint
from evdt.monitoring.route_energy_views import save_result


def projected_claim():
    args, assignment, snapshot = product_case()
    original = args["context"].plan
    plan = replace(
        original,
        edges=("already-traveled", *original.edges),
        remaining_stops=tuple(replace(s, external_index=1) for s in original.remaining_stops),
    )
    rebind(args, replace(args["context"], plan=plan, external_index=1))
    prepared = replace(
        args["prepared"],
        plan_sha256=plan.revision,
        route=replace(args["prepared"].route, revision=plan.revision),
        first_external_index=1,
        scenario_indices=(2,),
    )
    scenario = RouteScenario(
        "road-condition",
        assignment.conditions[0].origin,
        0,
        roads=(RoadConditions(2, assignment.conditions),),
    )
    assignment = replace(assignment, scenario=scenario, scenario_route_revision=plan.revision)
    snapshot.update(
        assignment=asdict(assignment),
        assignment_sha256=fingerprint(assignment),
        context=asdict(args["context"]),
        packet=asdict(args["packet"]),
    )
    _, request = prepare_input(
        json.loads(json.dumps(snapshot)),
        run_id=RUN,
        asset_id=assignment.asset_id,
        prepare_route=lambda a, c: prepared,
    )
    scheduler = Scheduler()
    scheduler.observe(request)
    claim = scheduler.claim(fence=1, owner=RUN, monotonic_s=1)
    completion = scheduler.finish(
        forecast(claim.request),
        fence=1,
        owner=RUN,
        monotonic_s=2,
        now_real_life_s=100,
    )
    return assignment, claim, completion


def stored_job(claim, completion):
    return {
        "owner": claim.owner,
        "run_id": claim.request.state.scope.run_id,
        "asset_id": claim.request.state.scope.asset_id,
        "generation": claim.request.generation,
        "input_sha256": fingerprint(claim.request),
        "request": json.loads(canonical_bytes(claim.request)),
        "status": completion.classification,
    }


class ResultBindingTests(unittest.TestCase):
    def test_admitted_localized_suffix_is_saved_with_original_assignment(self):
        assignment, claim, completion = projected_claim()
        self.assertEqual(assignment.scenario.roads[0].occurrence_index, 2)
        self.assertEqual(claim.request.scenario.roads[0].occurrence_index, 0)
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [
            stored_job(claim, completion),
            {
                "payload": json.loads(canonical_bytes(completion.result)),
                "assignment_sha256": fingerprint(assignment),
                "classification": completion.classification,
            },
        ]
        save_result(conn, assignment, claim, completion)
        self.assertEqual(conn.execute.call_count, 3)

    def test_missing_changed_or_nonterminal_claim_cannot_publish(self):
        assignment, claim, completion = projected_claim()
        original = stored_job(claim, completion)
        for change in (
            None,
            {"owner": "other"},
            {"run_id": "other"},
            {"asset_id": "other"},
            {"generation": 2},
            {"input_sha256": "f" * 64},
            {"request": {}},
            {"status": "running"},
            {"status": "incomplete"},
            {"status": "historical"},
        ):
            with self.subTest(change=change):
                conn = MagicMock()
                conn.execute.return_value.fetchone.return_value = (
                    None if change is None else original | change
                )
                with self.assertRaisesRegex(ValueError, "immutable terminal request"):
                    save_result(conn, assignment, claim, completion)
                self.assertEqual(conn.execute.call_count, 1)

    def test_original_assignment_and_route_binding_remain_required(self):
        assignment, claim, completion = projected_claim()
        for changed in (
            replace(assignment, scenario_route_revision="a" * 64),
            replace(assignment, revision=2),
            replace(assignment, scenario=None, scenario_route_revision=None),
        ):
            with self.subTest(assignment=changed.revision):
                conn = MagicMock()
                with self.assertRaisesRegex(ValueError, "assigned source"):
                    save_result(conn, changed, claim, completion)
                conn.execute.assert_not_called()
