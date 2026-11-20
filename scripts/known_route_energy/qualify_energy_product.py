"""One small owned database/API/worker/browser/restore integration; no load benchmark."""

import argparse
import copy
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from energy_holdout import sha, verify_release, write
from energy_runtime import ROOT, build_image, docker


def main():
    sys.path.insert(0, str(ROOT / "scripts/release"))
    from dashboard_service_guard import ReviewGuard

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--presentation-only",
        action="store_true",
        help="Focused browser check; never a complete integration qualification",
    )
    parser.add_argument(
        "--finish-e1",
        action="store_true",
        help="Complete original E1 functional and compatibility cases",
    )
    args = parser.parse_args()
    if args.finish_e1 and args.presentation_only:
        parser.error("E1 requires the complete integration")
    output = args.evidence_dir.resolve()
    if os.name != "nt" or not output.is_relative_to(ROOT / "evidence/p2") or output.exists():
        parser.error("Run on Windows with a new evidence/p2 directory")
    release = verify_release()
    output.mkdir(parents=True)
    token = uuid.uuid4().hex
    run = str(uuid.UUID(token))
    project = "evdt-e2c-" + token[:12]
    runtime = ROOT / ".runtime/p2-product" / token
    secret_dir = runtime / "secrets"
    fixture = runtime / "fixture"
    secret_dir.mkdir(parents=True)
    fixture.mkdir()
    report = {
        "passed": False,
        "scope": "Small controlled integration using retained training observations, not live fleet capacity or new accuracy evaluation",
        "run_id": run,
        "commands": [],
    }
    if args.presentation_only:
        report["scope"] = (
            "Focused browser presentation check only; not complete product integration, backup or access qualification"
        )
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("migrations/monitoring/*.sql"),
        *(p for p in ROOT.glob("apps/dashboard/src/**/*") if p.is_file()),
        # The read-only release audit consumes this report; it does not run here.
        *(
            p
            for p in ROOT.glob("scripts/known_route_energy/*.py")
            if p.name != "review_energy_advisory.py"
        ),
        ROOT / "ops/compose/compose.monitoring.yaml",
        ROOT / "ops/compose/compose.energy.yaml",
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "qualification/energy/product-profile.json",
        ROOT / "qualification/energy/product-profile-1.1.0.json",
        ROOT / "qualification/energy/station-navigation.json",
        ROOT / "qualification/browser/energy-e1.config.mjs",
        ROOT / "qualification/browser/energy-e1/closeout.spec.mjs",
        ROOT / "qualification/browser/energy.config.mjs",
        ROOT / "qualification/browser/energy/product.spec.mjs",
        ROOT / "scripts/release/dashboard_service_guard.py",
    ]
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}

    def inventory():
        return {
            name: sorted(docker(*argv).stdout.decode().splitlines())
            for name, argv in {
                "containers": ["ps", "-aq"],
                "volumes": ["volume", "ls", "-q"],
                "networks": ["network", "ls", "-q"],
            }.items()
        }

    initial = inventory()
    app = "evdt-p2-product:" + token
    app_built = False
    worker = None
    predictor = None
    predictor_invocation = None
    started = False
    guard = None
    log = (output / "commands.log").open("wb")

    def path(p):
        return "/mnt/" + p.resolve().as_posix()[0].lower() + p.resolve().as_posix()[2:]

    def command(argv, *, raw=None, timeout=180, check=True):
        execute = guard.run if guard else subprocess.run
        result = execute(argv, cwd=ROOT, input=raw, capture_output=True, timeout=timeout)
        log.write(result.stdout + result.stderr + b"\n")
        log.flush()
        report["commands"].append({"program": argv[0], "exit_code": result.returncode})
        if check and result.returncode:
            raise RuntimeError("Integration command failed; inspect private commands.log")
        return result

    def linux(*argv, **kwargs):
        return command(
            [sys.executable, "scripts/linux.py", "--timeout", "1200", "--", *argv], **kwargs
        )

    prefix = [
        "docker",
        "compose",
        "--project-directory",
        ".",
        "--env-file",
        path(runtime / "compose.env"),
        "-f",
        "ops/compose/compose.monitoring.yaml",
        "-f",
        "ops/compose/compose.energy.yaml",
        "-f",
        path(runtime / "fixture.yaml"),
        "-p",
        project,
        "--profile",
        "*",
    ]

    def compose(*argv, **kwargs):
        return linux(*prefix, *argv, **kwargs)

    def probe(action):
        return json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "probe",
                "python",
                "-m",
                "evdt.monitoring.energy_probe",
                action,
            ).stdout
        )

    def api(route, credential="bus-001", method="GET"):
        headers = (
            {
                "Authorization": "Bearer "
                + next(r for r in readers if r["label"] == credential)["token"]
            }
            if credential
            else {}
        )
        try:
            with urlopen(
                Request("http://127.0.0.1:18815" + route, headers=headers, method=method), timeout=5
            ) as response:
                return response.status, json.load(response)
        except HTTPError as e:
            return e.code, None

    def wait_for(fn, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if guard:
                guard.check()
            try:
                if fn():
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        raise RuntimeError("Bounded integration readiness deadline exceeded")

    def require(ok, message):
        if not ok:
            raise RuntimeError(message)

    try:
        print("Preparing retained training inputs and isolated service credentials", flush=True)
        training = (
            release["analysis"].parents[1]
            / "2026-11-04-energy-development-01/collection/nominal-01"
        )
        # The release does not authorize regeneration: verify the existing recorded files.
        collection = json.loads((training / "collection.json").read_bytes())
        for name, expected in collection["files_sha256"].items():
            require(sha(training / name) == expected, "Retained training file changed")
        manifest = json.loads((training / "plant/run-manifest.json").read_bytes())
        original_manifest = copy.deepcopy(manifest)
        write(fixture / "manifest.json", manifest)
        validation = fixture / "validation"
        validation.mkdir()
        for name in (
            "model.json",
            "predictions.json",
            *[p.name for p in release["analysis"].glob("*-job.json")],
        ):
            shutil.copyfile(release["analysis"] / name, validation / name)
        prefix_packets = [
            json.loads(line)
            for line in (training / "observations.jsonl").read_text().splitlines()
            if 60 <= json.loads(line)["sim_time_s"] <= 120
        ]
        require(len(prefix_packets) == 61, "Unexpected training prefix")
        (fixture / "prefix.jsonl").write_text("".join(json.dumps(p) + "\n" for p in prefix_packets))
        release_sha = sha(ROOT / "docs/models/energy-held-out-release.json")
        (fixture / "release-sha.txt").write_text(release_sha)
        mission = json.loads((training / "scenario/mission.json").read_bytes())
        base = json.loads((ROOT / "simulation/montreal/mission.json").read_bytes())
        require(
            mission["route_edges"] == base["route_edges"]
            and mission["anchors_lon_lat"] == base["anchors_lon_lat"],
            "Retained route geometry changed",
        )
        contexts = []
        for p in (ROOT / ".runtime/p1-uplink").glob("*/secrets/map_context.json"):
            c = json.loads(p.read_bytes())
            routes = [f for f in c["features"] if f["properties"].get("layer") == "route"]
            if len(routes) == 1 and routes[0]["properties"]["id"] == "montreal-core-loop-v1":
                contexts.append(c)
        require(bool(contexts), "Reviewed known-route context is unavailable")
        context = contexts[0]
        context.update(run_id=run, asset_id="bus-001")
        context["input_sha256"] = {k: manifest["input_sha256"][k] for k in context["input_sha256"]}
        write(secret_dir / "map_context.json", context)
        expected = {
            k: manifest[k]
            for k in (
                "schema_version",
                "catalog_version",
                "model_version",
                "input_sha256",
                "step_s",
                "signals",
                "heartbeat_deadline_s",
                "paced",
                "initial_energy_j",
                "capacity_j",
                "packages",
                "traci_protocol",
                "sumo_version",
            )
        }
        write(
            secret_dir / "run_policy.json",
            {
                "policy_version": "run-policy/1.0.0",
                "run_id": run,
                "mode": "simulation",
                "sources": [
                    {"asset_id": asset, "source_id": "sumo-adapter", "expected": expected}
                    for asset in ("bus-001", "bus-002")
                ],
            },
        )
        for key in (
            "admin_db",
            "ingest_db",
            "read_db",
            "registry_db",
            "replay_db",
            "forecast_db",
            "metrics",
            "ingest_mqtt",
            "adapter_mqtt",
            "adapter2_mqtt",
            "observer_mqtt",
            "replay_mqtt",
            "producer1",
            "producer2",
        ):
            (secret_dir / key).write_text(secrets.token_urlsafe(32) + "\n")
        readers = [
            {"label": "all", "assets": ["bus-001", "bus-002"], "token": secrets.token_urlsafe(32)},
            {"label": "bus-001", "assets": ["bus-001"], "token": secrets.token_urlsafe(32)},
            {
                "label": "energy-input",
                "purpose": "energy_input",
                "assets": ["bus-001"],
                "token": secrets.token_urlsafe(32),
            },
        ]
        (secret_dir / "energy_reader").write_text(readers[2]["token"] + "\n")
        write(secret_dir / "readers.json", {"readers": readers})
        write(secret_dir / "replay_readers.json", {"readers": []})
        write(secret_dir / "writers.json", {"writers": []})
        for name in ("mosquitto.conf", "passwords", "acl"):
            (secret_dir / name).write_text("")
        pins = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())
        node = json.loads((ROOT / "environments/engineering-images.json").read_bytes())["node"][
            "digest"
        ]
        env = {
            "EVDT_RUN_ID": run,
            "MONITORING_IMAGE": app,
            "SIMULATOR_IMAGE": app,
            "POSTGRES_IMAGE": pins["postgres"]["digest"],
            "MOSQUITTO_IMAGE": pins["mosquitto"]["digest"],
            "API_PORT": "18815",
            "REPLAY_PORT": "18816",
            "SECRET_DIRECTORY": path(secret_dir),
            "BROKER_CONFIG": path(secret_dir / "mosquitto.conf"),
            "BROKER_PASSWORDS": path(secret_dir / "passwords"),
            "BROKER_ACL": path(secret_dir / "acl"),
        }
        (runtime / "compose.env").write_text("".join(k + "=" + v + "\n" for k, v in env.items()))
        (runtime / "fixture.yaml").write_text(
            "services:\n  probe:\n    volumes:\n      - "
            + path(fixture)
            + ":/fixture\n  db-restore:\n    extends:\n      file: "
            + path(ROOT / "ops/compose/compose.monitoring.yaml")
            + "\n      service: db\n    volumes: !override [restore-database:/var/lib/postgresql]\nvolumes:\n  restore-database: {}\n"
        )
        if args.finish_e1:
            fixture_file = runtime / "fixture.yaml"
            content = fixture_file.read_text().replace("volumes:\n  restore-database: {}\n", "")
            content += (
                "  db-legacy:\n    extends:\n      file: "
                + path(ROOT / "ops/compose/compose.monitoring.yaml")
                + "\n      service: db\n    volumes: !override [legacy-database:/var/lib/postgresql]\nvolumes:\n  restore-database: {}\n  legacy-database: {}\n"
            )
            fixture_file.write_text(content)
        print("Checking host resources for the bounded Energy service review", flush=True)
        guard = ReviewGuard(output, project=project)
        print("Building the monitoring application and unchanged isolated worker", flush=True)
        linux(
            "docker",
            "build",
            "--target",
            "runtime",
            "--platform",
            "linux/amd64",
            "--build-arg",
            "PYTHON_IMAGE=" + pins["python"]["digest"],
            "--build-arg",
            "UV_IMAGE=" + pins["uv"]["digest"],
            "--build-arg",
            "NODE_IMAGE=" + node,
            "-f",
            "containers/monitoring.Dockerfile",
            "-t",
            app,
            ".",
            timeout=900,
        )
        app_built = True
        build_image(output)
        compose("config", "--quiet")
        started = True
        start = time.monotonic()
        compose("up", "-d", "db", "migrate", "api")
        wait_for(lambda: api("/health/ready", None)[0] == 200)
        report["startup_s"] = time.monotonic() - start
        require(report["startup_s"] <= 120, "Startup exceeded existing threshold")
        report["setup"] = probe("setup")
        report["validation_preparation_parity"] = probe("parity")
        compose(
            "run",
            "--rm",
            "--no-deps",
            "probe",
            "python",
            "-m",
            "evdt.monitoring.energy_views",
            "enable-writer",
        )
        for route in ("/v1/energy-input?asset_id=bus-001", "/v1/energy-forecasts?asset_id=bus-001"):
            require(api(route, None)[0] == 401, "Anonymous forecast access allowed")
            require(
                api(route.replace("bus-001", "bus-002"))[0] == 403,
                "Cross-asset forecast access allowed",
            )
        require(
            api("/v1/energy-forecasts?asset_id=bus-001", method="POST")[0] == 405,
            "Unexpected forecast write route",
        )
        require(
            api("/v1/energy-forecasts?asset_id=bus-002", "all")[1]["reason_codes"]
            == ["forecast_not_configured"],
            "Unconfigured asset misleading",
        )
        report["access"] = {"passed": True, "checks": 6}
        for route in ("/v1/catalog", "/v1/observations?asset_id=bus-001", "/v1/run"):
            require(
                api(route, "energy-input")[0] == 401,
                "Minimized energy credential gained general reader access",
            )
        require(
            api("/v1/energy-input?asset_id=bus-001", "energy-input")[0] == 200,
            "Energy-input credential cannot read its authorized input",
        )
        require(
            api("/v1/energy-input?asset_id=bus-002", "energy-input")[0] == 403,
            "Energy-input credential crossed assets",
        )
        report["input_credential"] = {"passed": True, "checks": 5}
        if args.finish_e1:
            from energy_e1_fixture import qualify

            e1 = qualify(
                compose,
                probe,
                linux,
                api,
                wait_for,
                require,
                path,
                runtime,
                output,
                env,
                readers,
                project,
            )
            record = e1.pop("record")
            report.update(e1)
        else:
            # Start the actual browser first, so the five-second input-age limit is unchanged.
            control = runtime / "browser"
            control.mkdir()
            write(
                control / "ready.json",
                {"token": readers[0]["token"], "asset_id": "bus-001", "run_id": run},
            )
            browser_output = output / "browser"
            browser_output.mkdir()
            node_exe = shutil.which("node.exe")
            require(bool(node_exe), "Qualified Windows Node is unavailable")
            browser_env = {
                **os.environ,
                "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".runtime/playwright-browsers"),
                "EVDT_BASE_URL": "http://127.0.0.1:18815",
                "EVDT_DASHBOARD_CONTROL": str(control),
                "EVDT_BROWSER_OUTPUT": str(browser_output),
            }
            browser_log = (output / "browser.log").open("wb")
            worker = subprocess.Popen(
                [
                    node_exe,
                    str(ROOT / "qualification/browser/node_modules/playwright/cli.js"),
                    "test",
                    "--config",
                    "energy.config.mjs",
                ],
                cwd=ROOT / "qualification/browser",
                env=browser_env,
                stdout=browser_log,
                stderr=browser_log,
            )
            wait_for(lambda: (control / "ready").exists(), seconds=40)
            prediction = output / "prediction"
            predictor_log_path = output / "predictor.log"
            predictor_log = predictor_log_path.open("wb")
            predictor_invocation = uuid.uuid4().hex
            predictor = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/linux.py",
                    "--run-id",
                    predictor_invocation,
                    "--timeout",
                    "180",
                    "--",
                    "python3",
                    "scripts/known_route_energy/run_monitoring_forecast.py",
                    "--asset",
                    "bus-001",
                    "--env-file",
                    path(runtime / "compose.env"),
                    "--project",
                    project,
                    "--credential-file",
                    path(secret_dir / "energy_reader"),
                    "--port",
                    "18815",
                    "--evidence-dir",
                    path(prediction),
                    "--wait-seconds",
                    "30",
                ],
                cwd=ROOT,
                stdout=predictor_log,
                stderr=predictor_log,
            )
            wait_for(
                lambda: (
                    b"Waiting for an admissible monitoring input" in predictor_log_path.read_bytes()
                ),
                seconds=25,
            )
            print(
                "Admitting 61 controlled packets, running both isolated forecasts and saving the immutable record",
                flush=True,
            )
            report["seed"] = probe("seed")
            require(
                guard.wait(predictor, timeout=120) == 0,
                "Bounded forecast controller failed; inspect private predictor.log",
            )
            predictor = None
            predictor_log.close()
            # The reusable command omits only the test overlay; the writer uses no fixture mount.
            record = json.loads((prediction / "record.json").read_bytes())
            require(record["status"] == "advisory", "Controlled forecast unavailable")
            wait_for(lambda: (control / "available-seen").exists(), seconds=15)
            wait_for(lambda: (control / "stale-seen").exists(), seconds=15)
            require(guard.wait(worker, timeout=30) == 0, "Actual dashboard browser checks failed")
            worker = None
            browser_log.close()
            report["browser"] = json.loads((browser_output / "result.json").read_bytes())
        if not args.presentation_only:
            # Re-saving exact content is idempotent; changed content with that input is refused.
            raw = json.dumps(record).encode()
            compose("run", "--rm", "--no-deps", "-T", "forecast-writer", raw=raw)
            bad = copy.deepcopy(record)
            bad["reason_codes"] = ["conflicting_content"]
            from evdt.energy.observations import fingerprint

            bad.pop("record_id")
            bad["record_id"] = fingerprint(bad)
            require(
                compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "forecast-writer",
                    raw=json.dumps(bad).encode(),
                    check=False,
                ).returncode
                != 0,
                "Conflicting forecast accepted",
            )
            # Refresh the trusted clock only: it must not rejuvenate the old reading.
            probe("resume")
            require(
                api("/v1/energy-forecasts?asset_id=bus-001")[1]["values"] is None,
                "Heartbeat made old forecast fresh",
            )
            probe("pause")
            require(
                api("/v1/energy-forecasts?asset_id=bus-001")[1]["reason_codes"]
                == ["source_paused"],
                "Paused forecast shown current",
            )
            unavailable = output / "unavailable"
            linux(
                "python3",
                "scripts/known_route_energy/run_monitoring_forecast.py",
                "--asset",
                "bus-001",
                "--env-file",
                path(runtime / "compose.env"),
                "--project",
                project,
                "--credential-file",
                path(secret_dir / "energy_reader"),
                "--port",
                "18815",
                "--evidence-dir",
                path(unavailable),
                timeout=120,
            )
            require(
                json.loads((unavailable / "record.json").read_bytes())["status"] == "unavailable",
                "Unavailable outcome not retained",
            )
            probe("resume")
            probe("advance")
            require(
                api("/v1/energy-input?asset_id=bus-001")[1]["reason_codes"]
                == ["prediction_cutoff_outside_evaluated_domain"],
                "New cutoff silently qualified",
            )
            history = api("/v1/energy-forecasts?asset_id=bus-001&limit=1")[1]
            older = api(
                "/v1/energy-forecasts?asset_id=bus-001&limit=1&before=" + str(history["next"])
            )[1]
            require(
                len(older["items"]) == 1
                and older["items"][0]["payload"]["record_id"] == record["record_id"],
                "Forecast history pagination lost record",
            )
            require(
                api("/v1/energy-forecasts/" + record["record_id"] + "?asset_id=bus-002", "all")[0]
                == 404,
                "Forecast detail crossed asset",
            )
            detail = api("/v1/energy-forecasts/" + record["record_id"] + "?asset_id=bus-001")[1]
            require(
                detail["historical"] and detail["item"]["payload"] == record,
                "Immutable historical forecast changed",
            )
            report["database_denial"] = probe("denial")
            report["accounting"] = probe("audit")
            expected_accounting = (
                {"records": 3, "available": 2, "unavailable": 1, "original_packets_intact": 183}
                if args.finish_e1
                else {"records": 2, "available": 1, "unavailable": 1, "original_packets_intact": 61}
            )
            require(
                report["accounting"] == expected_accounting,
                "Forecast/original accounting mismatch",
            )
            # Verify ordinary monitoring views still read these exact admitted originals.
            snap = api("/v1/snapshot?asset_id=bus-001")[1]
            require(len(snap["items"]) == 20, "Monitoring snapshot regressed")
            require(
                api(
                    "/v1/history?asset_id=bus-001&signal_id=motion.speed&as_of="
                    + snap["cursor"]
                    + "&from_s=60&to_s=120"
                )[1]["items"].__len__()
                == 61,
                "Original history regressed",
            )
            require(api("/v1/map-context?asset_id=bus-001")[0] == 200, "Map context regressed")
            report["monitoring_views"] = {
                "snapshot_signals": 20,
                "raw_history_readings": 61,
                "map_read_passed": True,
            }
            print(
                "Checking forecast data and permissions across a fresh-cluster backup restoration",
                flush=True,
            )
            compose("up", "-d", "db-restore")

            def admin(host, module, *argv):
                return compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "-e",
                    "DB_HOST=" + host,
                    "probe",
                    "python",
                    "-m",
                    module,
                    *argv,
                )

            source_inv = json.loads(admin("db", "evdt.recovery.database", "inventory").stdout)
            source_cluster = (
                admin("db", "evdt.recovery.database", "cluster").stdout.decode().strip()
            )
            wait_for(
                lambda: (
                    compose(
                        "exec",
                        "-T",
                        "db-restore",
                        "pg_isready",
                        "-U",
                        "evdt_admin",
                        "-d",
                        "evdt",
                        check=False,
                    ).returncode
                    == 0
                )
            )
            admin(
                "db-restore",
                "evdt.recovery.database",
                "bootstrap",
                "--source-cluster",
                source_cluster,
            )
            archive = compose(
                "exec", "-T", "db", "pg_dump", "-U", "evdt_admin", "-d", "evdt", "-Fc"
            ).stdout
            (output / "database.dump").write_bytes(archive)
            compose(
                "exec",
                "-T",
                "db-restore",
                "pg_restore",
                "-U",
                "evdt_admin",
                "-d",
                "evdt",
                "--exit-on-error",
                raw=archive,
            )
            target_inv = json.loads(
                admin("db-restore", "evdt.recovery.database", "inventory").stdout
            )
            require(source_inv == target_inv, "Restored data/schema/permissions inventory mismatch")
            require(
                sha(training / "plant/run-manifest.json")
                == collection["files_sha256"]["plant/run-manifest.json"],
                "Original training manifest changed",
            )
            require(original_manifest == manifest, "Private training manifest mutated")
            report["backup"] = {
                "passed": True,
                "fresh_cluster": True,
                "exact_data_schema_permissions": True,
                "forecast_rows": expected_accounting["records"],
                "original_packets": expected_accounting["original_packets_intact"],
            }
            if args.finish_e1:
                from energy_legacy_fixture import qualify_legacy

                report["legacy_backup"] = qualify_legacy(compose, admin, wait_for, require, output)
        guard.finish_measurement()
        report["host_guards_passed"] = True
        report["passed"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = str(exc)
    finally:
        if guard:
            guard.active = False  # Cleanup must remain available after a resource stop.
        if predictor is not None and predictor.poll() is None:
            try:
                if predictor_invocation:
                    subprocess.run(
                        [sys.executable, "scripts/linux.py", "--cancel", predictor_invocation],
                        cwd=ROOT,
                        timeout=20,
                        check=True,
                        capture_output=True,
                    )
                predictor.wait(timeout=60)
            except subprocess.SubprocessError:
                report.update(passed=False, predictor_cleanup_unconfirmed=True)
        if worker is not None and worker.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(worker.pid), "/T", "/F"], capture_output=True, timeout=15
            )
            worker.wait(timeout=10)
        try:
            if started:
                compose("down", "--volumes", "--remove-orphans", timeout=90)
            if app_built:
                linux("docker", "image", "rm", app)
            after = inventory()
            # Some removals become visible asynchronously after Compose exits.
            deadline = time.monotonic() + 8
            while initial != after and time.monotonic() < deadline:
                time.sleep(0.5)
                after = inventory()
            write(output / "cleanup-inventory.json", {"before": initial, "after": after})
            report["cleanup"] = {
                "passed": initial == after,
                "preexisting_volumes_preserved": len(
                    set(initial["volumes"]) & set(after["volumes"])
                ),
                "owned_application_image_removed": app_built,
            }
            require(initial == after, "Integration resources not fully cleaned")
        except Exception as exc:
            report.update(passed=False, cleanup_error=str(exc))
        if guard:
            try:
                guard.close()
            except Exception as exc:
                report.update(passed=False, resource_cleanup_error=str(exc))
        report["source_sha256"] = before
        if any(sha(ROOT / p) != h for p, h in before.items()):
            report.update(passed=False, source_changed=True)
        write(output / "qualification.json", report)
        log.close()
    print(
        f"{'P2-E1' if args.finish_e1 else 'P2-D1'} integration passed: {report['passed']}; private report: {output.relative_to(ROOT)}",
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
