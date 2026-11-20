"""Run the local monitoring and replay demonstration through ordinary services."""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from live_speed_evidence import SAMPLES
from qualify_connected_speed_limit import ASSET, read, request, require, wait_ready, write
from qualify_live_restart import wait_for
from qualify_live_speed_command import prepare_live

DEMO_BROWSER = "demo-browser"
DASHBOARD_REVIEW = "dashboard-review"
DEMO_CASES = ("demo-check", "demo-view", DEMO_BROWSER, DASHBOARD_REVIEW)
SERVICES = ("registry", "api", "ingest", "sender", "replay")


def review_profile():
    policy = read(Path(__file__).resolve().parents[2] / "qualification/dashboard/review.json")
    require(
        policy["version"] == "connected-dashboard-review/1.0.0"
        and policy["buses"] == 1
        and policy["signals"] == 20
        and policy["sample_period_s"] == 1
        and policy["samples"] == 300
        and policy["pause_at_s"] == 6
        and policy["pause_seconds"] == 28
        and policy["recording_review_seconds"] == 120
        and policy["worker_timeout_seconds"] == 750
        and policy["live_capture_rounds"] == 3
        and policy["automatic_dispatch"] is False,
        "Dashboard review profile changed; review the case before running",
    )
    return policy


def setup(compose, shell, runtime, override, env, tokens, run, case, dashboard_image):
    prepare_live(compose, shell, runtime, override, env, tokens, run, False)
    directory = runtime / "secrets"
    compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--volume",
        f"{directory}:/policy",
        "--entrypoint",
        "python",
        "source",
        "-m",
        "evdt.monitoring.map_context",
        "--run-id",
        run,
        "--output",
        "/policy/map-ready.json",
    )
    (directory / "map-ready.json").replace(directory / "map_context.json")
    config = read(override)
    services = config["services"]
    services["api"]["image"] = dashboard_image
    services["replay"] = {**services["api"]}
    if case in (DEMO_BROWSER, DASHBOARD_REVIEW):
        services["source"]["command"].extend(["--pause-at-s", "6", "--pause-seconds", "28"])
        if case == DASHBOARD_REVIEW:
            command = services["source"]["command"]
            command[command.index("--maximum-samples") + 1] = str(review_profile()["samples"])
    elif case == "demo-check":
        services["source"]["command"].append("--accelerated")
        policy_file = directory / "run_policy.json"
        policy = read(policy_file)
        for source in policy["sources"]:
            source["expected"]["paced"] = False
        write(policy_file, policy)
    write(override, config)


def dashboard_assets(base):
    with urlopen(base + "/", timeout=5) as response:
        html = response.read(50_000).decode("utf-8")
    scripts = re.findall(r'<script[^>]+src="(/assets/[^"]+)"', html)
    require(bool(scripts), "The page did not serve a built dashboard")
    for path in scripts:
        with urlopen(base + path, timeout=5) as response:
            require(
                "javascript" in response.headers.get_content_type() and bool(response.read(1)),
                "The dashboard module did not load",
            )


def observation_path(count):
    return "/v1/observations?" + urlencode({"asset_id": ASSET, "limit": count + 1})


def check_views(api, replay, tokens, run, originals, expected_samples=SAMPLES):
    query = urlencode({"asset_id": ASSET})
    reader, playback = tokens["reader"], tokens["replay"]

    def get(base, path, token):
        status, response = request(base, path, token)
        require(status == 200, f"Expected a readable {path} response, received {status}")
        return response

    snapshot = get(api, f"/v1/snapshot?{query}", reader)
    signals = get(api, f"/v1/catalog?{query}", reader)["signals"]
    history = get(api, observation_path(expected_samples), reader)["items"]
    final = originals[-1]
    require(
        snapshot["run_id"] == run
        and len(signals) == len(snapshot["items"]) == 20
        and {item["signal_id"]: item["observation"]["sample"] for item in snapshot["items"]}
        == {sample["signal_id"]: sample for sample in final["samples"]}
        and [item["payload"] for item in history] == originals
        and len({packet["event_id"] for packet in originals}) == expected_samples,
        "Current readings or history do not match the source",
    )
    context = get(api, f"/v1/map-context?{query}", reader)
    require(
        context["run_id"] == run and context["asset_id"] == ASSET and bool(context["features"]),
        "The scoped map context is missing",
    )
    for signal in signals:
        params = urlencode(
            {
                "asset_id": ASSET,
                "signal_id": signal,
                "as_of": snapshot["cursor"],
                "limit": expected_samples + 1,
            }
        )
        rows = get(api, f"/v1/history?{params}", reader)["items"]
        require(
            [row["sample"] for row in rows]
            == [
                next(sample for sample in body["samples"] if sample["signal_id"] == signal)
                for body in originals
            ],
            "A signal history does not match the original source",
        )
    selection = {"asset_id": ASSET, "source_run_id": run, "from_s": 0, "to_s": expected_samples}
    endpoint = "/v1/replay/sessions"
    status, package = request(replay, endpoint, playback, json.dumps(selection).encode())
    require(
        status == 200
        and package["source_run_id"] == run
        and package["replay_run_id"] != run
        and len(package["packets"]) == expected_samples
        and not package["capabilities"]["dispatch"],
        "Replay failed to return an isolated recording",
    )
    wrong_scope = {**selection, "source_run_id": str(uuid4())}
    require(
        request(api, f"/v1/snapshot?{query}", playback)[0] == 401
        and request(replay, endpoint, reader, json.dumps(selection).encode())[0] == 401
        and request(replay, endpoint, playback, json.dumps(wrong_scope).encode())[0] == 403
        and request(replay, "/v1/commands", playback, b"{}")[0] == 404,
        "Replay authentication or dispatch isolation failed",
    )
    return {
        "signals_checked": len(signals),
        "accepted_originals": len(history),
        "history_signals_checked": len(signals),
        "map_features": len(context["features"]),
        "replay_packets": len(package["packets"]),
        "replay_isolated": True,
    }


def run_demo(case, compose, shell, runtime, override, env, tokens, run, output, dashboard_image):
    interactive = case != "demo-check"
    review = review_profile() if case == DASHBOARD_REVIEW else None
    sample_count = review["samples"] if review else SAMPLES
    env.update(API_PORT="18765", REPLAY_PORT="18766")
    setup(compose, shell, runtime, override, env, tokens, run, case, dashboard_image)
    started = time.monotonic()
    compose("up", "-d", "--wait", "--wait-timeout", "45", "--pull", "never", *SERVICES)
    api, replay = "http://127.0.0.1:18765", "http://127.0.0.1:18766"
    wait_ready(api)
    wait_ready(replay)
    startup_s = time.monotonic() - started
    require(startup_s <= 120, "Demo startup exceeded the accepted limit")
    dashboard_assets(api)
    dashboard_assets(replay)
    if interactive:
        write(
            output / "ready.json",
            {
                "monitoring_url": api,
                "replay_url": replay,
                "monitoring_token": tokens["reader"],
                "replay_token": tokens["replay"],
                "review": review,
            },
        )
        wait_for(lambda: (output / "start").is_file(), "Demo start signal timed out", timeout=45)
    compose("up", "-d", "--no-deps", "--pull", "never", "source")
    source_id = compose("ps", "-aq", "source").decode().strip()
    require(bool(source_id) and "\n" not in source_id, "Expected one owned demo source")
    if case in (DEMO_BROWSER, DASHBOARD_REVIEW):
        wait_for(
            lambda: (output / "disconnect-source").is_file(), "Browser missed pause", timeout=30
        )
        shell("docker", "pause", source_id, timeout=10)
        try:
            wait_for(
                lambda: (output / "reconnect-source").is_file(),
                "Browser missed source outage",
                timeout=12,
            )
        finally:
            shell("docker", "unpause", source_id, timeout=10)
    require(
        shell("docker", "wait", source_id, timeout=360 if review else 55).strip() == b"0",
        "Demo source failed",
    )
    original_file = output / "source-report.json"
    compose("cp", "source:/source-output/source-report.json", str(original_file))
    report = read(original_file)
    originals = [row["body"] for row in report["events"]]
    require(report["passed"] and len(originals) == sample_count, "Unexpected demo source size")

    def ready():
        status, history = request(api, observation_path(sample_count), tokens["reader"])
        return status == 200 and len(history["items"]) == sample_count

    wait_for(ready, "The demo history did not receive the original messages")
    sender = compose("ps", "-q", "sender").decode().strip()
    require(bool(sender) and "\n" not in sender, "Expected one owned demo sender")
    queue_probe = (
        "import json; from evdt.monitoring.transport import open_spool; "
        "print(json.dumps(open_spool().counts()))"
    )

    def drained():
        counts = json.loads(shell("docker", "exec", sender, "python", "-c", queue_probe))
        return counts == {"pending": 0, "accepted": sample_count}

    wait_for(drained, "The demo queue still contains undelivered originals")
    if not review:
        compose("stop", "--timeout", "1", "sender", "ingest", "broker", "registry")
    result = check_views(api, replay, tokens, run, originals, sample_count)
    if case in (DEMO_BROWSER, DASHBOARD_REVIEW):
        (output / "demo-recorded").touch()
        browser = wait_for(
            lambda: (
                (output / "browser-result.json").is_file() and read(output / "browser-result.json")
            ),
            "The automated browser did not complete the recorded drive checks",
            timeout=110 if review else 50,
        )
        require(browser["passed"], "The automated browser failed")
        result["browser"] = browser
        if review:
            (output / "review-recording").touch()
            deadline = time.monotonic() + review["recording_review_seconds"]
            while not (output / "finish").is_file() and time.monotonic() < deadline:
                time.sleep(0.25)
    elif interactive:
        deadline = time.monotonic() + 60
        while not (output / "finish").is_file() and time.monotonic() < deadline:
            time.sleep(0.25)
    return {
        **result,
        "startup_s": startup_s,
        "pending_at_end": 0,
        "built_dashboard_assets_served": True,
    }
