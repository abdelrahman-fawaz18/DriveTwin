"""A stop request must leave a durable forecast claim safe to reconcile."""

import importlib
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, Mock

from test_energy_route_scheduling import snapshot

from evdt.energy.route_aware.scheduling import Scheduler

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/route_aware_energy"))
controller = importlib.import_module("controller")


def test_stop_during_claim_commits_before_cleanup(tmp_path, monkeypatch):
    conn = Mock()
    conn.execute.return_value.fetchall.return_value = []
    store = Mock()
    scheduler = Scheduler()
    store.ownership.return_value = nullcontext(True)
    store.running_claims.return_value = []
    store.load.return_value = scheduler
    stop_requested = Mock(return_value=False)
    worker_pool = MagicMock()
    worker_pool.__enter__.return_value = worker_pool
    order = []

    def claim(run, asset, state, now):
        record = state.claim(fence=1, owner="controller", monotonic_s=now)
        assert record is not None
        # Simulate a signal after the in-memory claim and before the SQL commit.
        stop_requested.return_value = True
        order.append("claim written")
        return record

    def commit():
        order.append("commit")

    def remove(fence, owner):
        assert (fence, owner) == (1, "controller")
        order.append("worker removed")

    def reconcile(run, asset, state, *, owned_worker_stopped):
        if state.active:
            order.append("claim reconciled")
        state.reconcile_restart(owned_worker_stopped=owned_worker_stopped)

    store.claim.side_effect = claim
    store.reconcile.side_effect = reconcile
    conn.commit.side_effect = commit
    monkeypatch.setattr(controller, "SchedulerStore", Mock(return_value=store))
    monkeypatch.setattr(controller, "ThreadPoolExecutor", Mock(return_value=worker_pool))
    monkeypatch.setattr(controller, "remove_owned", remove)
    monkeypatch.setattr(controller, "observed_energy", Mock(return_value=None))
    monkeypatch.setattr(importlib.import_module("charging_idle"), "reconcile", Mock())
    request = snapshot(real=time.time())
    monkeypatch.setattr(controller, "prepare_input", Mock(return_value=(object(), request)))

    report = controller.operate(
        conn,
        run="run",
        asset="bus",
        client=Mock(),
        provider=Mock(),
        image="unused",
        output=tmp_path,
        maximum_seconds=1,
        stop_requested=stop_requested,
    )

    assert order[order.index("claim written") :] == [
        "claim written",
        "commit",
        "worker removed",
        "worker removed",
        "claim reconciled",
        "commit",
    ]
    worker_pool.submit.assert_not_called()
    assert report["stop_reason"] == "operator_interrupt"
    assert report["cleanup_verified"]
    assert scheduler.active is None
