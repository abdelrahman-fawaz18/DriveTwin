import copy
import csv
import io
import json

import pytest

from evdt.monitoring.analysis_views import export_csv
from scripts.monitoring.history_download_audit import (
    validate_csv,
    validate_json,
    validate_raw_metadata,
)


def fixture():
    query = {
        "run_id": "run",
        "assets": ["bus-001"],
        "signals": ["motion.speed"],
        "from_s": 0,
        "to_s": 1,
        "expected_rows": 2,
    }
    expected = [
        {
            "run_id": "run",
            "asset_id": "bus-001",
            "event_id": f"event-{i}",
            "source_id": "source",
            "source_epoch": 1,
            "stream_seq": i,
            "sim_time_s": i,
            "observed_at": "2026-11-04T00:00:00Z",
            "schema_version": "3.2.0",
            "catalog_version": "1.4.0",
            "manifest_sha256": "a" * 64,
            "signal_id": "motion.speed",
            "sample": {
                "signal_id": "motion.speed",
                "value": value,
                "quality": "good",
                "unit": "m/s",
                "provenance": "simulated-observation",
            },
        }
        for i, value in enumerate([0.0, -5.25])
    ]
    metadata = {
        "run_id": "run",
        "asset_id": "bus-001",
        "as_of": "run:bus-001:9",
        "from_s": 0,
        "to_s": 1,
        "signals": {"motion.speed": {"unit": "m/s", "description": "Line, one\nLine two"}},
        "catalog_version": "1.4.0",
        "export_version": "1.1.0",
        "resolution": "raw",
        "download_complete": True,
        "row_count": 2,
        "page_limit": 1000,
        "page_count": 1,
        "download_row_limit": 100000,
    }
    items = [
        {**r, "received_at": "2026-11-04T00:01:00+00:00", "dashboard_revision": i + 5}
        for i, r in enumerate(expected)
    ]
    return query, expected, {"metadata": metadata, "items": items}


def csv_file(data):
    fields = [
        "record_type",
        "metadata_json",
        "event_id",
        "signal_id",
        "unit",
        "sim_time_s",
        "observed_at",
        "received_at",
        "source_id",
        "source_epoch",
        "stream_seq",
        "quality",
        "value",
        "raw_value",
        "reason",
        "provenance",
        "manifest_sha256",
        "schema_version",
        "catalog_version",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerow({"record_type": "metadata", "metadata_json": json.dumps(data["metadata"])})
    for row in data["items"]:
        writer.writerow({**row, **row["sample"], "record_type": "sample"})
    output.seek(0)
    return output


def test_real_csv_reader_handles_quoted_metadata_linebreaks_zero_and_negative_numbers():
    query, expected, data = fixture()
    assert validate_json(data, expected, query, "bus-001") == data["metadata"]
    assert validate_csv(csv_file(data), expected, query, "bus-001") == data["metadata"]


@pytest.mark.parametrize("fault", [None, "version", "limit", "policy", "snapshot", "value"])
def test_direct_export_audit_enforces_server_metadata_and_original_rows(fault):
    query, expected, data = fixture()
    query.update(
        as_of=data["metadata"]["as_of"],
        definitions=data["metadata"]["signals"],
        policy_sha256="a" * 64,
    )
    metadata = {
        "export_version": "1.0.0",
        "mode": "simulation",
        "run_id": query["run_id"],
        "asset_id": "bus-001",
        "as_of": query["as_of"],
        "from_s": 0.0,
        "to_s": 1.0,
        "time_basis": "simulation seconds",
        "policy_sha256": query["policy_sha256"],
        "resolution": "raw",
        "signals": query["definitions"],
        "row_limit": 10000,
    }
    if fault == "version":
        metadata["export_version"] = "1.1.0"
    elif fault == "limit":
        metadata["row_limit"] = 10000.0
    elif fault == "policy":
        metadata["policy_sha256"] = "b" * 64
    elif fault == "snapshot":
        metadata["as_of"] = "run:bus-001:10"
    elif fault == "value":
        data["items"] = copy.deepcopy(data["items"])
        data["items"][0]["sample"]["value"] = 1
    data["metadata"] = metadata
    stream = io.StringIO(export_csv(metadata, data["items"]))
    options = {"metadata_validator": validate_raw_metadata}
    if fault is None:
        with pytest.raises(ValueError, match="integer metadata"):
            validate_json(data, expected, query, "bus-001")
        assert validate_json(data, expected, query, "bus-001", **options) == metadata
        assert validate_csv(stream, expected, query, "bus-001", **options) == metadata
    else:
        with pytest.raises(ValueError):
            validate_json(data, expected, query, "bus-001", **options)
        with pytest.raises(ValueError):
            validate_csv(stream, expected, query, "bus-001", **options)


@pytest.mark.parametrize(
    "fault",
    [
        "value",
        "order",
        "scope",
        "unit",
        "recipe",
        "sequence",
        "time",
        "quality",
        "page_count",
        "incomplete",
        "row_count",
        "boolean_value",
        "boolean_count",
    ],
)
def test_download_audits_reject_changed_values_provenance_counts_and_incomplete_files(fault):
    query, expected, data = fixture()
    data = copy.deepcopy(data)
    if fault == "value":
        data["items"][0]["sample"]["value"] = 1e-15
    elif fault == "order":
        data["items"].reverse()
    elif fault == "scope":
        data["metadata"]["asset_id"] = "bus-002"
    elif fault == "unit":
        data["items"][0]["sample"]["unit"] = "km/h"
    elif fault == "recipe":
        data["items"][0]["manifest_sha256"] = "b" * 64
    elif fault == "sequence":
        data["items"][0]["stream_seq"] = 3
    elif fault == "time":
        data["items"][0]["observed_at"] = "2026-11-04T00:00:01Z"
    elif fault == "quality":
        data["items"][0]["sample"]["quality"] = "invalid"
    elif fault == "page_count":
        data["metadata"]["page_count"] = 2
    elif fault == "incomplete":
        data["metadata"]["download_complete"] = False
    elif fault == "boolean_value":
        data["items"][0]["sample"]["value"] = False
    elif fault == "boolean_count":
        data["metadata"]["page_count"] = True
    else:
        data["items"].pop()
    with pytest.raises(ValueError):
        validate_json(data, expected, query, "bus-001")
    with pytest.raises(ValueError):
        validate_csv(csv_file(data), expected, query, "bus-001")
