"""Normal route progress preserves cadence without accepting obsolete assignments."""

import unittest
from dataclasses import replace

from test_energy_route_wire import request

from evdt.energy.route_aware.contracts import RoadPoint
from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.scenarios import RoadConditions, RouteScenario
from evdt.energy.route_aware.scheduling import Scheduler, context_key
from evdt.energy.route_aware.screening import estimate_impact


def assigned_pair():
    job = request()
    first = replace(job.route.occurrences[0], stops=())
    second = replace(
        first,
        index=1,
        edge_id="next-road",
        lane_id="next-lane",
        shape=(RoadPoint(100, 0, 15), RoadPoint(200, 0, 20)),
    )
    job = replace(job, route=replace(job.route, occurrences=(first, second)))
    job = replace(job, assignment_sha256="c" * 64)
    progressed = replace(
        job,
        route=replace(job.route, occurrences=(replace(second, index=0),)),
        state=replace(job.state, working_s=59, observed_real_life_s=110, received_real_life_s=110),
        requested_real_life_s=110,
    )
    return job, progressed


class ProgressLifecycleTests(unittest.TestCase):
    def test_suffix_progress_keeps_current_result_and_thirty_second_cadence(self):
        job, progressed = assigned_pair()
        scheduler = Scheduler()
        scheduler.observe(job)
        claim = scheduler.claim(fence=1, owner="controller", monotonic_s=1)
        scheduler.finish(
            forecast(claim.request),
            fence=1,
            owner="controller",
            monotonic_s=2,
            now_real_life_s=100,
        )
        scheduler.observe(progressed)
        self.assertIsNone(scheduler.pending_reason)
        self.assertEqual(scheduler.current_status(now_real_life_s=110, monotonic_s=12), "current")
        scheduler.observe(replace(progressed, state=replace(progressed.state, working_s=60)))
        self.assertEqual(scheduler.pending_reason, "periodic")

    def test_true_route_change_still_rejects_active_completion(self):
        job, progressed = assigned_pair()
        scheduler = Scheduler()
        scheduler.observe(job)
        claim = scheduler.claim(fence=1, owner="controller", monotonic_s=1)
        changed = replace(
            progressed,
            route=replace(progressed.route, revision="detour"),
            state=replace(progressed.state, route_revision="detour"),
        )
        scheduler.observe(changed)
        self.assertEqual(scheduler.pending_reason, "route_change")
        completion = scheduler.finish(
            forecast(claim.request),
            fence=1,
            owner="controller",
            monotonic_s=12,
            now_real_life_s=110,
        )
        self.assertEqual(completion.reason, "obsolete_context")

    def test_unbound_requests_keep_full_geometry_identity(self):
        job, progressed = assigned_pair()
        self.assertNotEqual(
            context_key(replace(job, assignment_sha256=None)),
            context_key(replace(progressed, assignment_sha256=None)),
        )

    def test_new_assignment_or_elevation_never_reuses_context(self):
        job, progressed = assigned_pair()
        changed = (
            replace(progressed, assignment_sha256="d" * 64),
            replace(progressed, route=replace(progressed.route, network_sha256="e" * 64)),
            replace(progressed, route=replace(progressed.route, vertical_datum="new-datum")),
            replace(
                progressed,
                route=replace(
                    progressed.route,
                    elevation_origin=replace(progressed.route.elevation_origin, reference="new"),
                ),
            ),
        )
        for item in changed:
            with self.subTest(route=item.route.revision, assignment=item.assignment_sha256):
                self.assertNotEqual(context_key(job), context_key(item))

    def test_remapped_scenario_keeps_context_and_screens_equipment_change(self):
        job, progressed = assigned_pair()
        conditions = (replace(job.conditions[0], auxiliary_power_w=8000),)
        original = RouteScenario(
            "restriction", job.vehicle.origin, 90, roads=(RoadConditions(1, conditions),)
        )
        suffix = replace(original, roads=(RoadConditions(0, conditions),))
        job = replace(job, scenario=original)
        progressed = replace(progressed, scenario=suffix)
        self.assertEqual(context_key(job), context_key(progressed))
        changed = replace(progressed, state=replace(progressed.state, auxiliary_power_w=18000))
        impact = estimate_impact(job, changed, None)
        self.assertGreater(impact.energy_kwh, 0)
        self.assertFalse(impact.domain_changed)

    def test_reserve_crossing_is_detected_after_ordinary_progress(self):
        job, progressed = assigned_pair()
        progressed = replace(progressed, state=replace(progressed.state, soc=0.09))
        self.assertTrue(estimate_impact(job, progressed, None).reserve_crossed)


if __name__ == "__main__":
    unittest.main()
