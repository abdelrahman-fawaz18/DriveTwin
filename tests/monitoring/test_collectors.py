import copy
import json

import pytest

from evdt.monitoring import trace
from evdt.workload.correlation import clock_bounds, correlate, load_trace, resource_summary


def test_trace_off_and_bounded_enabled_records(tmp_path, monkeypatch):
    monkeypatch.delenv("EVDT_TRACE_FILE", raising=False)
    monkeypatch.setattr(trace, "_sequence", 0)
    trace.emit("ignored")
    assert not list(tmp_path.iterdir())
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("EVDT_TRACE_FILE", str(path))
    trace.emit("sample_ready", {k: k for k in ("run_id", "asset_id", "source_id", "event_id")})
    header, records = load_trace(path)
    assert header["boot_id"] and len(records) == 1
    assert "payload" not in path.read_text() and "token" not in path.read_text()
    monkeypatch.setattr(trace, "LIMIT", path.stat().st_size)
    with pytest.raises(RuntimeError, match="budget"):
        trace.emit("over_budget")


@pytest.mark.parametrize(
    "fault", ["truncated", "missing", "duplicate", "reordered", "clock_step", "nan"]
)
def test_trace_corruption_is_not_silently_discarded(tmp_path, fault):
    path = tmp_path / "trace.jsonl"
    rows = [
        {
            "stage": "process_start" if i == 0 else "sample_ready",
            "version": "event-trace/1.1.0",
            "sequence": i,
            "process": "p",
            "boot_id": "b",
            "utc_s": 100 + i,
            "monotonic_s": 10 + i,
        }
        for i in range(3)
    ]
    if fault == "missing":
        rows.pop(1)
    if fault == "duplicate":
        rows.insert(2, copy.deepcopy(rows[1]))
    if fault == "reordered":
        rows[1:] = reversed(rows[1:])
    if fault == "clock_step":
        rows[-1]["utc_s"] += 1
    if fault == "nan":
        rows[-1]["utc_s"] = float("nan")
    raw = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text(raw[:-1] if fault == "truncated" else raw)
    with pytest.raises(ValueError):
        load_trace(path)


def exchanges():
    return [
        {
            "client_send_s": 100 + i * 5,
            "client_receive_s": 100 + i * 5 + 0.01,
            "client_send_monotonic_s": 10 + i * 5,
            "client_receive_monotonic_s": 10 + i * 5 + 0.01,
            "received_utc_s": 100 + i * 5 + 0.003,
            "sent_utc_s": 100 + i * 5 + 0.004,
            "received_monotonic_s": 20 + i * 5 + 0.003,
            "sent_monotonic_s": 20 + i * 5 + 0.004,
            "wall_residual_s": 0,
            "boot_id": "b",
        }
        for i in range(4)
    ]


def test_clock_bounds_use_actual_before_periodic_and_after_exchange():
    result = clock_bounds(exchanges(), start_s=101, end_s=114)
    assert result.lower_s <= 0 <= result.upper_s and result.uncertainty_s < 0.05


@pytest.mark.parametrize(
    "fault", ["gap", "client_step", "server_step", "boot", "uncertainty", "not_bracketed"]
)
def test_inconclusive_clock_evidence_fails(fault):
    rows = exchanges()
    start, end = 101, 114
    if fault == "gap":
        rows.pop(1)
    if fault == "client_step":
        rows[1]["wall_residual_s"] = 0.1
    if fault == "server_step":
        rows[1]["received_utc_s"] += 0.1
        rows[1]["sent_utc_s"] += 0.1
    if fault == "boot":
        rows[-1]["boot_id"] = "new_boot"
    if fault == "uncertainty":
        for row in rows:
            row["client_receive_s"] += 0.2
            row["client_receive_monotonic_s"] += 0.2
    if fault == "not_bracketed":
        start = 99
    with pytest.raises(ValueError):
        clock_bounds(rows, start_s=start, end_s=end)


def correlated():
    originals, records, dom = [], [], {}
    for i in range(2):
        key = {"run_id": "r", "asset_id": "bus-001", "source_id": "s", "event_id": str(i)}
        body = {**key, "stream_seq": i, "samples": [{"signal_id": "x", "unit": "W", "value": i}]}
        originals.append({**key, "body": body, "wire_sha256": "hash"})
        t = 100 + i
        for j, stage in enumerate(
            (
                "sample_ready",
                "enqueue_committed",
                "publish_attempt",
                "publish_return",
                "broker_ack",
                "receiver_callback",
                "projection_staged",
                "transaction_committed",
                "delivery_complete",
                "receipt_applied",
            )
        ):
            records.append(
                {
                    **key,
                    "stage": stage,
                    "utc_s": t + j * 0.01,
                    "monotonic_s": t + j * 0.01,
                    "attempt_id": "a" + str(i),
                    "wire_sha256": "hash",
                    "rc": 0,
                    "mid": i,
                    "failed": False,
                    "applied": True,
                    "outcome": "accepted",
                }
            )
        dom[str(i)] = {
            "monotonic_s": t + 0.2,
            "witness": {
                "version": "clock-exchange/1.1.0",
                "asset_id": key["asset_id"],
                "event_id": key["event_id"],
                "client_send_monotonic_s": t + 0.201,
                "client_receive_monotonic_s": t + 0.21,
                "received_monotonic_s": t + 0.205,
            },
            **key,
            "utc_s": t + 0.2,
            "wall_residual_s": 0,
            "values": {"x": str(i) + " W"},
            "values_verified": True,
            "wire_sha256": "hash",
        }
    return originals, records, dom


def test_complete_correlated_events_keep_warmup_separate_from_percentiles():
    originals, records, dom = correlated()
    result = correlate(originals, records, dom, warmup=1)
    assert (
        result["unique_generated"] == 2
        and result["warmup_events"] == 1
        and result["measured_events"] == 1
    )
    assert result["publish_attempts"] == 2 and result["duplicate_deliveries"] == 0
    assert result["source_to_dom_upper"]["p95_s"] == pytest.approx(0.205001)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_dom",
        "extra_dom",
        "missing_stage",
        "duplicate_stage",
        "attempt",
        "wrong_bytes",
        "wrong_value_proof",
        "backward",
        "unknown_stage",
    ],
)
def test_incomplete_correlation_fails_without_changing_denominators(fault):
    originals, records, dom = correlated()
    if fault == "missing_dom":
        del dom["1"]
    if fault == "extra_dom":
        dom["extra"] = copy.deepcopy(dom["1"])
    if fault == "missing_stage":
        records.pop(0)
    if fault == "duplicate_stage":
        records.append(copy.deepcopy(records[0]))
    if fault == "attempt":
        records[3]["attempt_id"] = "unknown"
    if fault == "wrong_bytes":
        records[1]["wire_sha256"] = "changed"
    if fault == "wrong_value_proof":
        dom["1"]["values_verified"] = False
    if fault == "backward":
        records[6]["monotonic_s"] -= 2
    if fault == "unknown_stage":
        records[0]["stage"] = "unknown"
    with pytest.raises(ValueError):
        correlate(originals, records, dom, warmup=1)


def resources():
    return [
        {
            "sequence": i,
            "utc_s": 100 + i,
            "monotonic_s": 10 + i,
            "boot_id": "b",
            "page_bytes": 4096,
            "swap_in_pages": i,
            "swap_out_pages": 0,
            "linux_memory": {"MemAvailable": 10000000},
            "containers": [
                {"group": "/a", "memory_bytes": 1000, "memory_events": {"oom": 0, "oom_kill": 0}}
            ],
            "stopped": [],
            "physical_available_bytes": 3 * 1024**3,
            "paging": {"pages_input_s": 0, "pages_output_s": 0},
            "browser_name_total_bytes": 100,
            "wsl_name_total_bytes": 100,
        }
        for i in range(65)
    ]


def test_resource_rollups_have_disjoint_groups_and_counter_deltas():
    rows = resources()
    result = resource_summary(rows, platform="linux")
    assert result["core_peak_bytes"] == 1000 and result["rolling_60s_swap_max_bytes"] == 60 * 4096
    assert resource_summary(rows, platform="windows")["sampled_longest_below_2GiB_s"] == 0


@pytest.mark.parametrize(
    "fault",
    ["sequence", "gap", "overlap", "oom", "stopped_oom", "reset", "clock", "nan", "negative"],
)
def test_invalid_resources_cannot_be_reported_as_healthy(fault):
    rows = resources()
    if fault == "sequence":
        rows[1]["sequence"] = 4
    if fault == "gap":
        rows.pop(1)
        rows[1]["sequence"] = 1
    if fault == "overlap":
        rows[1]["containers"].append({"group": "/a/b", "memory_bytes": 1, "memory_events": {}})
    if fault == "oom":
        rows[1]["containers"][0]["memory_events"]["oom_kill"] = 1
    if fault == "stopped_oom":
        rows[1]["stopped"] = [{"oom_killed": True}]
    if fault == "reset":
        rows[3]["swap_in_pages"] = 0
    if fault == "clock":
        rows[1]["utc_s"] += 1
    if fault == "nan":
        rows[1]["monotonic_s"] = float("nan")
    if fault == "negative":
        rows[1]["containers"][0]["memory_bytes"] = -1
    with pytest.raises(ValueError):
        resource_summary(rows, platform="linux")


def test_sustained_swap_and_host_pressure_remain_visible_in_short_summaries():
    rows = resources()
    for row in rows:
        row["swap_in_pages"] *= 100000
        row["physical_available_bytes"] = 1024**3
    linux = resource_summary(rows, platform="linux")
    assert (
        linux["consecutive_excessive_rolling_windows"] >= 3
        and not linux["short_run_budget_conditions_met"]
    )
    assert resource_summary(rows, platform="windows")["sampled_longest_below_2GiB_s"] == 64


@pytest.mark.parametrize(
    "fault", ["witness_missing", "witness_timeout", "witness_identity", "witness_before_dom"]
)
def test_dom_witness_is_complete_causal_and_bounded(fault):
    originals, records, dom = correlated()
    if fault == "witness_missing":
        dom["1"].pop("witness")
    if fault == "witness_timeout":
        dom["1"]["witness"]["client_receive_monotonic_s"] += 12
    if fault == "witness_identity":
        dom["1"]["witness"]["event_id"] = "different"
    if fault == "witness_before_dom":
        dom["1"]["witness"]["client_send_monotonic_s"] -= 0.1
    with pytest.raises((ValueError, KeyError)):
        correlate(originals, records, dom, warmup=1)


def test_confirmation_overhead_does_not_create_a_favorable_clock_correction():
    originals, records, dom = correlated()
    dom["1"]["witness"]["client_receive_monotonic_s"] += 0.2
    result = correlate(originals, records, dom, warmup=1)
    assert result["witness_confirmation_overhead_bound_max_s"] > 0.2
    assert result["source_to_dom_upper"]["p95_s"] >= 0.205
    assert not result["cross_domain_timestamp_subtraction"]


def test_coarse_local_clock_cannot_pass_measurement():
    originals, records, dom = correlated()
    with pytest.raises(ValueError, match="resolution"):
        correlate(originals, records, dom, warmup=1, clock_resolution_s=0.051)
