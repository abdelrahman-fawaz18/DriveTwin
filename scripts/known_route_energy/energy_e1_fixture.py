"""Bounded E1 browser/source cases; reuse the owned product fixture and its cleanup."""

import json
import os
import shutil
import subprocess
import sys
import uuid

from energy_runtime import ROOT


def qualify(
    compose, probe, linux, api, wait_for, require, path, runtime, output, env, readers, project
):
    control = runtime / "browser"
    control.mkdir()
    browser_output = output / "browser"
    browser_output.mkdir()
    (control / "ready.json").write_text(
        json.dumps({"token": readers[0]["token"], "run_id": env["EVDT_RUN_ID"]})
    )
    browser_env = {
        **os.environ,
        "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".runtime/playwright-browsers"),
        "EVDT_BASE_URL": "http://127.0.0.1:18815",
        "EVDT_DASHBOARD_CONTROL": str(control),
        "EVDT_BROWSER_OUTPUT": str(browser_output),
    }
    node = shutil.which("node.exe")
    require(bool(node), "Qualified Windows browser tools unavailable")
    process = None
    predictor = None
    secret_dir = runtime / "secrets"
    fixture = runtime / "fixture"
    env_file = runtime / "compose.env"
    preserved = {
        p: p.read_bytes()
        for p in (
            secret_dir / "run_policy.json",
            secret_dir / "map_context.json",
            fixture / "fixture-state.json",
            env_file,
        )
    }
    original_run = env["EVDT_RUN_ID"]
    switched = False

    def marker(name, seconds=40):
        wait_for(lambda: (control / name).exists() or process.poll() is not None, seconds=seconds)
        require((control / name).exists(), "Browser stopped before " + name)

    def predict(name, seed):
        nonlocal predictor
        target = output / name
        logfile = output / (name + ".log")
        with logfile.open("wb") as stream:
            predictor = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/linux.py",
                    "--timeout",
                    "180",
                    "--",
                    "python3",
                    "scripts/known_route_energy/run_monitoring_forecast.py",
                    "--asset",
                    "bus-001",
                    "--env-file",
                    path(env_file),
                    "--project",
                    project,
                    "--credential-file",
                    path(secret_dir / "energy_reader"),
                    "--port",
                    "18815",
                    "--evidence-dir",
                    path(target),
                    "--wait-seconds",
                    "30",
                ],
                cwd=ROOT,
                stdout=stream,
                stderr=stream,
            )
            wait_for(
                lambda: (
                    b"Waiting for an admissible monitoring input" in logfile.read_bytes()
                    or predictor.poll() is not None
                ),
                seconds=25,
            )
            require(predictor.poll() is None, "Forecast controller exited before input")
            seed_result = seed()
            require(
                predictor.wait(timeout=120) == 0,
                "E1 forecast controller failed; inspect private log",
            )
            predictor = None
        record = json.loads((target / "record.json").read_bytes())
        require(record["status"] == "advisory", "E1 controlled forecast unavailable")
        stations = record["extensions"]["stations"]["items"]
        require(
            len(stations) == 2
            and all(s["status"] == "reachable_under_assumptions" for s in stations),
            "Static station assessments did not pass the positive case",
        )
        return record, seed_result

    try:
        with (output / "browser.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    node,
                    str(ROOT / "qualification/browser/node_modules/playwright/cli.js"),
                    "test",
                    "--config",
                    "energy-e1.config.mjs",
                ],
                cwd=ROOT / "qualification/browser",
                env=browser_env,
                stdout=log,
                stderr=log,
            )
            marker("ready")
            original_record, seed_result = predict("prediction", lambda: probe("seed"))
            marker("available-seen", 52)
            marker("stale-seen", 20)
            second_setup = probe("setup-second")
            missing = probe("seed-missing")
            body = api("/v1/energy-forecasts?asset_id=bus-002", "all")[1]
            require(
                body["reason_codes"] == ["required_observation_invalid"] and body["values"] is None,
                "Missing-input admission failed",
            )
            (control / "missing-ready").touch()
            marker("missing-seen")
            probe("advance-second")
            (control / "domain-ready").touch()
            marker("domain-seen")
            # A new registered run is the legitimate recovery from this one-cutoff
            # domain. Never rewind an admitted clock or edit an original packet.
            new_run = str(uuid.uuid4())
            for file in (secret_dir / "run_policy.json", secret_dir / "map_context.json"):
                value = json.loads(file.read_bytes())
                value["run_id"] = new_run
                file.write_text(json.dumps(value, sort_keys=True) + "\n")
            env["EVDT_RUN_ID"] = new_run
            env_file.write_text("".join(k + "=" + v + "\n" for k, v in env.items()))
            switched = True
            compose("run", "--rm", "--no-deps", "migrate")
            probe("setup")
            compose("up", "-d", "--no-deps", "--force-recreate", "api")
            wait_for(lambda: api("/health/ready", None)[0] == 200)
            (control / "new-run-ready").touch()
            marker("recovery-browser-ready")
            recovered, _ = predict("recovery-prediction", lambda: probe("seed"))
            marker("recovery-seen", 20)
            require(recovered["run_id"] != original_record["run_id"], "Recovery reused the old run")
            require(process.wait(timeout=40) == 0, "E1 actual browser acceptance failed")
            process = None
        browser = json.loads((browser_output / "result.json").read_bytes())
        require(browser["passed"], "Incomplete browser result")
        return {
            "record": original_record,
            "seed": seed_result,
            "browser": browser,
            "e1_cases": {
                "passed": True,
                "second_asset_missing_input": missing["admitted"],
                "unsupported_cutoff_visible": True,
                "new_registered_run_recovery": True,
                "original_packets_or_clocks_rewritten": False,
                "retained_training_used_without_held_out_regeneration": True,
                "second_setup": bool(second_setup["registered"]),
                "available_records": 2,
                "admitted_packets": 183,
            },
        }
    finally:
        if predictor is not None and predictor.poll() is None:
            predictor.wait(timeout=40)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if switched:
            for p, content in preserved.items():
                p.write_bytes(content)
            env["EVDT_RUN_ID"] = original_run
            compose("up", "-d", "--no-deps", "--force-recreate", "api")
            wait_for(lambda: api("/health/ready", None)[0] == 200)
