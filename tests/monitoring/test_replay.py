"""Pure replay request and historical-artifact boundaries; real roles use the probe."""

import copy
import hashlib
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from evdt.config import ROOT
from evdt.monitoring.replay_api import SessionRequest
from evdt.monitoring.replay_repository import make_package, original_catalog


def test_replay_rejects_scope_and_window_before_database(monkeypatch):
    monkeypatch.setenv("SOURCE_RUN_ID", "configured")
    for run, start, end, status in [
        ("other", 0, 1, 403),
        ("configured", 2, 1, 422),
        ("configured", 0, 86401, 422),
    ]:
        with pytest.raises(HTTPException) as error:
            make_package("bus-001", start, end, run)
        assert error.value.status_code == status


def test_replay_request_has_no_command_config_or_nonfinite_clock():
    request = {"source_run_id": "a" * 36, "asset_id": "bus-001", "from_s": 0, "to_s": 1}
    assert SessionRequest(**request).from_s == 0
    for extra in (
        {"dispatch": True},
        {"from_s": float("nan")},
        {"to_s": float("inf")},
        {"from_s": -1},
    ):
        with pytest.raises(ValidationError):
            SessionRequest(**{**request, **extra})


def test_replay_catalog_is_input_bound_and_asset_scoped():
    raw = (ROOT / "catalog/monitoring-signals.json").read_bytes()
    expected = {
        "catalog_version": "1.2.0",
        "input_sha256": {"catalog": hashlib.sha256(raw).hexdigest()},
        "signals": {"motion.speed": {"period_s": 1, "freshness_s": 5}},
    }
    policy = {
        "sources": [
            {"asset_id": "bus-001", "source_id": "s", "expected": expected},
            {
                "asset_id": "bus-002",
                "source_id": "private-owner",
                "expected": copy.deepcopy(expected),
            },
        ]
    }
    result = original_catalog(policy, "bus-001")
    assert list(result["signals"]) == ["motion.speed"]
    assert result["signals"]["motion.speed"]["source_id"] == "s"
    assert "private-owner" not in json.dumps(result)
    policy["sources"][0]["expected"]["input_sha256"]["catalog"] = "0" * 64
    with pytest.raises(HTTPException) as error:
        original_catalog(policy, "bus-001")
    assert error.value.status_code == 409
