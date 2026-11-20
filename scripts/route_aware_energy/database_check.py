"""Exercise real SQL claims, permissions, immutable results, and fresh-target restore."""

import json
import os
import time
from dataclasses import replace
from uuid import uuid4

from fixtures import development_request
from psycopg import errors, sql
from runtime import build_image, run_isolated, verify_image
from service_session import DatabaseSession

from evdt.energy.route_aware.admission import PreparedPlan
from evdt.energy.route_aware.comparisons import Comparisons
from evdt.energy.route_aware.residuals import EnergyObservation
from evdt.energy.route_aware.scenarios import RoadConditions, RouteScenario
from evdt.energy.route_aware.wire import fingerprint
from evdt.monitoring.repository import migrate
from evdt.monitoring.route_energy_views import provision, save_residual, save_result
from evdt.monitoring.route_scheduler_store import SchedulerStore
from evdt.recovery.database import cluster, inventory
from evdt.recovery.database import preflight as restore_preflight


def deny(conn, statement):
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(statement)
    except errors.InsufficientPrivilege:
        return
    raise RuntimeError("Role unexpectedly allowed: " + statement)


def run(output, *, case):
    from qualify_connected_speed_limit import prepare
    from qualify_restarts import monitor

    session = DatabaseSession(output)
    report = {"case": case, "passed": False, "cleanup_verified": False, "bus_started": False}
    run_id = str(uuid4())
    saved_environment = os.environ.copy()
    try:
        os.environ["EVDT_ENERGY_RESOURCE_PROJECT"] = session.project
        _, _, _ = prepare(session.runtime, output, run_id)
        with monitor(session.project, output, allow_replacement=True):
            primary = session.start()
            os.environ.update(
                session.environment(),
                EVDT_RUN_ID=run_id,
                RUN_POLICY_FILE=str(session.runtime / "secrets/run_policy.json"),
                MAP_CONTEXT_FILE=str(session.runtime / "secrets/map_context.json"),
            )
            migrate()
            migrate()
            report["migrations_idempotent"] = True
            assignment, request = development_request(run_id)
            # Exercise the real writer with a localized assumption reindexed
            # from the original route onto its remaining suffix.
            revision = "e" * 64
            scenario = RouteScenario(
                "localized-storage-check",
                assignment.conditions[0].origin,
                request.requested_real_life_s,
                roads=(RoadConditions(2, assignment.conditions),),
            )
            assignment = replace(assignment, scenario=scenario, scenario_route_revision=revision)
            route = replace(request.route, revision=revision)
            prepared = PreparedPlan(revision, route, (0,), 1, (2,))
            request = replace(
                request,
                route=route,
                state=replace(request.state, route_revision=revision),
                scenario=prepared.project_scenario(scenario),
                assignment_sha256=fingerprint(assignment),
            )
            with session.connect() as admin:
                admin.execute(
                    sql.SQL("ALTER ROLE evdt_forecast LOGIN PASSWORD {}").format(
                        sql.Literal(session.credentials["forecast"].read_text().strip())
                    )
                )
                assert provision(admin, assignment)
                assert not provision(admin, assignment)
                other_assignment = replace(assignment, revision=2, enabled=False)
                assert provision(admin, other_assignment)
            image = build_image(output)
            verify_image(image)
            report["worker_image_id"] = image
            with session.connect("forecast") as conn:
                store = SchedulerStore(conn, str(uuid4()))
                with store.ownership() as owned:
                    assert owned
                    with session.connect() as other:
                        competitor = SchedulerStore(other, str(uuid4()))
                        with competitor.ownership() as acquired:
                            assert not acquired
                    scheduler = store.load(run_id, assignment.asset_id)
                    now = time.time()
                    request = replace(
                        request,
                        requested_real_life_s=now,
                        state=replace(
                            request.state, observed_real_life_s=now, received_real_life_s=now
                        ),
                    )
                    scheduler.observe(request)
                    claim = store.claim(run_id, assignment.asset_id, scheduler, time.monotonic())
                    assert claim is not None
                    conn.commit()
                    assert (
                        store.claim(run_id, assignment.asset_id, scheduler, time.monotonic())
                        is None
                    )
                    started = time.monotonic()
                    result = run_isolated(
                        image, claim, deadline_monotonic_s=claim.deadline_monotonic_s
                    )
                    report["worker_elapsed_real_life_s"] = time.monotonic() - started
                    assert result.status == "available"
                    now = time.time()
                    scheduler.observe(
                        replace(
                            request,
                            requested_real_life_s=now,
                            state=replace(
                                request.state, observed_real_life_s=now, received_real_life_s=now
                            ),
                        )
                    )
                    completion = scheduler.finish(
                        result,
                        fence=claim.fence,
                        owner=claim.owner,
                        monotonic_s=time.monotonic(),
                        now_real_life_s=now,
                    )
                    assert completion.classification == "current"
                    store.close(
                        run_id,
                        assignment.asset_id,
                        fence=claim.fence,
                        claim_owner=claim.owner,
                        status=completion.classification,
                        outcome={"reason": completion.reason},
                    )
                    save_result(conn, assignment, claim, completion)
                    save_result(conn, assignment, claim, completion)
                    report["localized_projection_saved"] = (
                        assignment.scenario.roads[0].occurrence_index == 2
                        and claim.request.scenario.roads[0].occurrence_index == 0
                    )
                    assert report["localized_projection_saved"]
                    store.save(run_id, assignment.asset_id, scheduler)
                    conn.commit()
                    try:
                        with conn.transaction(force_rollback=True):
                            conn.execute(
                                """INSERT INTO m_route_energy_results
                                (fence,run_id,asset_id,source_id,assignment_sha256,input_sha256,result_sha256,classification,payload)
                                SELECT fence,run_id,asset_id,source_id,%s,input_sha256,result_sha256,classification,payload
                                FROM m_route_energy_results WHERE fence=%s ON CONFLICT DO NOTHING""",
                                (fingerprint(other_assignment), claim.fence),
                            )
                    except errors.RaiseException as exc:
                        assert "terminal fenced claim" in str(exc)
                    else:
                        raise RuntimeError("Direct writer substituted another assignment")
                    report["direct_assignment_substitution_rejected"] = True
                    assert (
                        conn.execute("SELECT count(*) AS n FROM m_route_energy_results").fetchone()[
                            "n"
                        ]
                        == 1
                    )
                    # These readings exercise persistence only; they are deliberately
                    # declared synthetic values and are not accuracy-study outcomes.
                    tracker = Comparisons()
                    tracker.add(claim.fence, claim.request, result)
                    initial = request.state.soc * request.vehicle.capacity_kwh * 3_600_000
                    for i in range(31):
                        tracker.observe(
                            EnergyObservation(
                                request.state.scope,
                                str(uuid4()),
                                f"{i:064x}",
                                request.route.revision,
                                request.state.working_s + i,
                                now + i,
                                initial - 10000 * i,
                                "not_charging",
                                "good",
                            )
                        )
                    assessed = tracker.assess(now + 31)
                    assert len(assessed) == 1
                    save_residual(conn, *assessed[0])
                    save_residual(conn, *assessed[0])
                    conn.commit()
                    for statement in (
                        "DELETE FROM m_route_energy_results",
                        "UPDATE m_route_energy_assignments SET revision=revision",
                        "UPDATE m_observations SET stream_seq=stream_seq",
                        "SELECT * FROM m_observations",
                        "UPDATE m_route_energy_residuals SET payload=payload",
                    ):
                        deny(conn, statement)
                    conn.commit()
                    now = time.time()
                    next_request = replace(
                        request,
                        requested_real_life_s=now,
                        state=replace(
                            request.state,
                            working_s=30,
                            observed_real_life_s=now,
                            received_real_life_s=now,
                        ),
                    )
                    scheduler.observe(next_request)
                    interrupted = store.claim(
                        run_id, assignment.asset_id, scheduler, time.monotonic()
                    )
                    assert interrupted is not None and interrupted.fence > claim.fence
                    conn.commit()
                    report["saved_result_sha256"] = fingerprint(result)
                    report["interrupted_fence"] = interrupted.fence
            with session.connect("read") as conn:
                assert (
                    conn.execute("SELECT count(*) AS n FROM m_route_energy_results").fetchone()["n"]
                    == 1
                )
                assert (
                    conn.execute("SELECT count(*) AS n FROM m_energy_forecasts").fetchone()["n"]
                    == 0
                )
                for statement in (
                    "DELETE FROM m_route_energy_results",
                    "UPDATE m_route_forecast_states SET state=state",
                ):
                    deny(conn, statement)
            with session.connect("replay") as conn:
                for table in (
                    "m_route_energy_results",
                    "m_route_forecast_states",
                    "m_route_energy_assignments",
                ):
                    deny(conn, "SELECT * FROM " + table)
            report["permission_and_idempotency_checks"] = True
            from charging_database_check import exercise

            report["moving_charge_handoff_verified"] = exercise(
                session, request.state.scope, claim.fence, fingerprint(assignment)
            )
            with session.connect() as conn:
                original_cluster = cluster(conn)
                before = inventory(conn)
                commands_before = inventory(conn, "commanding")
            archive = session.docker(
                "exec", primary, "pg_dump", "-U", "evdt_admin", "-d", "evdt", "-Fc", binary=True
            ).stdout
            (output / "private-backup.dump").write_bytes(archive)
            session.docker("stop", "--time", "5", primary)
            restored = session.start("restored")
            with session.connect() as conn:
                restore_preflight(conn, original_cluster)
                for role, credential in session.credentials.items():
                    if role != "admin":
                        conn.execute(
                            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                                sql.Identifier("evdt_" + role),
                                sql.Literal(credential.read_text().strip()),
                            )
                        )
                conn.execute("CREATE ROLE evdt_command NOLOGIN")
            session.docker(
                "exec",
                "-i",
                restored,
                "pg_restore",
                "-U",
                "evdt_admin",
                "-d",
                "evdt",
                "--exit-on-error",
                input_bytes=archive,
            )
            with session.connect() as conn:
                after = inventory(conn)
                assert before == after, "Restored data/schema/permissions/sequences differ"
                assert commands_before == inventory(conn, "commanding"), "Restored commands differ"
            with session.connect("forecast") as conn:
                recovered = SchedulerStore(conn, str(uuid4()))
                with recovered.ownership() as owned:
                    assert owned
                    state = recovered.load(run_id, assignment.asset_id)
                    assert state.active.fence == interrupted.fence
                    recovered.reconcile(
                        run_id, assignment.asset_id, state, owned_worker_stopped=True
                    )
                    conn.commit()
                    assert state.latest is None and state.active is None
                    row = conn.execute(
                        "SELECT status FROM m_route_forecast_jobs WHERE fence=%s",
                        (interrupted.fence,),
                    ).fetchone()
                    assert row["status"] == "incomplete"
                    assert (
                        conn.execute("SELECT nextval('m_route_forecast_fence') AS n").fetchone()[
                            "n"
                        ]
                        > interrupted.fence
                    )
            report.update(restore_inventory_equal=True, restart_fenced=True, passed=True)
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = repr(exc)
    finally:
        try:
            session.finish()
            report["cleanup_verified"] = True
        except Exception as exc:
            report.update(passed=False, cleanup_error=repr(exc))
        os.environ.clear()
        os.environ.update(saved_environment)
        (output / "database-result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
