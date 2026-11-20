import copy
import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

from evdt.workload.pacing import await_start, release, validate_release


@pytest.mark.parametrize(
    "change",
    [
        {"version": "future"},
        {"run_id": "other"},
        {"boot_id": "other"},
        {"assets": ["bus-002"]},
        {"assets": ["bus-001", "bus-001"]},
        {"start_monotonic_s": 99},
        {"start_monotonic_s": 131},
        {"start_monotonic_s": float("nan")},
        {"start_monotonic_s": True},
    ],
)
def test_pacing_release_rejects_unbound_or_invalid_clocks(change):
    row = {
        "version": "source-start/1.0.0",
        "run_id": "run",
        "boot_id": "boot",
        "assets": ["bus-001"],
        "start_monotonic_s": 105,
        **change,
    }
    with pytest.raises(ValueError):
        validate_release(row, run_id="run", asset_id="bus-001", boot="boot", now=100)


def test_actual_two_source_barrier_has_one_future_epoch(tmp_path):
    results, errors = {}, []

    def worker(asset):
        try:
            results[asset] = await_start(tmp_path, {"run_id": "run", "asset_id": asset})
        except Exception as exc:
            errors.append(exc)

    assets = ["bus-001", "bus-002"]
    threads = [threading.Thread(target=worker, args=(asset,)) for asset in assets]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 3
    while not all((tmp_path / ("ready-" + asset + ".json")).exists() for asset in assets):
        assert time.monotonic() < deadline, "Sources did not reach the barrier"
        time.sleep(0.01)
    assert not results
    record = release(tmp_path, "run", assets)
    for thread in threads:
        thread.join(timeout=7)
        assert not thread.is_alive()
    assert not errors
    assert results == dict.fromkeys(assets, record["start_monotonic_s"])
    assert time.monotonic() >= record["start_monotonic_s"]
    with pytest.raises(ValueError, match="already exists"):
        release(tmp_path, "run", assets)


def load_script(monkeypatch, name):
    folder = Path(__file__).resolve().parents[2] / "scripts/monitoring"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location(name, folder / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_profiles_preserve_frozen_duration_and_rehearsal_is_distinct(monkeypatch):
    module = load_script(monkeypatch, "workload_profile")
    for name, duration, count in (("W0", 120, 1), ("W1", 300, 10), ("rehearsal", 30, 10)):
        profile = module.select(name, "windows-edge-visible")
        assert profile["warmup_samples"] == 60
        assert profile["total_samples"] == duration + 60
        assert profile["measured_samples"] == duration
        assert len(profile["assets"]) == count
        assert profile["full_duration"] == (name != "rehearsal")
        assert profile["sustained_load_qualified"] is False
        assert profile["qualification_scope"] == "local short-run monitoring acceptance"
        assert profile["host_capacity_qualified"] is False
        assert profile["host_memory_policy"] == "phase1-local-host/1.0.1"
        assert profile["workload_spec_version"] == "1.3.1"
        assert profile["phase_1_policy_version"] == "phase1-local-monitoring/1.3.1"
        assert profile["browser_trace_policy"] == "action-har/1.0.0"
        assert profile["resource_collector_policy"] == "frozen-cgroup/1.0.0"
        assert profile["independent_contexts"] is False
        assert profile["browser_storage_contexts"] == 1
        assert profile["browser_viewer_policy"] == "fleet-operator-table/1.0.0"
        assert profile["browser_pages"] == 1
    with pytest.raises(ValueError):
        module.select("W1-fast", "windows-edge-visible")


@pytest.mark.parametrize("name", ["W0", "W1", "rehearsal"])
@pytest.mark.parametrize(
    "fault", ["old_version", "old_hour", "old_half_hour", "shortened", "changed_warmup", "rate"]
)
def test_runner_refuses_unapproved_duration_or_other_quality_changes(
    monkeypatch, tmp_path, name, fault
):
    module = load_script(monkeypatch, "workload_profile")
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "benchmarks/workloads.json").read_bytes())
    workload = "W1" if name == "rehearsal" else name
    if fault == "old_version":
        spec["profile_version"] = "1.0.0"
    elif fault in ("old_hour", "old_half_hour", "shortened"):
        duration = {"old_hour": 3600, "old_half_hour": 1800}.get(
            fault, spec["workloads"][workload]["duration_s"] - 1
        )
        spec["workloads"][workload]["duration_s"] = duration
        spec["workloads"][workload]["segments"][0]["duration_s"] = duration
    elif fault == "changed_warmup":
        spec["metric_policy"]["window_warmup_s"] = 30
    else:
        spec["workloads"][workload]["segments"][0]["messages_per_asset_s"] = 2
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks/workloads.json").write_text(json.dumps(spec))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        module.select(name, "windows-edge-visible")


def test_full_report_refuses_a_legacy_hour_profile_before_reading_evidence(monkeypatch, tmp_path):
    module = load_script(monkeypatch, "full_measurement_report")
    profile = module.select("W1", "windows-edge-visible")
    profile.update(measured_samples=3600, total_samples=3660)
    with pytest.raises(ValueError, match="duration policy"):
        module.build(tmp_path, profile)


def test_source_window_requires_actual_elapsed_time_and_every_tick(monkeypatch):
    module = load_script(monkeypatch, "full_measurement_report")
    profile = {"total_samples": 62, "warmup_samples": 60, "measured_samples": 2}
    source = {
        "passed": True,
        "paced": True,
        "events": [{"body": {"stream_seq": i}} for i in range(62)],
        "plant_summary": {
            "pacing": {
                "synchronized_start": True,
                "start_monotonic_s": 100,
                "completion_monotonic_s": 162.1,
                "step_lag_max_s": 0.02,
            }
        },
    }
    rows = [
        {"stage": "sample_ready", "stream_seq": i, "monotonic_s": 101.05 + i} for i in range(62)
    ]
    result = module.source_window(source, rows, {"start_monotonic_s": 100}, profile)
    assert result["actual_source_elapsed_s"] >= 62
    assert result["sample_scheduling_lag_s"]["count"] == 62
    for fault in ("shortened", "accelerated", "epoch", "missing", "catchup"):
        current, records = copy.deepcopy(source), copy.deepcopy(rows)
        if fault == "shortened":
            current["plant_summary"]["pacing"]["completion_monotonic_s"] = 161
        elif fault == "accelerated":
            current["paced"] = False
        elif fault == "epoch":
            current["plant_summary"]["pacing"]["start_monotonic_s"] = 99
        elif fault == "missing":
            records.pop()
        else:
            records[5]["monotonic_s"] += 1
        with pytest.raises(ValueError):
            module.source_window(current, records, {"start_monotonic_s": 100}, profile)


def test_host_low_bound_includes_uncertain_sampling_boundaries(monkeypatch):
    module = load_script(monkeypatch, "full_measurement_report")
    rows = [
        {"monotonic_s": i, "physical_available_bytes": (3 if i in (0, 31) else 1) * 1024**3}
        for i in range(32)
    ]
    assert module.host_low_upper(rows) == 31
    assert module.host_low_upper(rows[:20]) == 20.5
    assert module.host_low_upper([rows[0], rows[-1]]) == 0


def test_pacing_release_refuses_stale_readiness(tmp_path):
    from evdt.workload.pacing import boot_id

    (tmp_path / "ready-bus-001.json").write_text(
        json.dumps(
            {
                "version": "source-ready/1.0.0",
                "run_id": "other",
                "asset_id": "bus-001",
                "boot_id": boot_id(),
                "ready_monotonic_s": time.monotonic(),
            }
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        release(tmp_path, "run", ["bus-001"])
    assert not (tmp_path / "start.json").exists()
