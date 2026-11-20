"""Join complete monitoring workload evidence without shortening its clock."""

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from host_memory_policy import assess  # noqa: E402
from linux_resources import validate_final  # noqa: E402
from measurement_report import read_rows  # noqa: E402
from workload_profile import select  # noqa: E402

from evdt.workload.correlation import (  # noqa: E402
    clock_bounds,
    correlate,
    distribution,
    iter_trace,
    require,
    resource_summary,
    validate_clock_exchanges,
)


def host_low_upper(rows, threshold=2 * 1024**3):
    """Conservative sampled bound, including both boundary sampling cells."""
    maximum = 0.0
    started = None
    for i, row in enumerate(rows):
        if row["physical_available_bytes"] < threshold:
            if started is None:
                started = rows[max(0, i - 1)]["monotonic_s"]
        elif started is not None:
            maximum = max(maximum, row["monotonic_s"] - started)
            started = None
    if started is not None:
        maximum = max(maximum, rows[-1]["monotonic_s"] - started + 1.5)
    return maximum


def source_window(source, records, release, profile):
    require(
        source["passed"] and source["paced"] and len(source["events"]) == profile["total_samples"],
        "Full paced source incomplete",
    )
    require(
        [e["body"]["stream_seq"] for e in source["events"]]
        == list(range(profile["total_samples"])),
        "Source sequence does not cover the declared warmup and measurement",
    )
    pacing = source["plant_summary"]["pacing"]
    start = release["start_monotonic_s"]
    require(
        pacing["synchronized_start"]
        and pacing["start_monotonic_s"] == start
        and pacing["completion_monotonic_s"] >= start + profile["total_samples"]
        and pacing["step_lag_max_s"] < 1,
        "Source wall-time was shortened, unsynchronized or caught up from a missed period",
    )
    ready = sorted(
        (r for r in records if r["stage"] == "sample_ready"), key=lambda r: r["stream_seq"]
    )
    require(len(ready) == profile["total_samples"], "Missing source-ready schedule evidence")
    lags = [row["monotonic_s"] - start - row["stream_seq"] - 1 for row in ready]
    require(
        all(0 <= lag < 1 for lag in lags), "A source sample missed its nominal one-second period"
    )
    return {
        "start_monotonic_s": start,
        "planned_warmup_s": profile["warmup_samples"],
        "planned_measurement_s": profile["measured_samples"],
        "actual_source_elapsed_s": pacing["completion_monotonic_s"] - start,
        "scheduled_measurement_start_monotonic_s": start + profile["warmup_samples"],
        "last_sample_monotonic_s": ready[-1]["monotonic_s"],
        "measured_tick_count": profile["measured_samples"],
        "achieved_total_events_per_s": len(ready) / (ready[-1]["monotonic_s"] - start),
        "sample_scheduling_lag_s": distribution(lags),
        "physical_step_lag_max_s": pacing["step_lag_max_s"],
    }


def build(evidence, profile, *, phase6=False):
    if phase6:
        from smoke_policy import monitoring_profile

        expected = monitoring_profile(profile["workload"])
    else:
        expected = select(profile["workload"], profile["browser"])
    require(
        profile == expected,
        "Recorded workload does not match its frozen duration policy",
    )
    services = evidence / "services"
    service_record = json.loads((services / "qualification.json").read_bytes())
    if phase6 and profile["full_duration"]:
        fleet = service_record["sources"]["fleet"]
        require(
            service_record["passed"]
            and service_record["sources"]["profile"] == profile
            and fleet["passed"]
            and fleet["unique_events"] == len(profile["assets"]) * profile["total_samples"]
            and fleet["cross_asset_broker_publish_denied"]
            and set(fleet["assets"]) == set(profile["assets"])
            and all(
                asset["events"] == profile["total_samples"]
                and asset["scalars"] == profile["total_samples"] * profile["signals_per_asset"]
                and asset["pending"] == 0
                for asset in fleet["assets"].values()
            ),
            "Capacity workload storage, scope or queue reconciliation differs",
        )
    release = service_record["sources"]["start_release"]
    assets = profile["assets"]
    require(release["assets"] == assets, "Released fleet differs from workload")
    trace_paths = sorted((services / "traces").glob("*.jsonl"))
    require(len(trace_paths) == 2 * len(assets) + 1, "Missing or extra process trace")
    headers = []
    for path in trace_paths:
        # Exhaustion below validates every record, not only each header.
        rows = iter_trace(path)
        headers.append(next(rows))
        rows.close()
    boots = {header["boot_id"] for header in headers}
    require(boots == {release["boot_id"]}, "Pacing/trace boot mismatch")
    require(len({h["process"] for h in headers}) == len(headers), "Trace process reused")
    resolutions = [h["monotonic_resolution_s"] for h in headers]
    linux_rows = read_rows(services / "linux-resources.jsonl")
    windows_rows = read_rows(evidence / "windows-resources.jsonl")
    require(
        all(r.get("collector_policy") == profile["resource_collector_policy"] for r in linux_rows),
        "Resource collector policy differs",
    )
    terminal = json.loads((services / "linux-resources.final.json").read_bytes())
    require(
        terminal.get("passed") is True
        and terminal.get("policy") == profile["resource_collector_policy"]
        and terminal.get("boot_id") in boots
        and terminal.get("project") == service_record["project"],
        "Final resource identity proof differs",
    )
    validate_final(terminal["baseline"], terminal["final"])
    linux = resource_summary(linux_rows, platform="linux")
    windows = resource_summary(windows_rows, platform="windows")
    windows["below_2GiB_conservative_sampled_upper_s"] = host_low_upper(windows_rows)
    require({row["boot_id"] for row in linux_rows} == boots, "Resource/trace boot mismatch")
    require(
        all(
            {"db", "broker", "registry", "ingest", "api"} <= {g["service"] for g in r["containers"]}
            for r in linux_rows
        ),
        "Application resource coverage incomplete",
    )
    browser = json.loads(
        (evidence / (profile["browser"] + "-measurement-result.json")).read_bytes()
    )
    require(
        browser["passed"] and set(browser["assets"]) == set(assets), "Browser asset set differs"
    )
    require(
        browser.get("trace_policy") == profile["browser_trace_policy"],
        "Browser tracing policy differs",
    )
    require(
        browser.get("viewer_policy") == profile["browser_viewer_policy"]
        and browser.get("per_asset_dom_isolation_passed") is True
        and browser.get("browser_pages") == profile["browser_pages"] == 1
        and browser.get("empty_browser_storage_passed") is True
        and browser.get("asset_urls") == service_record["sources"]["asset_urls"],
        "Single-page fleet scope/storage proof differs",
    )
    expected_archives = [profile["browser"] + "-fleet.har"]
    require(
        browser.get("network_archives") == expected_archives and not browser.get("network_error"),
        "Browser network archives incomplete",
    )
    for name in expected_archives:
        archive = json.loads((evidence / name).read_bytes())
        require(
            archive.get("log", {}).get("version") == "1.2" and bool(archive["log"].get("entries")),
            "Invalid or empty browser network archive",
        )
    counts = defaultdict(int)
    samples = defaultdict(list)
    by_asset = {}
    all_ids = set()
    for asset in assets:
        source = json.loads((services / asset / "source-report.json").read_bytes())
        original = [
            {**event, **{key: event["body"][key] for key in ("run_id", "asset_id", "source_id")}}
            for event in source["events"]
        ]
        require(not all_ids & {e["event_id"] for e in original}, "Fleet reused an event ID")
        all_ids.update(e["event_id"] for e in original)
        records = []
        for name in ("source-" + asset, "sender-" + asset, "ingest"):
            for row in iter_trace(services / "traces" / (name + ".jsonl")):
                if row["stage"] == "process_start":
                    continue
                require(row.get("asset_id") in assets, "Unknown asset in full trace")
                if name != "ingest":
                    require(row["asset_id"] == asset, "Per-asset trace contains another asset")
                if row["asset_id"] == asset:
                    records.append(row)
        require(browser["assets"][asset]["passed"], "Per-asset browser verification failed")
        name = profile["browser"] + "-measurement-asset-" + asset + ".json"
        require(browser["assets"][asset]["file"] == name, "Unexpected browser artifact path")
        data = json.loads((evidence / name).read_bytes())
        events = data["events"]
        require(
            not data["failures"] and len(events) == profile["total_samples"], "DOM coverage differs"
        )
        start, end = (
            min(r["utc_s"] for r in events.values()),
            max(r["utc_s"] for r in events.values()),
        )
        options = dict(start_s=start, end_s=end, resolution_s=data["clock_resolution_s"])
        validate_clock_exchanges(data["clocks"], **options)
        require({c["boot_id"] for c in data["clocks"]} == boots, "Browser clock boot mismatch")
        require(
            all(e["witness"]["boot_id"] in boots for e in events.values()), "Witness boot mismatch"
        )
        resolution = max(
            *resolutions, *(e["witness"]["monotonic_resolution_s"] for e in events.values())
        )
        joined = correlate(
            original,
            records,
            events,
            clock_resolution_s=max(1e-6, 2 * resolution),
            include_samples=True,
        )
        for key in (
            "unique_generated",
            "accepted",
            "quarantined",
            "dropped",
            "pending",
            "measured_events",
            "warmup_events",
            "publish_attempts",
            "receiver_deliveries",
            "duplicate_deliveries",
        ):
            counts[key] += joined[key]
        for key, values in joined.pop("samples").items():
            samples[key].extend(values)
        clocks = {"exchanges": len(data["clocks"])}
        try:
            bound = clock_bounds(data["clocks"], **options)
            clocks.update(constant_offset="bounded", uncertainty_s=bound.uncertainty_s)
        except ValueError as exc:
            clocks.update(constant_offset="inconclusive", reason=str(exc))
        by_asset[asset] = {
            "events": joined,
            "clock_diagnostic": clocks,
            "schedule": source_window(source, records, release, profile),
        }
        ready_stamps = [r["utc_s"] for r in records if r["stage"] == "sample_ready"]
        first, last = min(ready_stamps), max(ready_stamps)
        index = int(asset.split("-")[1])
        names = {
            "source" if index == 1 else "source" + str(index),
            "sender" if index == 1 else "sender" + str(index),
        }
        active_rows = [r for r in linux_rows if first <= r["utc_s"] <= last]
        require(
            active_rows
            and all(names <= {g["service"] for g in r["containers"]} for r in active_rows),
            "Source/sender resource coverage incomplete",
        )
        require(
            linux_rows[0]["utc_s"] <= first and linux_rows[-1]["utc_s"] >= last,
            "Linux coverage incomplete",
        )
        require(
            windows_rows[0]["utc_s"] <= start and windows_rows[-1]["utc_s"] >= end,
            "Windows coverage incomplete",
        )
        active_windows = [r for r in windows_rows if start <= r["utc_s"] <= end]
        require(
            active_windows
            and all(
                r["wsl_name_total_bytes"] is not None and r["wsl_name_total_bytes"] > 0
                for r in active_windows
            ),
            "WSL resource coverage incomplete",
        )
    require(
        counts["measured_events"] == len(assets) * profile["measured_samples"]
        and counts["warmup_events"] == len(assets) * 60,
        "Declared workload event counts differ",
    )
    distributions = {key: distribution(values) for key, values in samples.items()}
    latency = distributions["source_to_dom_upper"]
    latency_met = latency["p95_s"] <= 2 and latency["p99_s"] <= 5
    local_host = assess(windows_rows)
    resources_met = linux["short_run_budget_conditions_met"] and local_host["passed"]
    if phase6:
        resources_met = resources_met and not local_host["capacity_warning"]
    capacity = phase6 and profile["workload"] == "W1-30" and resources_met
    require(linux["rolling_60s_swap_max_bytes"] is not None, "Missing complete swap window")
    result = {
        "passed": latency_met and resources_met,
        "instrumentation_complete": True,
        "profile": profile,
        "events": dict(counts),
        "fleet_distributions": distributions,
        "by_asset": by_asset,
        "linux_resources": linux,
        "windows_resources": windows,
        "local_host": local_host,
        "host_capacity_qualified": capacity,
        "latency_conditions_met": latency_met,
        "resource_conditions_met": resources_met,
        "W0_passed": profile["workload"] == "W0" and latency_met and resources_met,
        "W1_passed": profile["workload"] == "W1" and latency_met and resources_met,
        "Q12_W1_passed": capacity,
        "Q12_local_monitoring_passed": profile["workload"] == "W1" and resources_met,
        "full_phase_1_passed": False,
    }
    if phase6 and profile["full_duration"]:
        result["phase6_release_workload_passed"] = result["passed"]
    (evidence / "measurement.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return result
