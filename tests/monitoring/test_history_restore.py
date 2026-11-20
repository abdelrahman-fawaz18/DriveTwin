import copy

import pytest

from scripts.monitoring.qualify_history_restore import equal_query


@pytest.mark.parametrize(
    "path",
    ["sample", "identity", "count", "scope", "missing", "type", "metadata_mean", "mean_error"],
)
def test_restore_query_comparison_keeps_originals_exact_and_rejects_real_differences(path):
    expected = {
        "metadata": {"asset": "bus-001", "mean": 1.0},
        "items": [{"event_id": "event", "sample": {"value": 1.0}, "mean": 10.0, "count": 5}],
        "buckets": [{"mean": 20.0}],
    }
    actual = copy.deepcopy(expected)
    assert equal_query(actual, expected)
    if path == "sample":
        actual["items"][0]["sample"]["value"] += 1e-15
    elif path == "identity":
        actual["items"][0]["event_id"] = "another"
    elif path == "count":
        actual["items"][0]["count"] = 6
    elif path == "scope":
        actual["metadata"]["asset"] = "bus-002"
    elif path == "missing":
        actual["items"] = []
    elif path == "type":
        actual["items"][0]["count"] = 5.0
    elif path == "metadata_mean":
        actual["metadata"]["mean"] += 1e-15
    else:
        actual["items"][0]["mean"] += 1e-8
    assert not equal_query(actual, expected)


def test_only_aggregate_mean_roundoff_is_tolerated_and_nonfinite_means_fail():
    original = {"items": [{"mean": 10.0}], "buckets": [{"mean": 20.0}]}
    assert equal_query(
        {"items": [{"mean": 10.0 + 1e-13}], "buckets": [{"mean": 20.0 - 1e-13}]}, original
    )
    for number in [float("inf"), float("nan")]:
        assert not equal_query({"items": [{"mean": number}], "buckets": [{"mean": 20.0}]}, original)
