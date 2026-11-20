"""Run the bounded route-aware Forecast Controller on the trusted Ubuntu host."""

import argparse
import ipaddress
import json
import os
import signal
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from geometry_provider import GeometryProvider  # noqa: E402
from runtime import remove_owned, run_isolated, verify_image  # noqa: E402

from evdt.energy.route_aware.admission import InputUnavailable  # noqa: E402
from evdt.energy.route_aware.comparisons import Comparisons  # noqa: E402
from evdt.energy.route_aware.product import observed_energy, prepare_input  # noqa: E402
from evdt.energy.route_aware.screening import estimate_impact  # noqa: E402
from evdt.energy.route_aware.wire import canonical_bytes, decode_record  # noqa: E402
from evdt.monitoring.database import connection  # noqa: E402
from evdt.monitoring.route_energy_views import save_residual, save_result  # noqa: E402
from evdt.monitoring.route_scheduler_store import SchedulerStore  # noqa: E402


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("The input endpoint cannot redirect credentials")


def private_path(value):
    path = (ROOT / value).resolve()
    if not any(path.is_relative_to(ROOT / directory) for directory in (".runtime", ".private")):
        raise ValueError("Controller configuration and credentials must stay in ignored storage")
    return path


class InputClient:
    def __init__(self, port, credential):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Local monitoring port required")
        self.base = f"http://127.0.0.1:{port}/v2/energy-input?"
        self.credential = credential.strip()
        if not 16 <= len(self.credential) <= 512 or any(c.isspace() for c in self.credential):
            raise ValueError("Dedicated input credential format is invalid")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def read(self, asset):
        request = Request(
            self.base + urlencode({"asset_id": asset}),
            headers={"Authorization": "Bearer " + self.credential},
        )
        with self.opener.open(request, timeout=3) as response:
            raw = response.read(8_388_609)
        if len(raw) > 8_388_608:
            raise ValueError("Trusted input response byte budget exceeded")
        return json.loads(raw)


def operate(
    conn,
    *,
    run,
    asset,
    client,
    provider,
    image,
    output,
    maximum_seconds,
    stop_requested,
    charging=None,
    charging_delay=0,
):
    from charging_idle import IdleCharging, reconcile
    from charging_recovery import RecoveryCharging
    from charging_recovery import reconcile as reconcile_recovery

    owner = str(uuid.uuid4())
    store = SchedulerStore(conn, owner)
    deadline = time.monotonic() + maximum_seconds
    report = {
        "version": "route-energy-controller/2.0.0",
        "owner": owner,
        "run_id": run,
        "asset_id": asset,
        "completed_jobs": 0,
        "failed_jobs": 0,
        "cleanup_verified": False,
        "stopped": False,
    }
    future = None
    scheduler = None
    claim_assignment = None
    comparisons = Comparisons()
    with store.ownership() as owned:
        if not owned:
            raise RuntimeError("Another Forecast Controller holds global worker ownership")
        reconcile(conn)
        reconcile_recovery(conn)
        advisory = IdleCharging(conn, owner, charging, output, charging_delay) if charging else None
        recovery = RecoveryCharging(conn, owner, run, asset, charging, output) if charging else None
        for row in store.running_claims():
            old = store.load(str(row["run_id"]), row["asset_id"])
            if (
                not old.active
                or old.active.fence != row["fence"]
                or old.active.owner != str(row["owner"])
            ):
                raise RuntimeError("Interrupted claim does not match durable scheduler state")
            remove_owned(row["fence"], str(row["owner"]))
            store.reconcile(str(row["run_id"]), row["asset_id"], old, owned_worker_stopped=True)
            conn.commit()
        scheduler = store.load(run, asset)
        store.reconcile(run, asset, scheduler, owned_worker_stopped=True)
        conn.commit()
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                while not stop_requested() and time.monotonic() < deadline:
                    assignment = None
                    snapshot = {}
                    try:
                        snapshot = client.read(asset)
                        assignment, request = prepare_input(
                            snapshot, run_id=run, asset_id=asset, prepare_route=provider
                        )
                        if (
                            not -0.05
                            <= time.time() - request.requested_real_life_s
                            <= request.policy.source_max_age_real_life_s
                        ):
                            raise InputUnavailable("input_transport_stale_or_future")
                        reference = (
                            scheduler.active.request
                            if scheduler.active
                            else scheduler.accepted_request
                        )
                        scheduler.observe(
                            request,
                            impact=estimate_impact(reference, request, scheduler.accepted_result),
                        )
                        try:
                            comparisons.observe(observed_energy(snapshot, request))
                        except InputUnavailable:
                            # Missing energy does not invalidate an otherwise admitted forecast.
                            pass
                    except InputUnavailable as exc:
                        latest = scheduler.latest
                        delivery_in_progress = (
                            exc.reason == "current_bound_observation_not_ready"
                            and latest is not None
                            and snapshot.get("clock", {}).get("scope")
                            == json.loads(canonical_bytes(latest.state.scope))
                            and 0
                            <= time.time() - latest.state.received_real_life_s
                            <= latest.policy.source_max_age_real_life_s
                        )
                        if not delivery_in_progress:
                            scheduler.observe(None, unavailable=exc.reason)
                    except (OSError, ValueError, TimeoutError):
                        scheduler.observe(None, unavailable="input_or_geometry_unavailable")
                    store.save(run, asset, scheduler)
                    conn.commit()
                    if future is not None and future.done():
                        claim = scheduler.active
                        if claim is None:
                            raise RuntimeError("Worker completion has no durable claim")
                        try:
                            result = future.result()
                        except Exception as exc:
                            # A failed cleanup must keep the durable claim occupied.
                            # Never release it for a retry while a worker may survive.
                            remove_owned(claim.fence, claim.owner)
                            scheduler.failed(
                                fence=claim.fence, owner=owner, monotonic_s=time.monotonic()
                            )
                            store.close(
                                run,
                                asset,
                                fence=claim.fence,
                                claim_owner=owner,
                                status="failed",
                                outcome={
                                    "reason": "worker_failed",
                                    "error_type": type(exc).__name__,
                                },
                            )
                            report["failed_jobs"] += 1
                        else:
                            completion = scheduler.finish(
                                result,
                                fence=claim.fence,
                                owner=owner,
                                monotonic_s=time.monotonic(),
                                now_real_life_s=time.time(),
                            )
                            store.close(
                                run,
                                asset,
                                fence=claim.fence,
                                claim_owner=owner,
                                status=completion.classification,
                                outcome={
                                    "reason": completion.reason,
                                    "result_status": result.status,
                                },
                            )
                            save_result(conn, claim_assignment, claim, completion)
                            comparisons.add(claim.fence, claim.request, result)
                            (output / f"result-{claim.fence}.json").write_bytes(
                                canonical_bytes(result) + b"\n"
                            )
                            report["completed_jobs"] += 1
                        store.save(run, asset, scheduler)
                        conn.commit()
                        future, claim_assignment = None, None
                    for fence, residual in comparisons.assess(time.time()):
                        save_residual(conn, fence, residual)
                    conn.commit()
                    charge_active = (
                        advisory is not None
                        and advisory.future is not None
                        and advisory.poll(
                            scheduler, executor, image, snapshot, assignment, provider
                        )
                    )
                    if advisory is not None:
                        advisory.recheck(scheduler, snapshot)
                    recovery_active = (
                        recovery is not None
                        and not stop_requested()
                        and not charge_active
                        and future is None
                        and recovery.poll(snapshot, executor, image, provider)
                    )
                    if (
                        not stop_requested()
                        and not charge_active
                        and not recovery_active
                        and future is None
                        and assignment is not None
                    ):
                        claim = store.claim(run, asset, scheduler, time.monotonic())
                        conn.commit()
                        if claim is not None:
                            if stop_requested():
                                break
                            claim_assignment = assignment
                            (output / f"input-{claim.fence}.json").write_bytes(
                                canonical_bytes(claim.request) + b"\n"
                            )
                            future = executor.submit(
                                run_isolated,
                                image,
                                claim,
                                deadline_monotonic_s=claim.deadline_monotonic_s,
                            )
                        elif advisory is not None:
                            advisory.poll(
                                scheduler, executor, image, snapshot, assignment, provider
                            )
                    time.sleep(0.25)
                report["stopped"] = True
                if stop_requested():
                    report["stop_reason"] = "operator_interrupt"
            except Exception as exc:
                report["error_type"] = type(exc).__name__
                raise
            finally:
                try:
                    if recovery is not None:
                        conn.rollback()
                        recovery.stop()
                        report["charging_recovery_completed_jobs"] = recovery.completed
                        report["charging_recovery_failed_jobs"] = recovery.failed
                    if advisory is not None:
                        conn.rollback()
                        advisory.stop()
                        report["charging_completed_jobs"] = advisory.completed
                        report["charging_failed_jobs"] = advisory.failed
                    if scheduler.active:
                        claim = scheduler.active
                        remove_owned(claim.fence, claim.owner)
                        if future is not None:
                            try:
                                future.result(timeout=35)
                            except Exception:
                                pass
                        # Check again after the execution thread's own cleanup.
                        remove_owned(claim.fence, claim.owner)
                        conn.rollback()
                        store.reconcile(run, asset, scheduler, owned_worker_stopped=True)
                        conn.commit()
                    report["cleanup_verified"] = True
                except Exception as exc:
                    report["cleanup_error_type"] = type(exc).__name__
                    raise
                finally:
                    report["comparisons_skipped"] = comparisons.skipped
                    report["comparisons_unfinished"] = len(comparisons.pending)
                    (output / "controller-result.json").write_text(
                        json.dumps(report, indent=2) + "\n", encoding="utf-8"
                    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if os.name == "nt":
        parser.error("Run through the project Linux launcher")
    # Finish the current database transaction at a safe point. Raising from the
    # signal handler can interrupt a claim between its in-memory and durable writes.
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    config = json.loads(private_path(args.configuration).read_bytes())
    expected = {
        "run_id",
        "asset_id",
        "monitoring_port",
        "reader_credential_file",
        "db_host",
        "db_password_file",
        "geometry_bundle_file",
        "worker_image_id",
        "max_real_life_seconds",
    }
    optional = {"charging_assignment_file", "include_yellow", "charging_startup_delay_working_s"}
    if (
        not expected <= set(config) <= expected | optional
        or type(config["max_real_life_seconds"]) is not int
        or not 1 <= config["max_real_life_seconds"] <= 14_400
        or type(config.get("include_yellow", False)) is not bool
    ):
        raise ValueError("Bounded controller configuration required")
    from evdt.monitoring.charging_startup import charging_startup_delay

    charging_delay = charging_startup_delay(config.get("charging_startup_delay_working_s", 0))
    if charging_delay and "charging_assignment_file" not in config:
        raise ValueError("A charging startup delay requires a charging assignment")
    run = str(uuid.UUID(config["run_id"]))
    address = ipaddress.ip_address(config["db_host"])
    if address.version != 4 or not address.is_private:
        raise ValueError("Local private Docker database address required")
    os.environ.update(
        EVDT_RUN_ID=run,
        DB_USER="evdt_forecast",
        DB_HOST=str(address),
        DB_PASSWORD_FILE=str(private_path(config["db_password_file"])),
    )
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / "evidence/energy-v2") or output.exists():
        raise ValueError("Choose a new private evidence/energy-v2 controller folder")
    output.mkdir(parents=True)
    client = InputClient(
        config["monitoring_port"], private_path(config["reader_credential_file"]).read_text()
    )
    provider = GeometryProvider(
        private_path(config["geometry_bundle_file"]),
        include_yellow=config.get("include_yellow", False),
    )
    charging = None
    if "charging_assignment_file" in config:
        from evdt.energy.moving_charge_assignment import StationAssignment

        raw = private_path(config["charging_assignment_file"]).read_bytes()
        if len(raw) > 512 * 1024:
            raise ValueError("Charging assignment exceeds its byte limit")
        charging = decode_record(StationAssignment, json.loads(raw))
        if (charging.run_id, charging.asset_id, charging.network_sha256) != (
            run,
            config["asset_id"],
            provider.network.sha256,
        ):
            raise ValueError("Charging assignment must match the active run, asset, and network")
    verify_image(config["worker_image_id"])
    with connection() as conn:
        conn.execute("SET statement_timeout='3000ms'")
        conn.commit()
        result = operate(
            conn,
            run=run,
            asset=config["asset_id"],
            client=client,
            provider=provider,
            image=config["worker_image_id"],
            output=output,
            maximum_seconds=config["max_real_life_seconds"],
            charging=charging,
            charging_delay=charging_delay,
            stop_requested=lambda: stopping,
        )
    print(json.dumps(result))
    return 0 if result["cleanup_verified"] and not result["failed_jobs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
