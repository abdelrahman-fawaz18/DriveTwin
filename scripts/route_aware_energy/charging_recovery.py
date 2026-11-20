"""Own a separate forecast job for a verified stopped bus."""

import json
import time
from uuid import UUID

from psycopg.types.json import Jsonb
from runtime import remove_owned, run_charging_isolated

from evdt.energy.moving_charge_recovery import RECOVERY_HOLD_REAL_LIFE_S
from evdt.energy.moving_charge_recovery_input import prepare_replacement
from evdt.energy.route_aware.scheduling import Claim
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint
from evdt.monitoring.held_charge_observation import held_key, read_current_hold
from evdt.monitoring.route_energy_views import latest_assignment


def reconcile(conn):
    """Discard unconfirmed work under the shared Forecast Controller lock."""
    rows = conn.execute(
        "SELECT fence,owner FROM m_moving_charge_recovery_jobs WHERE status='running' FOR UPDATE"
    ).fetchall()
    for row in rows:
        remove_owned(row["fence"], str(row["owner"]))
        close(conn, row["fence"], row["owner"], "incomplete")
    conn.commit()


def close(conn, fence, owner, status, payload=None):
    row = conn.execute(
        """UPDATE m_moving_charge_recovery_jobs SET status=%s,payload=%s,
           completed_at=clock_timestamp() WHERE fence=%s AND owner=%s AND status='running'""",
        (status, payload, fence, owner),
    )
    if row.rowcount != 1:
        raise RuntimeError("Held departure forecast lost its owned job")


class RecoveryCharging:
    def __init__(self, conn, owner, run, asset, stations, output):
        self.conn, self.owner, self.run, self.asset = conn, owner, run, asset
        self.stations, self.output = stations, output
        self.future = self.claim = self.held = self.job = None
        self.seen = None
        self.completed = self.failed = 0

    def poll(self, snapshot, executor, image, provider):
        if self.future is not None:
            if not self.future.done():
                return True
            self.finish()
        if snapshot.get("reason") != "source_paused":
            return False
        assignment = latest_assignment(self.conn, self.run, self.asset)
        observed = (
            read_current_hold(self.conn, self.run, self.asset, assignment.source_id)
            if assignment and assignment.enabled
            else None
        )
        if observed is None:
            self.conn.commit()
            return False
        held, clock = observed
        receipt = fingerprint(held)
        if receipt == self.seen:
            self.conn.commit()
            return False
        self.seen = receipt
        now = time.time()
        try:
            job = prepare_replacement(
                held,
                clock,
                assignment=assignment,
                stations=self.stations,
                provider=provider,
                now=now,
            )
        except ValueError:
            self.failed += 1
            self.conn.commit()
            return False
        span = min(
            self.stations.budget_real_life_s + 3,
            assignment.policy.worker_deadline_real_life_s,
            held.observed_real_life_s + RECOVERY_HOLD_REAL_LIFE_S - time.time(),
        )
        if span <= 0:
            self.conn.commit()
            return False
        row = self.conn.execute(
            """INSERT INTO m_moving_charge_recovery_jobs
               (fence,owner,run_id,asset_id,source_id,source_epoch,session_id,
                observation_sha256,request_sha256,assignment_sha256,status)
               VALUES(nextval('m_route_forecast_fence'),%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')
               ON CONFLICT DO NOTHING RETURNING fence""",
            (UUID(self.owner), *held_key(held), receipt, fingerprint(job), fingerprint(assignment)),
        ).fetchone()
        self.conn.commit()
        if row is None:
            return False
        started = time.monotonic()
        self.claim = Claim(row["fence"], self.owner, job.baseline, started, started + span)
        self.held, self.job = held, job
        (self.output / f"recovery-input-{self.claim.fence}.json").write_bytes(
            canonical_bytes(job) + b"\n"
        )
        self.future = executor.submit(run_charging_isolated, image, self.claim, job)
        return True

    def finish(self):
        claim = self.claim
        try:
            result = self.future.result()
        except Exception:
            remove_owned(claim.fence, claim.owner)
            self.failed += 1
            close(self.conn, claim.fence, claim.owner, "failed")
        else:
            observed = read_current_hold(self.conn, self.run, self.asset, self.held.scope.source_id)
            assignment = latest_assignment(self.conn, self.run, self.asset)
            status = (
                "current"
                if observed is not None
                and observed[0] == self.held
                and assignment is not None
                and fingerprint(assignment) == self.job.baseline.assignment_sha256
                and time.time() < self.held.observed_real_life_s + RECOVERY_HOLD_REAL_LIFE_S
                and time.monotonic() < claim.deadline_monotonic_s
                else "historical"
            )
            raw = canonical_bytes(result)
            (self.output / f"recovery-result-{claim.fence}.json").write_bytes(raw + b"\n")
            close(self.conn, claim.fence, claim.owner, status, Jsonb(json.loads(raw)))
            self.completed += 1
        self.conn.commit()
        self.future = self.claim = self.held = self.job = None

    def stop(self):
        if self.claim is None:
            return
        if self.future is not None:
            try:
                self.future.result(timeout=35)
            except Exception:
                pass
        remove_owned(self.claim.fence, self.claim.owner)
        close(self.conn, self.claim.fence, self.claim.owner, "incomplete")
        self.conn.commit()
