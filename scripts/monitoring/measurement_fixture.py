"""Short paced collectors rehearsal; no full workload/performance gate claim."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

from workload_source_fixture import ASSETS, require, wait

ROOT = Path(__file__).resolve().parents[2]


def qualify(
    compose,
    execute,
    prefix,
    runtime,
    output,
    env,
    record,
    control,
    definitions,
    override,
    readers,
    *,
    profile=None,
):
    result = {
        "passed": False,
        "W0_passed": False,
        "W1_passed": False,
        "scope": "E2-B short actual collectors rehearsal: two concurrent sources per browser",
    }
    record["measurement"] = result
    total = profile["total_samples"] if profile else 90
    groups = (
        {profile["browser"]: profile["assets"]}
        if profile
        else {"windows-edge-visible": ASSETS[:2], "chromium-automated": ASSETS[2:4]}
    )
    active_assets = [asset for assets in groups.values() for asset in assets]
    phase6 = bool(profile and "phase_6_policy_version" in profile)
    if profile:
        checkpoint = (
            "P6-B1" if phase6 and profile["full_duration"] else "P6-A2" if phase6 else "E2-C"
        )
        result.update(
            profile=profile,
            scope=f"{checkpoint} {profile['workload']} wall-time attempt",
        )
    asset_urls = (
        {asset: f"http://127.0.0.1:{env['API_PORT']}" for asset in active_assets} if profile else {}
    )
    if profile:
        result["asset_urls"] = asset_urls
    definitions["volumes"]["measurements"] = {"labels": {"com.evdt.scope": "e2b-private"}}
    for i, asset in enumerate(ASSETS, 1):
        source = "source" if i == 1 else "source" + str(i)
        sender = "sender" if i == 1 else "sender" + str(i)
        definitions["services"][source]["command"][-1] = str(total)
        if profile:
            definitions["services"][source]["command"].extend(
                ["--synchronized-start-dir", "/measurements"]
            )
        for service, role in ((source, "source"), (sender, "sender")):
            definitions["services"][service]["environment"]["EVDT_TRACE_FILE"] = (
                f"/measurements/{role}-{asset}.jsonl"
            )
            definitions["services"][service]["volumes"].append("measurements:/measurements")
    definitions["services"]["ingest"] = {
        "environment": {"EVDT_TRACE_FILE": "/measurements/ingest.jsonl"},
        "volumes": ["measurements:/measurements"],
    }
    definitions["services"]["probe"]["volumes"].append("measurements:/measurements")
    override.write_text(json.dumps(definitions, indent=2) + "\n")
    compose("config", "--quiet")
    if phase6:
        result["effective_compose_sha256"] = hashlib.sha256(
            compose("config").encode("utf-8")
        ).hexdigest()
    compose("up", "-d", "db", "broker", "migrate", "registry", "ingest", "api", timeout=120)

    def ready():
        with urlopen(f"http://127.0.0.1:{env['API_PORT']}/health/ready", timeout=3) as response:
            return json.load(response)["ready"]

    wait(ready)
    compose(
        "run", "--rm", "--no-deps", "probe", "python", "-m", "evdt.workload.fleet_probe", "contexts"
    )
    project = prefix[prefix.index("-p") + 1]
    stop = runtime / "stop-linux-resources"
    monitor = None
    with (output / "linux-resources.log").open("w") as log:

        def start_monitor(expected=None):
            ready_file = runtime / "linux-resources-ready.json"
            child = subprocess.Popen(
                [
                    sys.executable,
                    "scripts/monitoring/linux_resources.py",
                    "--project",
                    project,
                    "--output",
                    str(output / "linux-resources.jsonl"),
                    "--stop",
                    str(stop),
                    *(["--phase6-monitoring"] if phase6 else []),
                    *(
                        ["--ready", str(ready_file), "--frozen-services", *expected]
                        if expected
                        else []
                    ),
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
            )
            if expected:
                try:
                    wait(lambda: ready_file.exists() or child.poll() is not None)
                    require(
                        child.poll() is None and ready_file.exists(),
                        "Frozen resource initialization failed",
                    )
                    require(
                        json.loads(ready_file.read_bytes())
                        == {
                            "policy": profile["resource_collector_policy"],
                            "project": project,
                        },
                        "Frozen resource readiness differs",
                    )
                except BaseException:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=8)
                    raise
            return child

        try:
            if not profile:
                monitor = start_monitor()
            (control / "ready.tmp").write_text(
                json.dumps(
                    {
                        "base_url": f"http://127.0.0.1:{env['API_PORT']}",
                        "reader_token": readers["readers"][0]["token"],
                        "run_id": env["EVDT_RUN_ID"],
                        "warmup_samples": 60,
                        "measured_samples": total - 60,
                        "total_samples": total,
                        "pairs": groups,
                        "profile": profile,
                        "trace_policy": "action-har/1.0.0",
                        "asset_urls": asset_urls,
                    }
                )
            )
            (control / "ready.tmp").replace(control / "ready.json")
            for browser, assets in groups.items():
                indices = [int(asset.split("-")[1]) for asset in assets]
                print("Waiting for actual browser collectors: " + browser, flush=True)
                wait(
                    lambda: (
                        (control / (browser + "-start")).exists()
                        or (control / "browser-done").exists()
                    ),
                    seconds=600 if profile else 200,
                )
                require(
                    not (control / "browser-done").exists(),
                    "Browser stopped before source rehearsal",
                )
                sources = ["source" if i == 1 else "source" + str(i) for i in indices]
                senders = ["sender" if i == 1 else "sender" + str(i) for i in indices]
                compose("up", "-d", *senders, *sources, timeout=120)
                if profile:
                    monitor = start_monitor(
                        ["db", "broker", "registry", "ingest", "api", *sources, *senders]
                    )
                    result["start_release"] = json.loads(
                        compose(
                            "run",
                            "--rm",
                            "--no-deps",
                            "probe",
                            "python",
                            "-m",
                            "evdt.workload.pacing",
                            "--directory",
                            "/measurements",
                            "--run-id",
                            env["EVDT_RUN_ID"],
                            "--assets",
                            *assets,
                            timeout=150,
                        )
                    )
                    if phase6 and profile["full_duration"]:
                        print(
                            f"Released {len(assets)} bus(es): 60 seconds of warmup "
                            "and 30 measured minutes",
                            flush=True,
                        )
                for source in sources:
                    require(
                        execute(
                            ["docker", "wait", compose("ps", "-aq", source)], timeout=total + 60
                        )
                        == "0",
                        "Paced source failed",
                    )
                originals = {}
                for i, source in zip(indices, sources):
                    asset = ASSETS[i - 1]
                    compose("cp", source + ":/source-output/.", str(output / asset))
                    if not profile:
                        originals[asset] = json.loads(
                            (output / asset / "source-report.json").read_bytes()
                        )["events"]
                    else:
                        # Each asset is read separately by Node; do not duplicate
                        # the ten full original packet arrays in a control file.
                        originals[asset] = str(output / asset / "source-report.json").replace(
                            "/mnt/c/", "C:/"
                        )
                original_file = control / (browser + "-originals.json")
                original_file.with_suffix(".tmp").write_text(json.dumps(originals))
                original_file.with_suffix(".tmp").replace(original_file)
                wait(
                    lambda: (
                        (control / (browser + "-measurement-result.json")).exists()
                        or (control / "browser-done").exists()
                    ),
                    seconds=180 if profile else 45,
                )
                require(
                    (control / (browser + "-measurement-result.json")).is_file(),
                    "Browser measurement missing",
                )
                require(
                    json.loads((control / (browser + "-measurement-result.json")).read_bytes())[
                        "passed"
                    ],
                    "Browser measurement failed",
                )
                if not profile:
                    compose("stop", *senders)
                require(monitor.poll() is None, "Linux resource collector failed")
            result["fleet"] = json.loads(
                compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "probe",
                    "python",
                    "-m",
                    "evdt.workload.fleet_probe",
                    "verify",
                    "--count",
                    str(total),
                    "--assets",
                    *active_assets,
                )
            )
            if phase6 and profile["full_duration"]:
                result["fleet"]["scope"] = (
                    "P6-B1 source, transport, storage and scoped read reconciliation"
                )
            wait(lambda: (control / "browser-done").exists(), seconds=45)
            require(
                json.loads((control / "browser-result.json").read_bytes())["passed"],
                "Browser controller failed",
            )
            result["passed"] = True
        finally:
            # Preserve partial records on failures as well as successful rehearsals.
            if profile:
                for asset in active_assets:
                    index = int(asset.split("-")[1])
                    source = "source" if index == 1 else "source" + str(index)
                    try:
                        compose(
                            "cp", source + ":/source-output/.", str(output / asset), check=False
                        )
                    except Exception as exc:
                        result.setdefault("partial_source_copy_errors", {})[asset] = str(exc)
            try:
                helper = compose(
                    "run",
                    "--no-deps",
                    "-d",
                    "probe",
                    "python",
                    "-c",
                    "import time; time.sleep(120)",
                )
                try:
                    execute(["docker", "cp", helper + ":/measurements/.", str(output / "traces")])
                finally:
                    execute(["docker", "rm", "-f", helper])
            except Exception as exc:
                result["trace_copy_error"] = str(exc)
                result["passed"] = False
            stop.touch()
            if monitor is not None:
                try:
                    monitor.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    monitor.terminate()
                    monitor.wait(timeout=5)
                    result["passed"] = False
                result["linux_collector_exit_code"] = monitor.returncode
                if monitor.returncode:
                    result["passed"] = False
    return result
