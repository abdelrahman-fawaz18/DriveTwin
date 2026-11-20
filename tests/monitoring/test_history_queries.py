import json

import pytest
from fastapi import HTTPException

from evdt.monitoring import history_queries as history


@pytest.mark.parametrize(
    "signals", [[], ["a"] * 2, ["a", "b", "c", "d", "e", "f"], [None], [""], ["a" * 129]]
)
def test_signal_selection_is_distinct_registered_shape(signals):
    with pytest.raises(HTTPException):
        history.validate_window(signals, 0, 3600)


@pytest.mark.parametrize(
    "start,end",
    [
        (-1, 1),
        (2, 1),
        (0, 86401),
        (0, float("nan")),
        (0, float("inf")),
        (True, 1),
        (0, "1"),
        (0, 1e12 + 1),
    ],
)
def test_history_window_rejects_invalid_numbers_and_bounds(start, end):
    with pytest.raises(HTTPException):
        history.validate_window(["motion.speed"], start, end)


def test_export_continuation_binds_the_complete_query_and_row():
    signals = ["a", "b"]
    event = "00000000-0000-0000-0000-000000000001"
    saved = ["run:asset:9", "raw-export/1.0.0", signals, 0, 3600, 3, 20, event, "a"]
    arguments = ("run:asset:9", signals, 0, 3600, 3)
    assert history.export_cursor(json.dumps(saved), *arguments) == (20.0, event, "a")
    assert history.export_cursor(None, *arguments)[0] == -1
    for index, replacement in enumerate(
        ["other", "future", ["a"], 1, 3599, 4, -1, "invalid", "other"]
    ):
        changed = saved.copy()
        changed[index] = replacement
        with pytest.raises(HTTPException):
            history.export_cursor(json.dumps(changed), *arguments)
    for replacement in (True, None, float("nan"), float("inf"), "20"):
        changed = saved.copy()
        changed[6] = replacement
        with pytest.raises(HTTPException):
            history.export_cursor(json.dumps(changed), *arguments)


@pytest.mark.parametrize("budget", [0, 19, 5001, True, 20.5])
def test_point_budget_fails_before_database_work(monkeypatch, budget):
    def unexpected():
        pytest.fail("Invalid bounds must not open a database connection")

    monkeypatch.setattr(history, "connection", unexpected)
    with pytest.raises(HTTPException):
        history.plot("bus", ["a", "b", "c", "d", "e"], 0, 3600, "cursor", budget)


@pytest.mark.parametrize("bucket", [0, -1, float("nan"), True, 1e-300, 1])
def test_summary_limit_counts_all_selected_signals(monkeypatch, bucket):
    def unexpected():
        pytest.fail("Invalid bucket bounds must not open a database connection")

    monkeypatch.setattr(history, "connection", unexpected)
    with pytest.raises(HTTPException):
        history.summary("bus", ["a", "b", "c", "d", "e"], 0, 3600, "cursor", bucket)


def test_fractional_interval_count_cannot_exceed_total_summary_limit(monkeypatch):
    monkeypatch.setattr(history, "connection", lambda: pytest.fail("Bounds must fail first"))
    with pytest.raises(HTTPException):
        history.summary("bus", ["a", "b", "c"], 0, 1666.5, "cursor", 1)


def test_empty_bucket_does_not_invent_a_numeric_reading():
    result = history.buckets([], ["a", "b"], 0, 10, 2, 5)
    assert len(result) == 4
    assert all(
        row["count"] == 0 and row["minimum"] is None and row["mean"] is None for row in result
    )
    assert [(row["from_s"], row["to_s"]) for row in result[:2]] == [(0, 5), (5, 10)]


def test_inconsistent_quality_or_nonfinite_statistics_fail_closed():
    row = {
        "signal_id": "a",
        "bucket": 0,
        "count": 2,
        "good": 1,
        "missing": 0,
        "invalid": 0,
        "minimum": 1,
        "maximum": 1,
        "mean": 1,
        "first_good_s": 0,
        "last_good_s": 0,
        "continuity_breaks": 0,
    }
    with pytest.raises(HTTPException):
        history.buckets([row], ["a"], 0, 1, 1, 1)
    row.update(count=1, mean=float("nan"))
    with pytest.raises(HTTPException):
        history.buckets([row], ["a"], 0, 1, 1, 1)
