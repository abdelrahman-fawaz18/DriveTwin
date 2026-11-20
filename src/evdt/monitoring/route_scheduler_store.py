"""Durable scheduler state and job fences on the existing PostgreSQL service.

The caller holds one global session lock across worker ownership, and uses a
transaction for each state/job update. The lock survives transaction commits and
is released by PostgreSQL if the controller connection dies. A replacement must
reconcile recorded workers before claiming fresh work.
"""

import json
import uuid
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from evdt.energy.route_aware.scheduling import Scheduler
from evdt.energy.route_aware.wire import canonical_bytes, decode_record, fingerprint

GLOBAL_LOCK = 730128002


class SchedulerStore:
    def __init__(self, conn, owner):
        self.conn = conn
        self.owner = str(uuid.UUID(owner))
        self.locked = False

    @contextmanager
    def ownership(self):
        if self.locked:
            raise RuntimeError("Controller lock is not reentrant")
        locked = self.conn.execute(
            "SELECT pg_try_advisory_lock(%s) AS locked", (GLOBAL_LOCK,)
        ).fetchone()["locked"]
        self.conn.commit()
        self.locked = bool(locked)
        try:
            yield self.locked
        finally:
            if self.locked:
                self.conn.rollback()
                self.conn.execute("SELECT pg_advisory_unlock(%s)", (GLOBAL_LOCK,))
                self.conn.commit()
                self.locked = False

    def _require_owner(self):
        if not self.locked:
            raise RuntimeError("Global worker ownership required")

    def load(self, run, asset):
        self._require_owner()
        row = self.conn.execute(
            "SELECT state FROM m_route_forecast_states WHERE run_id=%s AND asset_id=%s FOR UPDATE",
            (run, asset),
        ).fetchone()
        return decode_record(Scheduler, row["state"]) if row else Scheduler()

    def running_claims(self):
        self._require_owner()
        rows = self.conn.execute(
            "SELECT fence,owner,run_id,asset_id FROM m_route_forecast_jobs WHERE status='running' ORDER BY fence LIMIT 101"
        ).fetchall()
        if len(rows) > 100:
            raise RuntimeError("Interrupted-claim reconciliation exceeds the bounded domain")
        return rows

    def save(self, run, asset, scheduler):
        self._require_owner()
        # The lifecycle cannot be moved into another asset's row.
        requests = [
            scheduler.latest,
            scheduler.accepted_request,
            scheduler.active.request if scheduler.active else None,
        ]
        if any(
            r and (r.state.scope.run_id, r.state.scope.asset_id) != (str(run), asset)
            for r in requests
        ):
            raise ValueError("Scheduler source scope differs from its storage row")
        raw = canonical_bytes(scheduler)
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError("Scheduler state exceeds its bounded snapshot budget")
        self.conn.execute(
            """INSERT INTO m_route_forecast_states(run_id,asset_id,state) VALUES(%s,%s,%s)
            ON CONFLICT(run_id,asset_id) DO UPDATE SET state=excluded.state,updated_at=clock_timestamp()""",
            (run, asset, Jsonb(json.loads(raw))),
        )

    def claim(self, run, asset, scheduler, monotonic_s):
        self._require_owner()
        if self.conn.execute(
            "SELECT fence FROM m_route_forecast_jobs WHERE status='running' LIMIT 1"
        ).fetchone():
            return None
        # Sequence allocation is durable even if this transaction rolls back.
        fence = self.conn.execute("SELECT nextval('m_route_forecast_fence') AS fence").fetchone()[
            "fence"
        ]
        claim = scheduler.claim(fence=fence, owner=self.owner, monotonic_s=monotonic_s)
        if claim is None:
            return None
        raw = canonical_bytes(claim.request)
        if len(raw) > claim.request.policy.max_request_bytes:
            raise ValueError("Worker request exceeds its policy")
        self.save(run, asset, scheduler)
        self.conn.execute(
            """INSERT INTO m_route_forecast_jobs
            (fence,owner,run_id,asset_id,generation,input_sha256,request,status,lease_until)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'running',clock_timestamp() + %s * interval '1 second')""",
            (
                fence,
                self.owner,
                run,
                asset,
                claim.request.generation,
                fingerprint(claim.request),
                Jsonb(json.loads(raw)),
                claim.request.policy.lease_real_life_s,
            ),
        )
        return claim

    def close(self, run, asset, *, fence, claim_owner, status, outcome):
        self._require_owner()
        if status not in ("current", "historical", "failed", "incomplete"):
            raise ValueError("Explicit terminal job status required")
        if status != "incomplete" and claim_owner != self.owner:
            raise ValueError("Another controller's claim can only be reconciled as incomplete")
        row = self.conn.execute(
            """SELECT owner,run_id,asset_id,status,outcome FROM m_route_forecast_jobs
            WHERE fence=%s FOR UPDATE""",
            (fence,),
        ).fetchone()
        if not row or (str(row["owner"]), str(row["run_id"]), row["asset_id"]) != (
            claim_owner,
            str(run),
            asset,
        ):
            raise ValueError("Job close scope or fence mismatch")
        if row["status"] != "running":
            if row["status"] == status and row["outcome"] == outcome:
                return False
            raise ValueError("Completed claim outcome conflict")
        self.conn.execute(
            """UPDATE m_route_forecast_jobs SET status=%s,outcome=%s,completed_at=clock_timestamp()
            WHERE fence=%s AND status='running'""",
            (status, Jsonb(outcome), fence),
        )
        return True

    def reconcile(self, run, asset, scheduler, *, owned_worker_stopped):
        self._require_owner()
        active = scheduler.active
        scheduler.reconcile_restart(owned_worker_stopped=owned_worker_stopped)
        if active:
            self.close(
                run,
                asset,
                fence=active.fence,
                claim_owner=active.owner,
                status="incomplete",
                outcome={"reason": "controller_interrupted"},
            )
        self.save(run, asset, scheduler)
