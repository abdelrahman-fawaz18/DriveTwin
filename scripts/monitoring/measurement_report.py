"""Join private actual service/browser/resource evidence without inventing timestamps."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.workload.correlation import (  # noqa: E402
    clock_bounds,
    correlate,
    load_trace,
    require,
    resource_summary,
    validate_clock_exchanges,
)


def read_rows(path):
    raw = path.read_bytes()
    require(raw.endswith(b"\n"), "Truncated resource evidence")
    return [json.loads(line) for line in raw.splitlines()]


def build(evidence):
    services = evidence / "services"
    records, boots, processes, resolutions = [], set(), set(), []
    trace_paths = sorted((services / "traces").glob("*.jsonl"))
    require(len(trace_paths) == 9, "Require four source/sender traces and one receiver trace")
    for path in trace_paths:
        header, rows = load_trace(path)
        require(header["process"] not in processes, "Trace process reused")
        processes.add(header["process"])
        boots.add(header["boot_id"])
        resolutions.append(header["monotonic_resolution_s"])
        records.extend(rows)
    originals = []
    for i in range(1, 5):
        source = json.loads((services / f"bus-{i:03}" / "source-report.json").read_bytes())
        require(
            source["passed"] and source["paced"] and len(source["events"]) == 90,
            "Source incomplete",
        )
        originals.extend(
            {**event, **{k: event["body"][k] for k in ("run_id", "asset_id", "source_id")}}
            for event in source["events"]
        )
    # Controllers preserve exact raw browser results beside the aggregate report.
    rendered, clock_summaries = {}, {}
    for project in ("windows-edge-visible", "chromium-automated"):
        result = json.loads((evidence / (project + "-measurement-result.json")).read_bytes())
        require(result["passed"], "Browser collection failed")
        for asset, data in result["assets"].items():
            require(not data["failures"], "Clock collection failed")
            events = data["events"]
            require(
                len(events) == 90 and not set(events) & set(rendered),
                "Browser identity counts differ",
            )
            options = dict(
                start_s=min(r["utc_s"] for r in events.values()),
                end_s=max(r["utc_s"] for r in events.values()),
                resolution_s=data["clock_resolution_s"],
            )
            validate_clock_exchanges(data["clocks"], **options)
            require(
                {c["boot_id"] for c in data["clocks"]} == boots,
                "Source/browser clock domain differs",
            )
            require(
                all(e["witness"]["boot_id"] in boots for e in events.values()),
                "DOM witness clock domain differs",
            )
            resolutions.extend(e["witness"]["monotonic_resolution_s"] for e in events.values())
            rendered.update(events)
            clock_summaries[asset] = {"exchanges": len(data["clocks"])}
            try:
                bound = clock_bounds(data["clocks"], **options)
                clock_summaries[asset].update(
                    constant_offset="bounded",
                    offset_lower_s=bound.lower_s,
                    offset_upper_s=bound.upper_s,
                    uncertainty_s=bound.uncertainty_s,
                )
            except ValueError as exc:
                clock_summaries[asset].update(constant_offset="inconclusive", reason=str(exc))
    require(
        len(boots) == 1 and set(clock_summaries) == {f"bus-{i:03}" for i in range(1, 5)},
        "Missing clock domain/asset",
    )
    joined = correlate(
        originals, records, rendered, clock_resolution_s=max(0.000001, 2 * max(resolutions))
    )
    require(
        joined["unique_generated"] == 360
        and joined["measured_events"] == 120
        and joined["warmup_events"] == 240,
        "Declared rehearsal event counts differ",
    )
    windows_rows = read_rows(evidence / "windows-resources.jsonl")
    linux_rows = read_rows(services / "linux-resources.jsonl")
    linux = resource_summary(linux_rows, platform="linux")
    windows = resource_summary(windows_rows, platform="windows")
    require(
        windows["wsl_name_total_peak_bytes"] is not None
        and windows["wsl_name_total_peak_bytes"] > 0,
        "WSL working-set collection incomplete",
    )
    core_services = {"db", "broker", "registry", "ingest", "api"}
    require(
        all(core_services <= {g["service"] for g in row["containers"]} for row in linux_rows),
        "Core service resource coverage incomplete",
    )
    for i in range(1, 5):
        asset = f"bus-{i:03}"
        stamps = [
            r["utc_s"]
            for r in records
            if r.get("asset_id") == asset and r["stage"] == "sample_ready"
        ]
        names = {
            "source" if i == 1 else "source" + str(i),
            "sender" if i == 1 else "sender" + str(i),
        }
        sampled = [r for r in linux_rows if min(stamps) <= r["utc_s"] <= max(stamps)]
        require(
            sampled and all(names <= {g["service"] for g in r["containers"]} for r in sampled),
            "Source/sender resource coverage incomplete",
        )
    require({r["boot_id"] for r in linux_rows} == boots, "Resource/trace boot differs")
    source_stamps = [r["utc_s"] for r in records if r["stage"] == "sample_ready"]
    first, last = min(source_stamps), max(source_stamps)
    require(
        linux_rows[0]["utc_s"] <= first and linux_rows[-1]["utc_s"] >= last,
        "Linux resources do not cover source run",
    )
    for asset in clock_summaries:
        stamps = [r["utc_s"] for r in rendered.values() if r["asset_id"] == asset]
        require(
            all(
                r["wsl_name_total_bytes"] is not None
                for r in windows_rows
                if min(stamps) <= r["utc_s"] <= max(stamps)
            ),
            "Missing WSL memory during rendering",
        )
        require(
            windows_rows[0]["utc_s"] <= min(stamps) and windows_rows[-1]["utc_s"] >= max(stamps),
            "Windows resources do not cover browser run",
        )
    result = {
        "passed": True,
        "scope": "E2-B collectors; two concurrent assets per browser, not full W0/W1",
        "full_E2_passed": False,
        "W0_passed": False,
        "W1_passed": False,
        "Q12_W1_passed": False,
        "events": joined,
        "clocks": clock_summaries,
        "linux_resources": linux,
        "windows_resources": windows,
    }
    (evidence / "measurement.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return result
