"""Explicit clocks exercise lifecycle decisions without waiting in real time."""

import unittest
from dataclasses import replace

from test_energy_route_wire import request

from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.scheduling import Impact, Scheduler
from evdt.energy.route_aware.wire import canonical_bytes, decode_record


def snapshot(working=30, real=100, **changes):
    job = request()
    state = replace(
        job.state, working_s=working, observed_real_life_s=real, received_real_life_s=real
    )
    return replace(job, state=state, requested_real_life_s=real, **changes)


def accepted(**changes):
    scheduler = Scheduler()
    scheduler.observe(snapshot(**changes))
    claim = scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
    completion = scheduler.finish(
        forecast(claim.request), fence=1, owner="controller-a", monotonic_s=11, now_real_life_s=101
    )
    assert completion.classification == "current"
    return scheduler


class SchedulingTests(unittest.TestCase):
    def test_first_ready_then_thirty_working_seconds(self):
        scheduler = accepted()
        scheduler.observe(snapshot(59, 110))
        self.assertIsNone(scheduler.pending_reason)
        scheduler.observe(snapshot(60, 111))
        self.assertEqual(scheduler.pending_reason, "periodic")
        claim = scheduler.claim(fence=2, owner="controller-a", monotonic_s=20)
        self.assertEqual(claim.request.generation, 2)
        self.assertEqual(claim.request.state.working_s, 60)

    def test_live_two_minute_period_and_early_plan_change(self):
        policy = replace(
            request().policy,
            period_working_s=120,
            forecast_max_age_working_s=150,
            forecast_max_age_real_life_s=150,
            max_observation_lag_working_s=30,
        )
        startup = Scheduler()
        startup.observe(snapshot(1, policy=policy))
        self.assertEqual(startup.pending_reason, "first_ready")
        scheduler = accepted(policy=policy)
        scheduler.observe(snapshot(149, 219, policy=policy))
        self.assertIsNone(scheduler.pending_reason)
        scheduler.observe(snapshot(150, 220, policy=policy))
        self.assertEqual(scheduler.pending_reason, "periodic")

        scheduler = accepted(policy=policy)
        scheduler.observe(None, unavailable="current_route_plan_changed")
        job = snapshot(45, 115, policy=policy)
        scheduler.observe(
            replace(
                job,
                route=replace(job.route, revision="new-plan"),
                state=replace(job.state, route_revision="new-plan"),
            )
        )
        self.assertEqual(scheduler.pending_reason, "route_change")

    def test_busy_worker_coalesces_into_latest_snapshot(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        first = scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
        for working in (60, 90, 120):
            scheduler.observe(snapshot(working, 101))
            self.assertIsNone(scheduler.claim(fence=2, owner="controller-a", monotonic_s=11))
        completion = scheduler.finish(
            forecast(first.request),
            fence=1,
            owner="controller-a",
            monotonic_s=12,
            now_real_life_s=102,
        )
        self.assertEqual(completion.classification, "historical")
        self.assertEqual(completion.reason, "forecast_working_age_exceeded")
        second = scheduler.claim(fence=2, owner="controller-a", monotonic_s=12)
        self.assertEqual(second.request.state.working_s, 120)

    def test_pause_suspends_cadence_and_resume_schedules_once(self):
        scheduler = accepted()
        scheduler.observe(None, unavailable="source_paused")
        self.assertEqual(scheduler.phase, "paused")
        self.assertIsNone(scheduler.claim(fence=2, owner="controller-a", monotonic_s=20))
        scheduler.observe(snapshot(30, 200))
        self.assertEqual(scheduler.pending_reason, "recovery")
        claim = scheduler.claim(fence=2, owner="controller-a", monotonic_s=100)
        self.assertEqual(claim.request.trigger, "recovery")

    def test_context_change_rejects_late_completion(self):
        scheduler = Scheduler()
        job = snapshot()
        scheduler.observe(job)
        claim = scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
        changed = replace(
            job,
            route=replace(job.route, revision="new-route"),
            state=replace(job.state, route_revision="new-route"),
        )
        scheduler.observe(changed)
        self.assertEqual(scheduler.pending_reason, "route_change")
        completion = scheduler.finish(
            forecast(claim.request),
            fence=1,
            owner="controller-a",
            monotonic_s=11,
            now_real_life_s=101,
        )
        self.assertEqual(completion.reason, "obsolete_context")
        self.assertIsNone(scheduler.accepted_result)

    def test_source_restart_resets_working_time_and_invalidates_old_scope(self):
        scheduler = accepted()
        job = snapshot(1, 110)
        job = replace(
            job,
            state=replace(
                job.state, scope=replace(job.state.scope, source_epoch=2, session_id="new")
            ),
        )
        scheduler.observe(job)
        self.assertEqual(scheduler.pending_reason, "recovery")
        self.assertEqual(
            scheduler.current_status(now_real_life_s=110, monotonic_s=20), "obsolete_context"
        )

    def test_working_clock_rollback_waits_for_new_valid_state(self):
        scheduler = accepted()
        scheduler.observe(snapshot(29, 110))
        self.assertIsNone(scheduler.latest)
        self.assertEqual(scheduler.reason, "working_clock_regression")

    def test_material_change_requires_persistence_and_hysteresis(self):
        scheduler = accepted()
        for working in (31, 33, 35):
            scheduler.observe(snapshot(working, 101), impact=Impact(energy_kwh=1))
            self.assertIsNone(scheduler.pending_reason)
        scheduler.observe(snapshot(36, 102), impact=Impact(energy_kwh=1))
        self.assertEqual(scheduler.pending_reason, "conditions")
        claim = scheduler.claim(fence=2, owner="controller-a", monotonic_s=12)
        scheduler.finish(
            forecast(claim.request),
            fence=2,
            owner="controller-a",
            monotonic_s=13,
            now_real_life_s=103,
        )
        scheduler.observe(snapshot(37, 103), impact=Impact(energy_kwh=1))
        self.assertIsNone(scheduler.pending_reason)
        scheduler.observe(snapshot(38, 104), impact=Impact(energy_kwh=0))
        scheduler.observe(snapshot(39, 104), impact=Impact(energy_kwh=1))
        scheduler.observe(snapshot(44, 104), impact=Impact(energy_kwh=1))
        self.assertIsNone(scheduler.pending_reason)  # Ten-second cooldown after cutoff 36.
        scheduler.observe(snapshot(46, 105), impact=Impact(energy_kwh=1))
        self.assertEqual(scheduler.pending_reason, "conditions")

    def test_reserve_crossing_bypasses_persistence(self):
        scheduler = accepted()
        scheduler.observe(snapshot(31, 101), impact=Impact(reserve_crossed=True))
        self.assertEqual(scheduler.pending_reason, "conditions")

    def test_original_input_age_does_not_use_sensor_freshness_limit(self):
        scheduler = accepted()
        scheduler.observe(snapshot(50, 120))
        self.assertEqual(scheduler.current_status(now_real_life_s=120, monotonic_s=30), "current")
        self.assertEqual(
            scheduler.current_status(now_real_life_s=126, monotonic_s=36), "current_source_stale"
        )

    def test_no_duplicate_claim_and_wrong_fence_or_owner_rejected(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        claim = scheduler.claim(fence=7, owner="controller-a", monotonic_s=10)
        for fence, owner in ((6, "controller-a"), (7, "controller-b")):
            with self.assertRaisesRegex(ValueError, "Obsolete or foreign"):
                scheduler.finish(
                    forecast(claim.request),
                    fence=fence,
                    owner=owner,
                    monotonic_s=11,
                    now_real_life_s=101,
                )
        scheduler.finish(
            forecast(claim.request),
            fence=7,
            owner="controller-a",
            monotonic_s=11,
            now_real_life_s=101,
        )
        with self.assertRaises(ValueError):
            scheduler.finish(
                forecast(claim.request),
                fence=7,
                owner="controller-a",
                monotonic_s=12,
                now_real_life_s=102,
            )

    def test_timeout_uses_monotonic_time_even_if_utc_did_not_advance(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        claim = scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
        self.assertFalse(scheduler.expired(39.9))
        self.assertTrue(scheduler.expired(40))
        completion = scheduler.finish(
            forecast(claim.request),
            fence=1,
            owner="controller-a",
            monotonic_s=40,
            now_real_life_s=100,
        )
        self.assertEqual(completion.reason, "worker_deadline_exceeded")
        self.assertIsNone(scheduler.accepted_result)

    def test_one_retry_then_stable_failure_even_with_fresh_packets(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
        scheduler.failed(fence=1, owner="controller-a", monotonic_s=11)
        self.assertIsNone(scheduler.claim(fence=2, owner="controller-a", monotonic_s=11.5))
        scheduler.observe(snapshot(31, 102))
        scheduler.claim(fence=2, owner="controller-a", monotonic_s=12)
        scheduler.failed(fence=2, owner="controller-a", monotonic_s=13)
        for working in (32, 60, 90):
            scheduler.observe(snapshot(working, 104))
            self.assertIsNone(scheduler.claim(fence=3, owner="controller-a", monotonic_s=14))
        self.assertEqual(scheduler.reason, "worker_retry_exhausted")

    def test_restart_requires_cleanup_fresh_input_and_new_fence(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
        import json

        restored = decode_record(Scheduler, json.loads(canonical_bytes(scheduler)))
        with self.assertRaises(ValueError):
            restored.reconcile_restart(owned_worker_stopped=False)
        restored.reconcile_restart(owned_worker_stopped=True)
        self.assertIsNone(restored.active)
        self.assertIsNone(restored.latest)
        restored.observe(snapshot(31, 103))
        with self.assertRaises(ValueError):
            restored.claim(fence=1, owner="controller-b", monotonic_s=1)
        claim = restored.claim(fence=2, owner="controller-b", monotonic_s=1)
        self.assertEqual(claim.request.trigger, "recovery")

    def test_updated_conditions_make_previous_result_historical_until_refreshed(self):
        scheduler = accepted()
        job = snapshot(31, 101)
        job = replace(job, conditions=(replace(job.conditions[0], auxiliary_power_w=12_000),))
        scheduler.observe(job, impact=Impact(energy_kwh=1))
        self.assertIsNone(scheduler.pending_reason)
        self.assertEqual(
            scheduler.current_status(now_real_life_s=101, monotonic_s=11), "obsolete_context"
        )
        scheduler.observe(
            replace(job, state=replace(job.state, working_s=36)), impact=Impact(energy_kwh=1)
        )
        self.assertEqual(scheduler.pending_reason, "conditions")

    def test_utc_rollback_does_not_publish_current_result(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        claim = scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
        result = scheduler.finish(
            forecast(claim.request),
            fence=1,
            owner="controller-a",
            monotonic_s=11,
            now_real_life_s=99,
        )
        self.assertEqual(result.classification, "historical")
        self.assertEqual(result.reason, "current_source_stale")

    def test_terminal_source_never_schedules_more_driving_work(self):
        scheduler = accepted()
        scheduler.observe(None, unavailable="source_finished")
        self.assertEqual(scheduler.phase, "finished")
        self.assertIsNone(scheduler.claim(fence=2, owner="controller-a", monotonic_s=20))

    def test_stale_source_suppresses_completion_without_destroying_last_result(self):
        scheduler = accepted()
        old = scheduler.accepted_result
        scheduler.observe(snapshot(60, 110))
        claim = scheduler.claim(fence=2, owner="controller-a", monotonic_s=20)
        scheduler.observe(None, unavailable="required_input_stale_or_future")
        completed = scheduler.finish(
            forecast(claim.request),
            fence=2,
            owner="controller-a",
            monotonic_s=21,
            now_real_life_s=111,
        )
        self.assertEqual(completed.classification, "historical")
        self.assertEqual(scheduler.accepted_result, old)

    def test_many_missed_periods_create_one_job_and_count_skips(self):
        scheduler = accepted()
        scheduler.observe(snapshot(150, 110))
        claim = scheduler.claim(fence=2, owner="controller-a", monotonic_s=20)
        self.assertEqual(claim.request.state.working_s, 150)
        self.assertEqual(scheduler.coalesced_periods, 3)
        self.assertIsNone(scheduler.claim(fence=3, owner="controller-a", monotonic_s=21))

    def test_superseded_failure_does_not_charge_new_route_retry_budget(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        scheduler.claim(fence=1, owner="controller-a", monotonic_s=10)
        job = snapshot(31, 101)
        job = replace(
            job,
            route=replace(job.route, revision="detour"),
            state=replace(job.state, route_revision="detour"),
        )
        scheduler.observe(job)
        scheduler.failed(fence=1, owner="controller-a", monotonic_s=11)
        self.assertEqual(scheduler.failures, 0)
        self.assertEqual(scheduler.pending_reason, "route_change")

    def test_changed_assignment_can_recover_after_retry_exhaustion_and_restart(self):
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        for fence, start in ((1, 10), (2, 12)):
            scheduler.claim(fence=fence, owner="controller-a", monotonic_s=start)
            scheduler.failed(fence=fence, owner="controller-a", monotonic_s=start + 0.1)
        scheduler.reconcile_restart(owned_worker_stopped=True)
        job = snapshot(31, 102)
        job = replace(
            job,
            route=replace(job.route, revision="changed-assignment"),
            state=replace(job.state, route_revision="changed-assignment"),
        )
        scheduler.observe(job)
        self.assertEqual(scheduler.failures, 0)
        self.assertIsNotNone(scheduler.claim(fence=3, owner="controller-b", monotonic_s=1))
