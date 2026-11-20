"""Generic policy-scoped catalog selection, before introducing the held-back signal."""

import hashlib
import json

import pytest
from fastapi import HTTPException

from evdt.config import ROOT
from evdt.monitoring.asset_catalog import for_policy
from evdt.monitoring.formats import supported_pair


def test_catalog_is_asset_owned_and_hash_bound():
    catalog = json.loads((ROOT / "catalog/monitoring-signals.json").read_text())
    digest = hashlib.sha256((ROOT / "catalog/monitoring-signals.json").read_bytes()).hexdigest()
    policy = {
        "sources": [
            {
                "asset_id": "bus-001",
                "source_id": "sumo-adapter",
                "expected": {
                    "catalog_version": "1.2.0",
                    "input_sha256": {"catalog": digest},
                    "signals": {"motion.speed": {}},
                },
            }
        ]
    }
    selected = for_policy(policy, "bus-001")
    assert list(selected["signals"]) == ["motion.speed"]
    assert selected["signals"]["motion.speed"] == catalog["signals"]["motion.speed"]
    assert list(selected["assets"]) == ["bus-001"]
    with pytest.raises(HTTPException):
        for_policy(policy, "bus-002")
    policy["sources"][0]["expected"]["input_sha256"]["catalog"] = "0" * 64
    with pytest.raises(HTTPException) as error:
        for_policy(policy, "bus-001")
    assert error.value.status_code == 409


def test_only_declared_operational_pairs_are_allowed():
    assert supported_pair("3.0.0", "1.2.0")
    assert not supported_pair("2.1.0", "1.1.0")
    assert not supported_pair("3.0.0", "9.0.0")
