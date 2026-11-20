"""One paced bus through authenticated inputs, isolated jobs, SQL, and the browser."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import psycopg
from fixtures import development_request
from psycopg import sql
from psycopg.rows import dict_row
from runtime import build_image

from evdt.energy.route_aware.context import parse_context
from evdt.energy.route_aware.wire import fingerprint
from evdt.monitoring.route_energy_views import provision

ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate, message, seconds=25):
    """Return the successful snapshot, without a second potentially different read."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.25)
    raise RuntimeError(message)


def run(output, *, case, terrain=None):
    from qualify_connected_speed_limit import prepare, read, request, require, wait_ready, write
    from qualify_live_speed_command import prepare_live
    from qualify_restarts import monitor

    project = "evdt-e2c-" + hashlib.sha256(output.name.encode()).hexdigest()[:12]
    runtime = ROOT / ".runtime/energy-v2" / project
    runtime.mkdir(parents=True, exist_ok=False)
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
        "case": case,
        "passed": False,
        "cleanup_verified": False,
        "bus_count": 1,
        "elevation_mode": "terrain" if terrain else "declared_flat",
    }
    controller = None
    old_environment = dict(os.environ)
    with (output / "commands.log").open("xb") as log:

        def shell(*args, timeout=90):
            result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
            log.write(result.stdout + result.stderr + b"\n")
            log.flush()
            require(result.returncode == 0, f"{args[:2]} failed; inspect commands.log")
            return result.stdout

        def compose(*args, **kwargs):
            return shell(*prefix, *args, **kwargs)

        def call(path, token=None, expected=200):
            status, body = request("http://127.0.0.1:18765", path, token or tokens["reader"])
            require(status == expected, f"{path} expected {expected}, received {status}")
            return body

        wait = wait_for

        try:
            with monitor(
                project, output, allow_replacement=True, finite_services=("source", "energy-worker")
            ):
                prepare_live(compose, shell, runtime, override, env, tokens, run_id, False)
                directory = runtime / "secrets"
                energy_token = secrets.token_urlsafe(36)
                reader_file = directory / "energy_reader"
                reader_file.write_text(energy_token + "\n")
                readers = read(directory / "readers.json")
                readers["readers"].append(
                    {"token": energy_token, "assets": ["bus-001"], "purpose": "energy_input"}
                )
                write(directory / "readers.json", readers)
                forecast_password = directory / "forecast_db"
                forecast_password.write_text(secrets.token_urlsafe(36) + "\n")
                config = read(override)
                services = config["services"]
                services["source"]["command"] = [
                    "python",
                    "-m",
                    "evdt.monitoring.adapter",
                    "--maximum-samples",
                    "135",
                    "--route-aware-energy",
                    "--pause-at-s",
                    "45",
                    "--pause-seconds",
                    "8",
                ]
                services["api"]["volumes"].append("./apps/dashboard/dist:/app/dashboard:ro")
                services["replay"] = {**services["api"]}
                write(override, config)
                image = build_image(output)
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
                    "replay",
                )
                wait_ready("http://127.0.0.1:18765")
                wait_ready("http://127.0.0.1:18766")
                db_id = compose("ps", "-q", "db").decode().strip()
                host = json.loads(shell("docker", "inspect", db_id))[0]["NetworkSettings"][
                    "Networks"
                ][project + "_fixture"]["IPAddress"]
                admin = psycopg.connect(
                    host=host,
                    dbname="evdt",
                    user="evdt_admin",
                    password=(directory / "admin_db").read_text().strip(),
                    connect_timeout=3,
                    row_factory=dict_row,
                )
                os.environ["EVDT_RUN_ID"] = run_id
                with admin:
                    admin.execute(
                        sql.SQL("ALTER ROLE evdt_forecast LOGIN PASSWORD {}").format(
                            sql.Literal(forecast_password.read_text().strip())
                        )
                    )
                    admin.commit()
                    compose("up", "-d", "--no-deps", "--pull", "never", "source")
                    source_id = compose("ps", "-q", "source").decode().strip()
                    wait(
                        lambda: any(
                            item.get("observation") is not None
                            for item in call("/v1/snapshot?asset_id=bus-001")["items"]
                        ),
                        "Source did not produce accepted readings",
                    )
                    network = runtime / "network.net.xml"
                    compose("cp", "source:/source-output/network.net.xml", str(network))
                    network_hash = hashlib.sha256(network.read_bytes()).hexdigest()
                    origin = {
                        "kind": "declared",
                        "source": "Connected functional check",
                        "reference": "Explicit flat height; this run does not qualify elevation accuracy",
                    }
                    if terrain:
                        manifest_raw = (ROOT / terrain["path"]).read_bytes()
                        require(
                            hashlib.sha256(manifest_raw).hexdigest() == terrain["sha256"],
                            "Terrain manifest changed after admission",
                        )
                        manifest = json.loads(manifest_raw)
                        origin = {
                            "kind": "observed",
                            "source": manifest["provider"],
                            "reference": manifest["sha256"],
                        }
                    osm = ROOT / "data/public/montreal-core-v1/roads.osm.xml"
                    bundle = runtime / "geometry.json"
                    write(
                        bundle,
                        {
                            "version": "route-energy-geometry/2.0.0",
                            "network": {
                                "path": network.relative_to(ROOT).as_posix(),
                                "sha256": network_hash,
                            },
                            "mode": report["elevation_mode"],
                            "terrain": terrain,
                            "osm": {
                                "path": osm.relative_to(ROOT).as_posix(),
                                "sha256": hashlib.sha256(osm.read_bytes()).hexdigest(),
                            }
                            if terrain
                            else None,
                            "origin": origin,
                            "vertical_datum": manifest["vertical_datum"]
                            if terrain
                            else "declared-local-height",
                            "flat_height_m": None if terrain else 0,
                        },
                    )
                    assignment, _ = development_request(run_id)
                    assignment = replace(
                        assignment,
                        network_sha256=network_hash,
                        geometry_bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
                    )
                    provision(admin, assignment)
                    admin.commit()
                    configuration = runtime / "controller.json"
                    write(
                        configuration,
                        {
                            "run_id": run_id,
                            "asset_id": "bus-001",
                            "monitoring_port": 18765,
                            "reader_credential_file": reader_file.relative_to(ROOT).as_posix(),
                            "db_host": host,
                            "db_password_file": forecast_password.relative_to(ROOT).as_posix(),
                            "geometry_bundle_file": bundle.relative_to(ROOT).as_posix(),
                            "worker_image_id": image,
                            "max_real_life_seconds": 180,
                        },
                    )
                    controller_output = output / "controller"
                    with (output / "controller.log").open("xb") as controller_log:
                        controller = subprocess.Popen(
                            [
                                sys.executable,
                                "scripts/route_aware_energy/controller.py",
                                "--configuration",
                                str(configuration.relative_to(ROOT)),
                                "--output",
                                str(controller_output.relative_to(ROOT)),
                            ],
                            cwd=ROOT,
                            env={**os.environ, "EVDT_ENERGY_RESOURCE_PROJECT": project},
                            stdout=controller_log,
                            stderr=controller_log,
                        )
                    endpoint = "/v2/energy-forecasts?asset_id=bus-001"
                    call("/v2/energy-input?asset_id=bus-001", tokens["replay"], 401)
                    call("/v2/energy-input?asset_id=bus-002", energy_token, 403)
                    call(endpoint, energy_token, 401)
                    call(endpoint, tokens["replay"], 401)
                    require(
                        request("http://127.0.0.1:18766", endpoint, tokens["replay"])[0] == 404,
                        "Replay exposed live forecasts",
                    )

                    def available_view():
                        view = call(endpoint)
                        return view if view["status"] == "available" and view["current"] else None

                    first = wait(
                        available_view,
                        "First admitted forecast did not arrive",
                        40,
                    )
                    require(
                        first["current"]["cutoff_working_s"] < 120,
                        "First forecast still depends on 120-second cutoff",
                    )
                    require(
                        "elevation_" + report["elevation_mode"]
                        in first["current"]["summary"]["ledger"]["assumptions"],
                        "Forecast elevation basis differs from the connected configuration",
                    )
                    write(output / "first-view.json", first)
                    map_endpoint = f"/v2/energy-forecasts/{first['current_fence']}/map"
                    call(map_endpoint + "?asset_id=bus-001", tokens["replay"], 401)
                    call(map_endpoint + "?asset_id=bus-002", tokens["reader"], 403)
                    projected = call(map_endpoint + "?asset_id=bus-001")
                    require(
                        projected["input_sha256"] == first["current"]["input_sha256"]
                        and projected["scope"] == first["current"]["scope"]
                        and projected["cutoff_working_s"] == first["current"]["cutoff_working_s"],
                        "Forecast map differs from its saved source/input",
                    )
                    write(
                        output / "browser-ready.json",
                        {
                            "monitoring_url": "http://127.0.0.1:18765",
                            "monitoring_token": tokens["reader"],
                            "run_id": run_id,
                            "elevation_label": "Terrain data" if terrain else "Declared flat",
                        },
                    )
                    wait(
                        lambda: (output / "browser-connected").is_file(),
                        "Browser did not connect",
                        25,
                    )
                    shell("docker", "pause", source_id)
                    try:
                        time.sleep(7)
                        stale = call(endpoint)
                        require(
                            stale["current"] is None and stale["items"],
                            "Stale source retained actionable forecast or lost history",
                        )
                        write(output / "stale-view.json", stale)
                        (output / "stale-ready").touch()
                        wait(
                            lambda: (output / "browser-stale").is_file(),
                            "Browser did not hide stale forecast",
                            15,
                        )
                    finally:
                        shell("docker", "unpause", source_id)
                    wait(
                        lambda: call(endpoint)["status"] == "available",
                        "Forecast did not recover",
                        30,
                    )
                    changed = replace(
                        assignment,
                        revision=2,
                        conditions=tuple(
                            replace(row, auxiliary_power_w=12_000) for row in assignment.conditions
                        ),
                    )
                    provision(admin, changed)
                    admin.commit()
                    wait(
                        lambda: any(
                            row["assignment_sha256"] == fingerprint(changed)
                            for row in call(endpoint)["items"]
                        ),
                        "Changed assumption did not trigger a forecast",
                        35,
                    )
                    (output / "recovered-ready").touch()
                    wait(
                        lambda: (output / "browser-result.json").is_file(),
                        "Browser did not finish",
                        90,
                    )
                    browser = read(output / "browser-result.json")
                    require(browser["passed"], "Browser verification failed")
                    require(
                        shell("docker", "wait", source_id, timeout=170).strip() == b"0",
                        "Bounded source failed",
                    )
                    wait(
                        lambda: len(call(endpoint)["items"]) >= 3,
                        "Periodic forecasts were not saved",
                    )
                    final = call(endpoint)
                    write(output / "final-view.json", final)
                    require(
                        final["current"] is None, "Finished source still has a current forecast"
                    )
                    require(
                        any(row["trigger"] == "periodic" for row in final["items"]),
                        "Periodic refresh not observed",
                    )
                    require(
                        bool(final["comparisons"]), "No completed prediction window was recorded"
                    )
                    captured = (
                        parse_context(bytes(row["raw_context"]))
                        for row in admin.execute(
                            "SELECT raw_context FROM m_route_contexts WHERE run_id=%s AND asset_id=%s ORDER BY working_s",
                            (run_id, "bus-001"),
                        )
                    )
                    dwell = [
                        (context, remaining)
                        for context in captured
                        if (remaining := context.remaining_dwell_working_s) is not None
                        and remaining > 0
                    ]
                    require(len(dwell) >= 2, "Missing consecutive dwell observations")
                    require(
                        len({context.plan.revision for context, _ in dwell}) == 1,
                        "Ordinary stop countdown changed the route revision",
                    )
                    release_times = [context.working_s + remaining for context, remaining in dwell]
                    require(
                        max(release_times) - min(release_times) <= 0.21,
                        "Observed countdown advanced faster or slower than working time",
                    )
                    write(output / "final-view.json", final)
                    compose(
                        "cp",
                        "source:/source-output/source-report.json",
                        str(output / "source-report.json"),
                    )
                    source_report = read(output / "source-report.json")
                    require(
                        source_report["passed"] and len(source_report["events"]) == 135,
                        "Source accounting failed",
                    )
                    history = call("/v1/observations?asset_id=bus-001&limit=136")
                    require(
                        len(history["items"]) == 135,
                        "Accepted observation count differs from original",
                    )
                    require(
                        all(
                            len(event["body"]["samples"]) == 20 for event in source_report["events"]
                        ),
                        "Twenty-signal contract changed",
                    )
                    controller.send_signal(__import__("signal").SIGINT)
                    require(controller.wait(timeout=40) == 0, "Controller did not stop cleanly")
                    controller_result = read(controller_output / "controller-result.json")
                    require(
                        controller_result["cleanup_verified"]
                        and controller_result["failed_jobs"] == 0,
                        "Worker lifecycle failed",
                    )
                    report.update(
                        passed=True,
                        run_id=run_id,
                        first_cutoff_working_s=first["current"]["cutoff_working_s"],
                        saved_forecasts=len(final["items"]),
                        comparisons=len(final["comparisons"]),
                        browser=browser,
                        controller=controller_result,
                        signals=20,
                        observations=135,
                        authenticated_scope_checked=True,
                        stop_countdown_samples=len(dwell),
                    )
        except (Exception, KeyboardInterrupt) as exc:
            report.update(passed=False, error=repr(exc))
            try:
                (output / "service-failure.log").write_bytes(
                    compose("logs", "--no-color", "--tail", "100")
                )
            except Exception as log_error:
                report["log_collection_error"] = repr(log_error)
            for name in ("run-manifest.json", "source-report.json", "network.net.xml"):
                try:
                    compose("cp", "source:/source-output/" + name, str(output / name))
                except Exception:
                    # A source that failed before creation has no such artifact.
                    report.setdefault("missing_source_artifacts", []).append(name)
        finally:
            try:
                if controller and controller.poll() is None:
                    controller.send_signal(__import__("signal").SIGINT)
                    controller.wait(timeout=40)
                (output / "browser-stop").touch()
                compose("down", "--timeout", "2", "--volumes", "--remove-orphans", timeout=60)
                for kind in ("container", "volume", "network"):
                    require(
                        not shell(
                            "docker",
                            kind,
                            "ls",
                            "-q",
                            "--filter",
                            "label=com.docker.compose.project=" + project,
                        ).strip(),
                        f"Owned {kind} cleanup incomplete",
                    )
                report["cleanup_verified"] = True
            except Exception as exc:
                report.update(passed=False, cleanup_error=repr(exc))
            os.environ.clear()
            os.environ.update(old_environment)
            write(output / "connected-result.json", report)
    return report
