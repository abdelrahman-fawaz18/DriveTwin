import hashlib
import json
import shutil

import pytest
from fastapi import HTTPException

from evdt import contract_profile
from evdt.monitoring import asset_catalog


@pytest.fixture
def context(tmp_path, monkeypatch):
    shutil.copytree(asset_catalog.ROOT / "contracts", tmp_path / "contracts")
    shutil.copytree(asset_catalog.ROOT / "catalog", tmp_path / "catalog")
    monkeypatch.setattr(asset_catalog, "ROOT", tmp_path)
    monkeypatch.setattr(contract_profile, "ROOT", tmp_path)
    asset_catalog.validated_catalog.cache_clear()
    path = tmp_path / "catalog/monitoring-fleet-1.4.0.json"
    policy = {
        "sources": [
            {
                "asset_id": "bus-001",
                "source_id": "sumo-adapter",
                "expected": {
                    "catalog_version": "1.4.0",
                    "input_sha256": {"catalog": hashlib.sha256(path.read_bytes()).hexdigest()},
                    "signals": {"motion.speed": {}},
                },
            }
        ]
    }
    yield tmp_path, path, policy
    asset_catalog.validated_catalog.cache_clear()


def test_repeated_views_validate_once_and_cannot_poison_later_definitions(context, monkeypatch):
    root, path, policy = context
    original = asset_catalog.Contract
    calls = []

    def construct(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(asset_catalog, "Contract", construct)
    first = asset_catalog.for_policy(policy, "bus-001")
    first["signals"]["motion.speed"]["unit"] = "changed by a caller"
    first["assets"]["bus-001"]["sources"].append("unregistered-source")
    later = asset_catalog.for_policy(policy, "bus-001")
    assert len(calls) == 1
    assert later["signals"]["motion.speed"]["unit"] == "m/s"
    assert later["assets"]["bus-001"]["sources"] == ["sumo-adapter"]
    assert set(later["signals"]) == {"motion.speed"}
    assert set(later["assets"]) == {"bus-001"}


def test_original_catalog_bytes_are_rechecked_on_every_warm_view(context):
    root, path, policy = context
    asset_catalog.for_policy(policy, "bus-001")
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(HTTPException, match="fingerprint changed"):
        asset_catalog.for_policy(policy, "bus-001")


def test_valid_profile_change_is_revalidated_not_hidden_by_cache(context, monkeypatch):
    root, path, policy = context
    original = asset_catalog.Contract
    calls = []

    def construct(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(asset_catalog, "Contract", construct)
    before = asset_catalog.for_policy(policy, "bus-001")
    profile = root / "contracts/monitoring.profile.json"
    profile.write_bytes(profile.read_bytes() + b"\n")
    assert asset_catalog.for_policy(policy, "bus-001") == before
    assert len(calls) == 2


def test_changed_schema_cannot_reuse_a_previously_valid_catalog(context):
    root, path, policy = context
    asset_catalog.for_policy(policy, "bus-001")
    schema = root / "contracts/monitoring.catalog-1.4.schema.json"
    data = json.loads(schema.read_bytes())
    data["properties"]["catalog_version"] = {"const": "unregistered-version"}
    schema.write_text(json.dumps(data))
    with pytest.raises(HTTPException, match="artifact invalid"):
        asset_catalog.for_policy(policy, "bus-001")


def test_registered_version_and_scope_still_fail_closed(context):
    root, path, policy = context
    with pytest.raises(HTTPException, match="not registered"):
        asset_catalog.for_policy(policy, "bus-002")
    policy["sources"][0]["expected"]["catalog_version"] = "future"
    with pytest.raises(HTTPException, match="unavailable"):
        asset_catalog.for_policy(policy, "bus-001")


def test_changed_dependencies_during_validation_are_not_cached(context, monkeypatch):
    root, path, policy = context
    original = asset_catalog.Contract

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        schema = root / "contracts/monitoring.catalog-1.4.schema.json"
        schema.write_bytes(schema.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(asset_catalog, "Contract", changing)
    with pytest.raises(HTTPException, match="dependencies changed"):
        asset_catalog.for_policy(policy, "bus-001")
    assert asset_catalog.validated_catalog.cache_info().currsize == 0


def test_catalog_version_mismatch_is_rejected_even_with_a_matching_byte_hash(context):
    root, path, policy = context
    catalog = json.loads(path.read_bytes())
    catalog["catalog_version"] = "1.3.0"
    path.write_text(json.dumps(catalog))
    policy["sources"][0]["expected"]["input_sha256"]["catalog"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    with pytest.raises(HTTPException, match="version changed"):
        asset_catalog.for_policy(policy, "bus-001")
