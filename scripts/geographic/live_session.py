"""One geographic SUMO bus through the normal monitoring and forecast services."""

import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from contextlib import suppress
from uuid import uuid4

from live_inputs import (
    ROOT,
    assignment,
    read,
    selected_services,
    time_scale,
    write,
)
from live_inputs import prepare as prepare_inputs
from live_validation import (
    initial_charge_receipt,
    reconcile,
    startup_forecast,
    validate_current_forecast,
)


def save_recording(prefix, output, env):
    """Keep a failed archive for inspection; accept only a readable custom dump."""
    path = output / "database.dump"
    with path.open("xb") as archive:
        result = subprocess.run(
            [*prefix, "exec", "-T", "db", "pg_dump", "-U", "evdt_admin", "-d", "evdt", "-Fc"],
            cwd=ROOT,
            env=env,
            stdout=archive,
            stderr=subprocess.PIPE,
            timeout=90,
        )
    if result.returncode or not path.stat().st_size:
        raise RuntimeError("Private recording backup failed")
    with path.open("rb") as archive:
        if archive.read(5) != b"PGDMP":
            raise RuntimeError("Private recording backup has an invalid archive header")
        archive.seek(0)
        check = subprocess.run(
            [*prefix, "exec", "-T", "db", "pg_restore", "--list"],
            cwd=ROOT,
            env=env,
            stdin=archive,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    if check.returncode:
        raise RuntimeError("Private recording backup is not a readable archive")


def release_services(compose, shell, output, project, report, *, backup_expected):
    """Release owned resources only after preserving a usable recording."""
    report["cleanup_verified"] = False

    def resources(kind):
        args = [
            "docker",
            kind,
            "ls",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + project,
        ]
        if kind == "container":
            args.append("-a")
        return shell(*args).decode().split()

    if backup_expected and (
        report.get("backup_saved") is not True or report.get("finalization_errors")
    ):
        receipt = {
            "version": 1,
            "trip": output.name,
            "project": project,
            "cleanup_verified": False,
            "backup_saved": False,
            "backup_warning": "Recording finalization is incomplete. Owned resources were retained.",
            "original_backup_saved": report.get("backup_saved") is True,
            "retained_volumes": [],
            "discovery_complete": False,
        }
        # The existing recovery command can retry the dump from the stopped DB.
        # Save ownership before stopping anything, and never remove these volumes.
        report.update(
            recovery_required=True,
            recovery_project=project,
        )
        try:
            retained = resources("volume")
            receipt["retained_volumes"] = retained
            report["retained_volumes"] = retained
            report["retained_containers"] = resources("container")
            receipt["discovery_complete"] = True
        finally:
            try:
                write(output / "cleanup-recovery.json", receipt)
            finally:
                # Discovery or an unwritable disk must not prevent shutdown.
                # Recovery can rediscover the project-labeled owners later.
                compose("stop", "--timeout", "5", timeout=90)
        raise RuntimeError(
            "Recording finalization is incomplete; stopped services and volumes need recovery"
        )

    compose("down", "--timeout", "5", "--volumes", "--remove-orphans", timeout=90)
    for kind in ("container", "network", "volume"):
        if resources(kind):
            raise RuntimeError("Owned resource remains: " + kind)
    report["cleanup_verified"] = True


def run(output, profile, *, preview_seconds=None):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from qualify_connected_speed_limit import prepare, request, wait_ready
    from qualify_live_speed_command import prepare_live
    from qualify_restarts import monitor
    from runtime import build_image

    from evdt.energy.route_aware.wire import canonical_bytes, fingerprint
    from evdt.monitoring.route_energy_views import provision

    enabled = selected_services(profile)
    speed = time_scale(profile.get("time_scale", 1))
    energy_enabled = "energy" in enabled
    replay_enabled = "replay" in enabled
    automatic = profile.get("moving_charge_mode") == "automatic-qualification"
    charge_commands = automatic or profile.get("moving_charge_mode") == "operator-development"
    automatic_policy = None
    project = "evdt-e2c-" + hashlib.sha256(output.name.encode()).hexdigest()[:12]
    runtime = ROOT / ".runtime/energy-v2" / project
    runtime.mkdir(parents=True)
    run_id = str(uuid4())
    tokens, env, override = prepare(runtime, output, run_id)
    env.update(API_PORT="18765", REPLAY_PORT="18766")
    prefix = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "-p",
        project,
        "-f",
        "ops/compose/compose.monitoring.yaml",
        "-f",
        str(override),
        "--profile",
        "source",
    ]
    report = {
        "version": "live-geographic-session/1.0.0",
        "status": "incomplete",
        "cleanup_verified": False,
        "project": project,
        "run_id": run_id,
        "journey": profile["journey_id"],
        "preview_seconds": preview_seconds,
        "services": ["monitoring", *enabled],
        "time_scale": speed,
        **({"charging_mode": "automatic-qualification"} if automatic else {}),
    }
    controller, source_id = None, None
    restriction_updater = input_client = None
    old_environment = dict(os.environ)
    compose_ready = False
    history_window_complete = False

    with (output / "commands.log").open("xb") as log:

        def shell(*args, timeout=90):
            result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
            log.write(result.stdout + result.stderr + b"\n")
            log.flush()
            if result.returncode:
                raise RuntimeError(f"{args[:2]} failed; inspect commands.log")
            return result.stdout

        def compose(*args, **kwargs):
            return shell(*prefix, *args, **kwargs)

        def call(path):
            status, body = request("http://127.0.0.1:18765", path, tokens["reader"])
            if status != 200:
                raise RuntimeError(f"Monitoring request returned {status}")
            return body

        try:
            with monitor(
                project, output, allow_replacement=True, finite_services=("source", "energy-worker")
            ):
                prepare_live(compose, shell, runtime, override, env, tokens, run_id, False)
                scenario, prepared, data, station_model = prepare_inputs(profile, runtime, run_id)
                directory = runtime / "secrets"
                reader_file, forecast_password = (
                    directory / "energy_reader",
                    directory / "forecast_db",
                )
                energy_token = secrets.token_urlsafe(36)
                reader_file.write_text(energy_token + "\n")
                forecast_password.write_text(secrets.token_urlsafe(36) + "\n")
                readers = read(directory / "readers.json")
                readers["readers"].append(
                    {"token": energy_token, "assets": ["bus-001"], "purpose": "energy_input"}
                )
                write(directory / "readers.json", readers)
                config = read(override)
                services = config["services"]
                source = services["source"]
                source["volumes"] += [
                    str(scenario) + ":/scenario:ro",
                    str(prepared / "roads.osm.xml") + ":/input/roads.osm.xml:ro",
                ]
                source["command"] = [
                    "python",
                    "-m",
                    "evdt.monitoring.adapter",
                    "--maximum-samples",
                    str(preview_seconds or 14400),
                    "--route-aware-energy",
                    "--time-scale",
                    str(speed),
                ]
                if preview_seconds is None:
                    controls = output / "control"
                    for name in ("requests", "status"):
                        control_dir = controls / name
                        control_dir.mkdir(parents=True)
                        mode = "ro" if name == "requests" else "rw"
                        source["volumes"].append(f"{control_dir}:/trip-control/{name}:{mode}")
                    source["command"] += ["--pause-control", "/trip-control"]
                services["api"]["volumes"].append("./apps/dashboard/dist:/app/dashboard:ro")
                services["replay"] = {**services["api"]}
                completion = runtime / "monitoring-lifecycle"
                completion.mkdir()
                services["api"]["volumes"] = [
                    *services["api"]["volumes"],
                    f"{completion}:/run/evdt-monitoring-lifecycle:ro",
                ]
                services["api"]["environment"] = {
                    **services["api"]["environment"],
                    "EVDT_LOCAL_TRIP_CONTROL": "1",
                    "EVDT_MONITORING_COMPLETION_FILE": "/run/evdt-monitoring-lifecycle/closed",
                    **(
                        {
                            "EVDT_MOVING_CHARGE_MARGIN_SOC": str(station_model.policy.margin_soc),
                            "EVDT_CHARGING_STARTUP_DELAY_WORKING_S": str(
                                profile.get("charging_startup_delay_working_s", 0)
                            ),
                        }
                        if station_model is not None
                        else {}
                    ),
                }
                if charge_commands:
                    from live_charging import configure

                    configure(
                        services,
                        config,
                        runtime,
                        scenario,
                        station_model,
                        env["MONITORING_IMAGE"],
                        recovery=profile.get("moving_charge_recovery") is True,
                        outage=profile.get("qualification_charger_outage"),
                        automatic=automatic,
                        queue_trial=profile.get("qualification_charge_queue") is True,
                    )
                    services["api"]["environment"]["EVDT_MOVING_CHARGE_OPERATOR_ASSET"] = "bus-001"
                    services["api"]["environment"]["EVDT_DASHBOARD_OPERATOR_FILE"] = (
                        "/run/secrets/command_operator"
                    )
                    services["api"]["secrets"] = [
                        *services["api"].get("secrets", []),
                        "command_operator",
                    ]
                    if profile.get("moving_charge_recovery") is True:
                        services["api"]["environment"]["EVDT_MOVING_CHARGE_RECOVERY"] = "1"
                    if automatic:
                        services["api"]["environment"]["EVDT_MOVING_CHARGE_AUTOMATIC"] = "1"
                    if profile.get("qualification_charge_queue") is True:
                        services["api"]["environment"]["EVDT_MOVING_CHARGE_QUEUE_TRIAL"] = "1"
                write(override, config)
                (directory / "run_policy.json").unlink()
                compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--volume",
                    str(directory) + ":/policy",
                    "--entrypoint",
                    "python",
                    "source",
                    "-m",
                    "evdt.monitoring.provision_policy",
                    "--run-id",
                    run_id,
                    "--output",
                    "/policy/run_policy.json",
                )
                compose_ready = True
                if (output / "stop").exists():
                    raise KeyboardInterrupt
                image = build_image(output) if energy_enabled else None
                compose(
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "45",
                    "--pull",
                    "never",
                    "registry",
                    "api",
                    "ingest",
                    "sender",
                    *(["command-api"] if charge_commands else []),
                    *(["replay"] if replay_enabled else []),
                )
                wait_ready("http://127.0.0.1:18765")
                if replay_enabled:
                    wait_ready("http://127.0.0.1:18766")
                if automatic:
                    from live_automatic import AutomaticPolicy

                    command_id = compose("ps", "-q", "command-api").decode().strip()
                    command_host = json.loads(shell("docker", "inspect", command_id))[0][
                        "NetworkSettings"
                    ]["Networks"][project + "_fixture"]["IPAddress"]
                    wait_ready(f"http://{command_host}:8000")
                    automatic_policy = AutomaticPolicy(
                        command_host,
                        run_id,
                        tokens["operator"],
                        directory / "command_moving_charge_policy",
                    )
                    if profile.get("qualification_auto_override") is True:
                        automatic_policy.preempt()
                db_id = compose("ps", "-q", "db").decode().strip()
                host = json.loads(shell("docker", "inspect", db_id))[0]["NetworkSettings"][
                    "Networks"
                ][project + "_fixture"]["IPAddress"]
                if energy_enabled:
                    from controller import InputClient
                    from geometry_provider import GeometryProvider
                    from live_assignments import RestrictionUpdater

                    os.environ["EVDT_RUN_ID"] = run_id
                    assigned = assignment(
                        runtime,
                        run_id,
                        data["recipe"],
                        pace=profile.get("time_scale", 1),
                        source_distance=profile.get("forecast_candidate") == "sumo-source-distance",
                    )
                    if data["recipe"].get("speed_zones"):
                        restriction_updater = RestrictionUpdater(
                            assigned,
                            data["recipe"],
                            data["mission"]["route_edges"],
                            GeometryProvider(
                                runtime / "geometry.json",
                                include_yellow=bool(assigned.policy.sumo_yellow_clearance),
                            ),
                        )
                        input_client = InputClient(18765, energy_token)
                        assigned = restriction_updater.current
                    with psycopg.connect(
                        host=host,
                        dbname="evdt",
                        user="evdt_admin",
                        password=(directory / "admin_db").read_text().strip(),
                        connect_timeout=3,
                        row_factory=dict_row,
                    ) as admin:
                        admin.execute(
                            sql.SQL("ALTER ROLE evdt_forecast LOGIN PASSWORD {}").format(
                                sql.Literal(forecast_password.read_text().strip())
                            )
                        )
                        provision(admin, assigned)
                        admin.commit()
                    configuration = runtime / "controller.json"
                    charging_configuration = {}
                    if station_model is not None:
                        local = runtime / "charging-assignment.json"
                        local.write_bytes(canonical_bytes(station_model) + b"\n")
                        charging_configuration["charging_assignment_file"] = local.relative_to(
                            ROOT
                        ).as_posix()
                        charging_configuration["charging_startup_delay_working_s"] = profile.get(
                            "charging_startup_delay_working_s", 0
                        )
                    write(
                        configuration,
                        {
                            "run_id": run_id,
                            "asset_id": "bus-001",
                            "monitoring_port": 18765,
                            "reader_credential_file": reader_file.relative_to(ROOT).as_posix(),
                            "db_host": host,
                            "db_password_file": forecast_password.relative_to(ROOT).as_posix(),
                            "geometry_bundle_file": (runtime / "geometry.json")
                            .relative_to(ROOT)
                            .as_posix(),
                            "include_yellow": bool(assigned.policy.sumo_yellow_clearance),
                            "worker_image_id": image,
                            "max_real_life_seconds": profile["maximum_real_life_s"],
                            **charging_configuration,
                        },
                    )
                    with (output / "controller.log").open("xb") as controller_log:
                        controller = subprocess.Popen(
                            [
                                sys.executable,
                                "scripts/route_aware_energy/controller.py",
                                "--configuration",
                                str(configuration.relative_to(ROOT)),
                                "--output",
                                str((output / "controller").relative_to(ROOT)),
                            ],
                            cwd=ROOT,
                            env={**os.environ, "EVDT_ENERGY_RESOURCE_PROJECT": project},
                            stdout=controller_log,
                            stderr=controller_log,
                        )
                if (output / "stop").exists():
                    raise KeyboardInterrupt
                compose("up", "-d", "--no-deps", "--pull", "never", "source")
                source_id = compose("ps", "-q", "source").decode().strip()
                access = {
                    "run_id": run_id,
                    "monitoring_url": "http://127.0.0.1:18765",
                    "monitoring_token": tokens["reader"],
                    **({"charging_operator_token": tokens["operator"]} if charge_commands else {}),
                    **(
                        {
                            "replay_url": "http://127.0.0.1:18766",
                            "replay_token": tokens["replay"],
                        }
                        if replay_enabled
                        else {}
                    ),
                    "services": ["monitoring", *enabled],
                    "title": profile["title"],
                }
                if not charge_commands:
                    write(output / "ready.json", access)
                deadline = time.monotonic() + (
                    preview_seconds / speed + 120
                    if preview_seconds
                    else profile["maximum_real_life_s"]
                )
                startup_deadline = time.monotonic() + 180
                startup_verified, next_startup_check = False, 0.0
                next_view_check = 0.0
                while time.monotonic() < deadline:
                    if (output / "stop").exists():
                        raise KeyboardInterrupt
                    if (output / "linux-guard.json").exists():
                        raise RuntimeError("Linux resource guard stopped this session")
                    if controller and controller.poll() is not None:
                        raise RuntimeError("Forecast Controller exited before the trip finished")
                    if energy_enabled and time.monotonic() >= next_view_check:
                        energy_view = call("/v2/energy-forecasts?asset_id=bus-001")
                        with (output / "forecast-pacing.jsonl").open("a") as handle:
                            handle.write(
                                json.dumps(
                                    {
                                        key: energy_view.get(key)
                                        for key in (
                                            "as_of_real_life_s",
                                            "status",
                                            "reason",
                                            "current_fence",
                                            "working_age_s",
                                            "real_life_age_s",
                                            "lifecycle",
                                        )
                                    }
                                )
                                + "\n"
                            )
                        validate_current_forecast(energy_view, assigned.policy)
                        next_view_check = time.monotonic() + 1
                    if restriction_updater:
                        snapshot = input_client.read("bus-001")
                        candidate = restriction_updater.candidate(snapshot)
                        if candidate is not None:
                            with psycopg.connect(
                                host=host,
                                dbname="evdt",
                                user="evdt_admin",
                                password=(directory / "admin_db").read_text().strip(),
                                connect_timeout=3,
                                row_factory=dict_row,
                            ) as admin:
                                provision(admin, candidate)
                                admin.commit()
                            restriction_updater.committed(candidate)
                            update = {
                                "revision": candidate.revision,
                                "assignment_sha256": fingerprint(candidate),
                                "route_revision": candidate.scenario_route_revision,
                                "working_s": snapshot["context"]["working_s"],
                                "external_index": snapshot["context"]["external_index"],
                                "remaining_stops": len(
                                    snapshot["context"]["plan"]["remaining_stops"]
                                ),
                                "restricted_occurrences": len(candidate.scenario.roads),
                            }
                            with (output / "assignment-updates.jsonl").open("a") as updates:
                                updates.write(json.dumps(update) + "\n")
                            report["restriction_assignment_updates"] = candidate.revision - 1
                    if automatic_policy and startup_verified:
                        policy_state = automatic_policy.poll()
                        if policy_state:
                            with (output / "automatic-policy.jsonl").open("a") as policy_log:
                                policy_log.write(
                                    json.dumps({"state": policy_state, "real_life_s": time.time()})
                                    + "\n"
                                )
                    if not startup_verified and time.monotonic() >= next_startup_check:
                        # Reuse the current poll already checked for freshness above.
                        # A second read can straddle a source update and miss that result.
                        view = energy_view if energy_enabled else {"current": None, "items": []}
                        snapshot = call("/v1/snapshot?asset_id=bus-001")
                        present = sum(
                            item.get("observation") is not None for item in snapshot["items"]
                        )
                        first = startup_forecast(view)
                        startup_verified = present == 20 and (
                            not energy_enabled or first is not None
                        )
                        if startup_verified:
                            context = call("/v1/map-context?asset_id=bus-001")
                            if context.get("mission_id") != profile["journey_id"]:
                                raise RuntimeError(
                                    "Dashboard map does not match the selected journey"
                                )
                            write(
                                output / "startup-check.json",
                                {
                                    "passed": True,
                                    "signals": present,
                                    "services": ["monitoring", *enabled],
                                    "first_forecast": first,
                                },
                            )
                            if charge_commands:
                                # Verify the normal forecast before charging can
                                # intentionally suspend it.
                                temporary = output / "ready.tmp"
                                write(temporary, access)
                                temporary.replace(output / "ready.json")
                        elif time.monotonic() > startup_deadline:
                            write(
                                output / "startup-failure.json",
                                {"signals": present, "forecast": view},
                            )
                            raise RuntimeError(
                                "Live readings and current forecast did not become available within three minutes"
                            )
                        next_startup_check = time.monotonic() + (
                            1 if charge_commands else max(1, 5 / speed)
                        )
                    state = json.loads(shell("docker", "inspect", source_id))[0]["State"]
                    if not state["Running"]:
                        (output / "driving-ended").touch()
                        if state["OOMKilled"]:
                            raise RuntimeError("Vehicle source stopped with an error")
                        break
                    time.sleep(1)
                else:
                    raise TimeoutError("Live trip exceeded its bounded duration")
                compose(
                    "cp",
                    "source:/source-output/source-report.json",
                    str(output / "source-report.json"),
                )
                source_report = read(output / "source-report.json")
                moving = source_report["plant_summary"].get("moving_charge", {})
                interrupted = moving.get("interrupted") is True
                if state["ExitCode"] and not interrupted:
                    raise RuntimeError("Vehicle source stopped with an error")
                if not interrupted and (
                    not source_report["passed"]
                    or not (preview_seconds or source_report["plant_summary"]["mission_finished"])
                ):
                    raise RuntimeError("Journey did not reach its destination")
                charge_receipt = initial_charge_receipt(source_report, data["recipe"], profile)
                write(output / "initial-charge.json", charge_receipt)
                if not charge_receipt["passed"]:
                    raise RuntimeError(
                        "SUMO did not insert the bus at the requested starting charge"
                    )
                drain_deadline = time.monotonic() + 30
                while time.monotonic() < drain_deadline:
                    with psycopg.connect(
                        host=host,
                        dbname="evdt",
                        user="evdt_admin",
                        password=(directory / "admin_db").read_text().strip(),
                        connect_timeout=3,
                    ) as admin:
                        count = admin.execute(
                            "SELECT count(*) FROM m_observations WHERE run_id=%s", (run_id,)
                        ).fetchone()[0]
                    if count == len(source_report["events"]):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("Accepted event accounting did not drain")
                with psycopg.connect(
                    host=host,
                    dbname="evdt",
                    user="evdt_admin",
                    password=(directory / "admin_db").read_text().strip(),
                    connect_timeout=3,
                ) as admin:
                    committed = admin.execute(
                        "SELECT event_id,wire_sha256 FROM m_observations WHERE run_id=%s", (run_id,)
                    ).fetchall()
                    sample_counts = admin.execute(
                        "SELECT signal_id,count(*) FROM m_sample_history WHERE run_id=%s GROUP BY signal_id",
                        (run_id,),
                    ).fetchall()
                write(
                    output / "recording-check.json",
                    reconcile(source_report["events"], committed, sample_counts),
                )
                if interrupted:
                    report.update(
                        events=count,
                        working_s=source_report["plant_summary"]["completed_sim_time_s"],
                        charging_interruption=moving["events"][-1],
                        charging_interruption_snapshot=moving.get("interruption_snapshot"),
                    )
                    raise RuntimeError(
                        "Charging interrupted: the approved station became unavailable"
                    )
                view = (
                    call("/v2/energy-forecasts?asset_id=bus-001")
                    if energy_enabled
                    else {"items": []}
                )
                write(output / "final-forecast-view.json", view)
                if (energy_enabled and not view["items"]) or not startup_verified:
                    raise RuntimeError(
                        "Trip ended before the selected monitoring and forecast startup checks passed"
                    )
                report.update(
                    status="preview_complete" if preview_seconds else "arrived",
                    events=count,
                    saved_forecasts=len(view["items"]),
                    working_s=source_report["plant_summary"]["completed_sim_time_s"],
                )
                write(
                    output / "arrived.json",
                    {
                        "status": report["status"],
                        "history_minutes": 5 if not preview_seconds else 0,
                    },
                )
                end = time.monotonic() + (0 if preview_seconds else 300)
                while time.monotonic() < end and not (output / "stop").exists():
                    if (output / "linux-guard.json").exists():
                        raise RuntimeError("Linux resource guard stopped the history window")
                    time.sleep(1)
                history_window_complete = time.monotonic() >= end
        except (Exception, KeyboardInterrupt) as exc:
            report.update(
                status="stopped_incomplete"
                if isinstance(exc, KeyboardInterrupt)
                else "failed_or_incomplete",
                error=repr(exc),
            )
        finally:
            # Finalization is bounded and restricted to this session's resources.
            with suppress(OSError):
                (output / "driving-ended").touch()
            controller_result = output / "controller/controller-result.json"
            forecast_closed = not energy_enabled
            for action in ("controller", "source", "logs", "backup", "cleanup"):
                try:
                    if action == "controller" and controller:
                        if controller.poll() is None:
                            controller.send_signal(signal.SIGINT)
                            controller.wait(timeout=45)
                        if controller_result.is_file():
                            report["controller"] = read(controller_result)
                            forecast_closed = (
                                report["controller"].get("cleanup_verified") is True
                                and report["controller"].get("failed_jobs") == 0
                            )
                        if not forecast_closed and (
                            "controller" in report
                            or report["status"] in ("arrived", "preview_complete")
                        ):
                            raise RuntimeError("Forecast Controller finalization was not verified")
                    elif action == "source" and source_id:
                        compose("stop", "--timeout", "20", "source", timeout=35)
                        for name in ("source-report.json", "run-manifest.json", "plant-steps.json"):
                            compose("cp", "source:/source-output/" + name, str(output / name))
                        if charge_commands:
                            compose(
                                "cp",
                                "source:/source-output/charging-stations.xml",
                                str(output / "charging-stations.xml"),
                            )
                    elif action == "logs":
                        (output / "services.log").write_bytes(
                            compose("logs", "--no-color", "--tail", "100")
                        )
                    elif action == "backup" and compose_ready:
                        save_recording(prefix, output, env)
                        report["backup_saved"] = True
                    elif action == "cleanup":
                        try:
                            if (
                                report["status"] == "arrived"
                                and history_window_complete
                                and report.get("backup_saved")
                                and not report.get("finalization_errors")
                                and not (output / "stop").exists()
                                and not (output / "linux-guard.json").exists()
                                and forecast_closed
                            ):
                                (completion / "closed").touch()
                                # Deliver the final snapshot before removing the HTTP service.
                                # A healthy stream emits every 250 ms.
                                time.sleep(2)
                        except (OSError, ValueError) as exc:
                            report.setdefault("finalization_errors", {})["monitoring_close"] = repr(
                                exc
                            )
                        release_services(
                            compose,
                            shell,
                            output,
                            project,
                            report,
                            backup_expected=compose_ready,
                        )
                except Exception as exc:
                    report.setdefault("finalization_errors", {})[action] = repr(exc)
            if report.get("finalization_errors"):
                report["status"] = "failed_or_incomplete"
            os.environ.clear()
            os.environ.update(old_environment)
            write(output / "session-result.json", report)
    return report
