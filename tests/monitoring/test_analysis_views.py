import csv
import io

import pytest
from fastapi import HTTPException

from evdt.monitoring.analysis_views import csv_text, export_csv, summarize
from evdt.monitoring.map_context import at_offset


def test_stop_location_interpolates_along_lane_not_through_corner():
    assert at_offset([(0, 0), (10, 0), (10, 10)], 15) == (10, 5)
    assert at_offset([(0, 0), (10, 0)], 0) == (0, 0)
    with pytest.raises(ValueError):
        at_offset([(0, 0), (10, 0)], 20)


def test_aggregation_preserves_zero_extrema_and_explicit_empty_invalid_buckets():
    rows = [
        {"signal_id": "s", "sim_time_s": t, "sample": {"quality": q, "value": v}}
        for t, q, v in [
            (0, "good", 0),
            (1, "good", 100),
            (2, "good", -10),
            (3, "missing", None),
            (4, "invalid", None),
        ]
    ]
    result = summarize(rows, 0, 10, 5, ["s"])
    assert result[0] == {
        "signal_id": "s",
        "from_s": 0,
        "to_s": 5,
        "good": 3,
        "missing": 1,
        "invalid": 1,
        "minimum": -10,
        "maximum": 100,
        "mean": 30,
    }
    assert result[1]["mean"] is None and result[1]["good"] == 0
    with pytest.raises(HTTPException):
        summarize(rows, 0, 10000, 1, ["s"])


def test_csv_metadata_survives_empty_result_and_formula_text_is_escaped():
    metadata = {"run_id": "run", "signals": {"s": {"unit": "V"}}}
    empty = list(csv.DictReader(io.StringIO(export_csv(metadata, []))))
    assert len(empty) == 1 and empty[0]["record_type"] == "metadata"
    assert '"run_id":"run"' in empty[0]["metadata_json"]
    assert csv_text("=1+1") == "'=1+1" and csv_text(" @x") == "' @x"
    assert csv_text(-10) == -10 and csv_text(0) == 0
