"""Run advisory-only jobs when the existing Forecast Controller is idle."""

import json
import time
from functools import partial

from psycopg.types.json import Jsonb
from runtime import remove_owned, run_charging_isolated

from evdt.energy.route_aware.context import SourceContext
from evdt.energy.route_aware.scheduling import Claim, context_key
from evdt.energy.route_aware.wire import canonical_bytes, decode_record, fingerprint
from evdt.monitoring.charging_startup import startup_waiting


def reconcile(conn):
    """Call under the forecast worker lock before starting any new worker."""
    for row in conn.execute(
        "SELECT fence,owner FROM m_moving_charge_jobs WHERE status='running' FOR UPDATE"
    ).fetchall():
        remove_owned(row["fence"], str(row["owner"]))
        conn.execute(
            """UPDATE m_moving_charge_jobs
            SET status='incomplete',completed_at=clock_timestamp()
            WHERE fence=%s AND status='running'""",
            (row["fence"],),
        )
    conn.commit()


def station_state_key(bays, revisions, context):
    """Ignore progress within a bay's approach and distinguish a passed visit."""
    return fingerprint(
        sorted(
            (entry.bay.bay_id, revisions[entry.bay.bay_id])
            for entry in bays
            if not entry.has_passed(context)
        )
    )


def station_expiry(job, statuses, now, decision, *, rechecking=False):
    """Reconfirm the same declared intervals using a fresh source observation."""
    maximum_age = job.policy.max_status_age_real_life_s
    job_age = now - job.evaluated_real_life_s
    if (
        not 0
        <= job_age
        <= (
            job.baseline.policy.forecast_max_age_real_life_s
            if rechecking
            else job.baseline.policy.worker_deadline_real_life_s
        )
    ):
        return None
    current = {item.bay_id: item for item in statuses}
    planned = {
        candidate.bay_id: candidate
        for candidate in (decision.preferred, decision.fallback)
        if candidate is not None
    }
    expiry = now + maximum_age
    for offer in job.offers:
        before = offer.status
        after = current.get(before.bay_id)
        if (
            after is None
            or after.revision != before.revision
            or after.scenario_id != before.scenario_id
            or after.state != before.state
            or after.offered_power_w != before.offered_power_w
            or (
                before.offered_power_w is not None
                and (
                    after.open_until_working_s != before.open_until_working_s
                    or (
                        before.state == "occupied"
                        and after.available_from_working_s != before.available_from_working_s
                    )
                )
            )
            or after.observed_working_s < before.observed_working_s
            or not before.observed_real_life_s
            <= after.observed_real_life_s
            <= after.received_real_life_s
            <= now
        ):
            return None
        # For a completed option, the refreshed window must still reproduce the
        # original transfer start. Available intervals report "available now".
        candidate = planned.get(before.bay_id)
        if candidate is not None:
            try:
                window = after.window(
                    offer.stop.bay,
                    scenario_id=job.station_scenario_id,
                    now_real_life_s=now,
                    current_working_s=after.observed_working_s,
                    arrival_working_s=candidate.approach.forecast.summary.arrival_working_s,
                    status_max_age_real_life_s=maximum_age,
                )
            except ValueError:
                return None
            if window.charge_from_working_s != candidate.transfer.start_working_s:
                return None
        expiry = min(expiry, after.observed_real_life_s + maximum_age)
    return expiry if expiry > now else None


class IdleCharging:
    def __init__(self, conn, owner, configuration, output, charging_delay=0):
        self.charging_delay = charging_delay
        self.conn, self.owner, self.output = conn, owner, output
        self.configuration = configuration
        self.sha256 = fingerprint(configuration)
        self.scenario_sha256 = configuration.scenario.sha256
        self.future = self.claim = self.request = None
        self.last_seen = None
        self.review = None
        self.completed = self.failed = 0

    def validity(self, job):
        cutoff = job.baseline.state.working_s
        bounds = [cutoff + job.baseline.policy.forecast_max_age_working_s]
        offered_bays = {offer.status.bay_id: offer.status for offer in job.offers}
        bounds.extend(
            boundary
            for schedule in self.configuration.scenario.schedules
            if schedule.bay_id in offered_bays
            for entry in schedule.intervals
            for boundary in (entry.from_working_s, entry.until_working_s)
            if boundary > cutoff
        )
        return {
            "forecast_assignment_sha256": job.baseline.assignment_sha256,
            "route_revision": job.baseline.route.revision,
            "bay_positions": {
                entry.bay.bay_id: {
                    "external_index": entry.external_index,
                    "lane_id": entry.bay.lane_id,
                    "end_offset_m": entry.bay.end_offset_m,
                    "station_state": offered_bays[entry.bay.bay_id].state,
                    **(
                        {"branch_only": True, "detour_sha256": fingerprint(entry.detour)}
                        if entry.detour is not None
                        else {}
                    ),
                }
                for entry in self.configuration.bays
                if entry.bay.bay_id in offered_bays
            },
            "working_expires": min(bounds),
            "real_life_expires": min(
                [job.evaluated_real_life_s + job.policy.max_status_age_real_life_s]
                + [
                    offer.status.observed_real_life_s + job.policy.max_status_age_real_life_s
                    for offer in job.offers
                ]
            ),
        }

    def poll(self, scheduler, executor, image, snapshot, assignment, provider):
        if self.future is not None:
            if not self.future.done():
                return True
            self.finish(scheduler)
            return False
        if (
            scheduler.active is not None
            or scheduler.pending_reason is not None
            or scheduler.current_status(now_real_life_s=time.time(), monotonic_s=time.monotonic())
            != "current"
            or assignment is None
            or scheduler.latest is None
            or startup_waiting(self.charging_delay, scheduler.latest.state.working_s)
        ):
            return False
        accepted = scheduler.accepted_result
        if accepted is None or accepted.summary is None:
            return False
        accepted_request = scheduler.accepted_request
        latest = scheduler.latest
        worker_limit = min(
            self.configuration.budget_real_life_s + 3,
            latest.policy.worker_deadline_real_life_s,
        )
        # Leave room for the next regular forecast at the supported 1× pace.
        if accepted_request is None or (
            latest.state.working_s - accepted_request.state.working_s + worker_limit
            >= latest.policy.period_working_s
        ):
            return False
        floor = scheduler.latest.vehicle.reserve_soc + self.configuration.policy.margin_soc
        if accepted.summary.arrival_soc is not None and accepted.summary.minimum_soc >= floor:
            return False

        inputs = decode_record(SourceContext, snapshot["context"])
        if inputs.plan.edges != self.configuration.baseline_edges:
            # This profile authorizes one visit. The ordinary forecast still
            # follows the new authenticated route.
            return False
        now = time.time()
        if latest.state.working_s != inputs.working_s or not (
            0 <= now - latest.state.received_real_life_s <= latest.policy.source_max_age_real_life_s
        ):
            return False
        states = self.configuration.scenario.observe(
            run_id=latest.state.scope.run_id,
            working_s=latest.state.working_s,
            observed_real_life_s=latest.state.observed_real_life_s,
            received_real_life_s=latest.state.received_real_life_s,
            expected_sha256=self.scenario_sha256,
        )
        revisions = {item.bay_id: item.revision for item in states}
        station_state = station_state_key(self.configuration.bays, revisions, inputs)
        key = (accepted.input_sha256, station_state)
        if key == self.last_seen:
            return False
        self.last_seen = key
        try:
            alternate = (
                {"prepare_alternate": partial(provider, assignment), "network": provider.network}
                if any(entry.detour is not None for entry in self.configuration.bays)
                else {}
            )
            job = self.configuration.prepare_job(
                latest,
                inputs,
                provider(assignment, inputs),
                assignment_sha256=self.sha256,
                now_real_life_s=now,
                **alternate,
            )
        except ValueError:
            self.failed += 1
            return False
        row = self.conn.execute(
            """SELECT fence FROM m_route_energy_results
            WHERE run_id=%s AND asset_id=%s AND classification='current'
              AND input_sha256=%s""",
            (
                latest.state.scope.run_id,
                latest.state.scope.asset_id,
                accepted.input_sha256,
            ),
        ).fetchone()
        if row is None:
            self.conn.commit()
            return False
        baseline_fence = row["fence"]
        fence = self.conn.execute("SELECT nextval('m_route_forecast_fence') AS fence").fetchone()[
            "fence"
        ]
        inserted = self.conn.execute(
            """INSERT INTO m_moving_charge_jobs
            (fence,forecast_fence,owner,assignment_sha256,request_sha256,validity,station_state_sha256,status)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'running')
            ON CONFLICT(forecast_fence,station_state_sha256) DO NOTHING""",
            (
                fence,
                baseline_fence,
                self.owner,
                self.sha256,
                fingerprint(job),
                Jsonb(self.validity(job)),
                station_state,
            ),
        ).rowcount
        self.conn.commit()
        if not inserted:
            return False
        started = time.monotonic()
        self.claim = Claim(fence, self.owner, latest, started, started + worker_limit)
        self.request = job
        (self.output / f"charge-input-{fence}.json").write_bytes(canonical_bytes(job) + b"\n")
        self.future = executor.submit(run_charging_isolated, image, self.claim, job)
        return True

    def recheck(self, scheduler, snapshot):
        if self.review is None:
            return
        fence, job, decision, accepted_sha, expires = self.review
        now = time.time()
        if expires - now > job.policy.max_status_age_real_life_s / 2:
            return
        latest = scheduler.latest
        accepted = scheduler.accepted_result
        if (
            latest is None
            or accepted is None
            or accepted.input_sha256 != accepted_sha
            or scheduler.current_status(now_real_life_s=now, monotonic_s=time.monotonic())
            != "current"
            or context_key(latest) != context_key(job.baseline)
            or not job.baseline.state.working_s
            <= latest.state.working_s
            < self.validity(job)["working_expires"]
            or not 0
            <= now - latest.state.received_real_life_s
            <= latest.policy.source_max_age_real_life_s
            or snapshot.get("clock", {}).get("working_s") != latest.state.working_s
            or snapshot.get("context") is None
        ):
            return
        context = decode_record(SourceContext, snapshot["context"])
        if context.scope != latest.state.scope or context.working_s != latest.state.working_s:
            return
        statuses = self.configuration.scenario.observe(
            run_id=latest.state.scope.run_id,
            working_s=latest.state.working_s,
            observed_real_life_s=latest.state.observed_real_life_s,
            received_real_life_s=latest.state.received_real_life_s,
            expected_sha256=self.scenario_sha256,
        )
        renewed = station_expiry(job, statuses, now, decision, rechecking=True)
        if renewed is None or renewed <= expires:
            return
        self.conn.execute(
            """UPDATE m_moving_charge_jobs SET station_rechecked_until=%s
            WHERE fence=%s AND owner=%s AND status='current'""",
            (renewed, fence, self.owner),
        )
        self.conn.commit()
        self.review = fence, job, decision, accepted_sha, renewed

    def finish(self, scheduler):
        claim, job, future = self.claim, self.request, self.future
        payload = expiry = None
        try:
            result = future.result()
        except Exception:
            remove_owned(claim.fence, claim.owner)
            self.failed += 1
            status = "failed"
        else:
            latest = scheduler.latest
            now = time.time()
            current = (
                latest is not None
                and scheduler.pending_reason is None
                and context_key(latest) == context_key(claim.request)
                and 0
                <= latest.state.working_s - claim.request.state.working_s
                <= latest.policy.forecast_max_age_working_s
                and 0
                <= now - latest.state.received_real_life_s
                <= latest.policy.source_max_age_real_life_s
                and time.monotonic() < claim.deadline_monotonic_s
            )
            if current:
                # Compare status intervals again. Working-clock advances inside
                # the same interval are compatible; a transition invalidates it.
                statuses = self.configuration.scenario.observe(
                    run_id=latest.state.scope.run_id,
                    working_s=latest.state.working_s,
                    observed_real_life_s=latest.state.observed_real_life_s,
                    received_real_life_s=latest.state.received_real_life_s,
                    expected_sha256=self.scenario_sha256,
                )
                expiry = station_expiry(job, statuses, now, result.decision)
                current = expiry is not None
                preferred = result.decision.preferred
                if preferred is not None:
                    current = (
                        current
                        and latest.state.working_s
                        < preferred.approach.forecast.summary.arrival_working_s
                    )
            status = "current" if current else "historical"
            if current and preferred is not None and expiry is not None:
                self.review = (
                    claim.fence,
                    job,
                    result.decision,
                    scheduler.accepted_result.input_sha256,
                    expiry,
                )
            payload = Jsonb(json.loads(canonical_bytes(result)))
            (self.output / f"charge-result-{claim.fence}.json").write_bytes(
                canonical_bytes(result) + b"\n"
            )
            self.completed += 1
        self.close(status, payload, expiry if status == "current" else None)

    def close(self, status, payload=None, expiry=None):
        row = self.conn.execute(
            """UPDATE m_moving_charge_jobs SET status=%s,payload=%s,station_rechecked_until=%s,
            completed_at=clock_timestamp()
            WHERE fence=%s AND owner=%s AND status='running'""",
            (status, payload, expiry, self.claim.fence, self.owner),
        ).rowcount
        if row != 1:
            raise RuntimeError("Charging result differs from its owned job")
        self.conn.commit()
        self.future = self.claim = self.request = None

    def stop(self):
        if self.claim is None:
            return
        if self.future is not None:
            try:
                self.future.result(timeout=35)
            except Exception:
                pass
        remove_owned(self.claim.fence, self.owner)
        self.close("incomplete")
