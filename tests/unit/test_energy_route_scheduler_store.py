"""Host-side storage guards; SQL execution is a separate connected gate."""

import unittest
from unittest.mock import Mock

from test_energy_route_scheduling import snapshot

from evdt.energy.route_aware.scheduling import Scheduler
from evdt.monitoring.route_scheduler_store import SchedulerStore


class StoreGuardTests(unittest.TestCase):
    def setUp(self):
        self.conn = Mock()
        self.store = SchedulerStore(self.conn, "00000000-0000-0000-0000-000000000001")

    def test_state_access_requires_global_ownership(self):
        with self.assertRaisesRegex(RuntimeError, "ownership required"):
            self.store.load("run", "bus")
        self.conn.execute.assert_not_called()

    def test_cannot_write_another_asset_state(self):
        self.store.locked = True
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        with self.assertRaisesRegex(ValueError, "scope differs"):
            self.store.save("run", "other-bus", scheduler)
        self.conn.execute.assert_not_called()

    def test_old_controller_job_cannot_be_published_by_a_new_owner(self):
        self.store.locked = True
        with self.assertRaisesRegex(ValueError, "reconciled as incomplete"):
            self.store.close(
                "run",
                "bus",
                fence=1,
                claim_owner="other-owner",
                status="current",
                outcome={"result": "placeholder"},
            )
        self.conn.execute.assert_not_called()

    def test_failed_cleanup_cannot_release_persisted_claim(self):
        self.store.locked = True
        scheduler = Scheduler()
        scheduler.observe(snapshot())
        scheduler.claim(fence=1, owner=self.store.owner, monotonic_s=10)
        with self.assertRaisesRegex(ValueError, "cleanup"):
            self.store.reconcile("run", "bus", scheduler, owned_worker_stopped=False)
        self.assertIsNotNone(scheduler.active)
        self.conn.execute.assert_not_called()
