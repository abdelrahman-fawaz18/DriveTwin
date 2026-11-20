import copy
import json

import pytest

from scripts.monitoring import history_query_audit as audit
from scripts.monitoring import qualify_history_queries as queries


def points():
    rows = []
    for index, (t, value, quality, epoch) in enumerate(
        [
            (0, 0.0, "good", 1),
            (1, 10.0, "good", 1),
            (2, None, "missing", 1),
            (3, None, "invalid", 1),
            (4, 5.0, "good", 1),
            (6, 3.0, "good", 1),
            (7, 4.0, "good", 2),
        ]
    ):
        rows.append(
            {
                "signal_id": "speed",
                "event_id": str(index),
                "sim_time_s": t,
                "observed_at": "2026-11-04T00:00:00Z",
                "source_id": "source",
                "source_epoch": epoch,
                "stream_seq": index,
                "sample": {"signal_id": "speed", "value": value, "quality": quality, "unit": "m/s"},
            }
        )
    return {"speed": rows}


def test_independent_expected_reduction_preserves_zero_spike_quality_gaps_and_epoch():
    rows, buckets = audit.expected(points(), ["speed"], 0, 8, 2, 4, {"speed": 1})
    assert [r["sim_time_s"] for r in rows] == [0, 1, 4, 6, 7]
    assert [r["segment_id"] for r in rows] == [
        "source:1:1",
        "source:1:1",
        "source:1:4",
        "source:1:5",
        "source:2:1",
    ]
    first, second = buckets
    assert (
        first["count"],
        first["good"],
        first["missing"],
        first["invalid"],
        first["continuity_breaks"],
    ) == (4, 2, 1, 1, 2)
    assert (first["minimum"], first["maximum"], first["mean"]) == (0.0, 10.0, 5.0)
    assert (second["count"], second["good"], second["continuity_breaks"]) == (3, 3, 2)
    assert second["mean"] == 4.0 and second["first_good_s"] == 4 and second["last_good_s"] == 7
    _, empty = audit.expected(points(), ["speed"], 100, 108, 2, 4, {"speed": 1})
    assert all(b["count"] == 0 and b["mean"] is None for b in empty)


def response(limit=20):
    original = points()
    count = limit // 4
    kept, buckets = audit.expected(original, ["speed"], 0, 8, count, 8 / count, {"speed": 1})
    context = {
        "asset_id": "bus-001",
        "run_id": "run",
        "as_of": "cursor",
        "signals": {"speed": {"unit": "m/s"}},
        "timings": {"speed": {"period_s": 1}},
    }
    metadata = {
        **context,
        "from_s": 0,
        "to_s": 8,
        "plot_version": "history-plot/1.0.0",
        "interpolated": False,
        "resolution": "first/minimum/maximum/last per bucket and continuous segment",
        "point_limit": limit,
        "point_count": len(kept),
        "bucket_s": 8 / count,
        "quality_counts": {
            k: sum(b[k] for b in buckets)
            for k in ("count", "good", "missing", "invalid", "continuity_breaks")
        },
    }
    return (
        {
            "metadata": metadata,
            "items": [{**r, "received_at": "2026-11-04T00:01:00Z"} for r in kept],
            "buckets": buckets,
        },
        original,
        context,
    )


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "value",
        "unit",
        "gap",
        "scope",
        "order",
        "missing",
        "quality",
        "extremum",
        "mean",
        "boolean",
    ],
)
def test_query_auditor_rejects_wrong_originals_segments_extrema_and_statistics(fault):
    actual, original, context = response()
    actual = copy.deepcopy(actual)
    if fault == "value":
        actual["items"][0]["sample"]["value"] += 1e-15
    elif fault == "boolean":
        actual["items"][0]["sample"]["value"] = False
    elif fault == "unit":
        actual["items"][0]["sample"]["unit"] = "km/h"
    elif fault == "gap":
        actual["items"][-2]["segment_id"] = "source:1:1"
    elif fault == "scope":
        actual["metadata"]["asset_id"] = "bus-002"
    elif fault == "order":
        actual["items"][0], actual["items"][1] = actual["items"][1], actual["items"][0]
    elif fault == "missing":
        actual["items"].pop()
    elif fault == "quality":
        actual["metadata"]["quality_counts"]["missing"] = 0
    elif fault == "extremum":
        actual["buckets"][0]["maximum"] = 9.0
    elif fault == "mean":
        actual["buckets"][0]["mean"] += 0.01
    case = {"from_s": 0, "to_s": 8}
    if fault:
        with pytest.raises(ValueError):
            audit.audit_plot(actual, original, context, case, 20)
    else:
        assert audit.audit_plot(actual, original, context, case, 20)["passed"]


def test_auditor_does_not_hide_fragmentation_by_truncating_original_points():
    actual, original, context = response(4)
    with pytest.raises(ValueError, match="coverage"):
        audit.audit_plot(actual, original, context, {"from_s": 0, "to_s": 8}, 4)


@pytest.mark.parametrize("fault", [None, "cap", "window", "cases", "duration", "signals"])
def test_query_cases_are_fixed_before_measurement(monkeypatch, tmp_path, fault):
    root = queries.ROOT
    protocol = json.loads((root / "benchmarks/history.json").read_bytes())
    frozen = json.loads((root / "benchmarks/history-query-cases.json").read_bytes())
    if fault == "cap":
        frozen["point_limit_total"] = 4999
    elif fault == "window":
        frozen["cases"][0]["to_s"] = 3599
    elif fault == "cases":
        frozen["cases"].pop()
    elif fault == "duration":
        protocol["query"]["window_s"] = 1800
    elif fault == "signals":
        protocol["query"]["signals"].pop()
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks/history.json").write_text(json.dumps(protocol))
    (tmp_path / "benchmarks/history-query-cases.json").write_text(json.dumps(frozen))
    monkeypatch.setattr(queries, "ROOT", tmp_path)
    if fault:
        with pytest.raises(ValueError):
            queries.cases()
    else:
        assert len(queries.cases()[0]["cases"]) == 20


def test_query_percentiles_use_nearest_rank_and_keep_slow_samples():
    result = queries.distribution([0.1] * 18 + [2.1, 10])
    assert result["count"] == 20 and result["p95_s"] == 2.1 and result["max_s"] == 10
    for invalid in ([], [float("nan")], [-1], [float("inf")]):
        with pytest.raises(ValueError):
            queries.distribution(invalid)
