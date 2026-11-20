import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from evdt.contract_profile import Rejected
from evdt.monitoring.run_policy import encoded, presentation, validate_manifest, validate_policy
from evdt.monitoring.sensors import SensorBank


def metadata():
    identity = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "session_id": "00000000-0000-0000-0000-000000000002",
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "source_epoch": 1,
    }
    expected = {
        "schema_version": "3.0.0",
        "catalog_version": "1.2.0",
        "model_version": "synthetic-bus-plant/1.0.0",
        "input_sha256": {"plant.json": "a" * 64},
        "step_s": 0.2,
        "signals": {"location.latitude": {"period_s": 1, "freshness_s": 5}},
        "heartbeat_deadline_s": 5,
        "paced": True,
        "initial_energy_j": 100,
        "capacity_j": 200,
        "packages": {"eclipse-sumo": "1.28.0"},
        "traci_protocol": 22,
        "sumo_version": "SUMO 1.28.0",
    }
    manifest = {
        **expected,
        "manifest_version": "source-run/2.0.0",
        "mode": "simulation",
        "identity": identity,
        "asset_id": "bus-001",
        "evidence_class": "assumed",
        "sampling": {"default_period_s": 1, "period_overrides_s": {}},
        "minimum_sim_time_s": 0,
        "source_epoch_start_sim_s": 0,
        "initial_state_sim_s": 0.2,
        "battery_power_source": "actual device step energy",
        "insertion_policy": "explicit initialization",
        "driver_options_sha256": "b" * 64,
    }
    policy = {
        "policy_version": "run-policy/1.0.0",
        "mode": "simulation",
        "run_id": identity["run_id"],
        "sources": [{"asset_id": "bus-001", "source_id": "sumo-adapter", "expected": expected}],
    }
    return policy, manifest


def test_recipe_matches_trusted_policy_and_unknown_sources_cannot_enroll():
    policy, manifest = metadata()
    assert validate_policy(encoded(policy)) == policy
    assert validate_manifest(encoded(manifest), policy) == manifest
    other = copy.deepcopy(manifest)
    other["identity"]["source_id"] = "unapproved"
    with pytest.raises(Rejected, match="source_not_authorized"):
        validate_manifest(encoded(other), policy)


@pytest.mark.parametrize(
    "key,value",
    [
        ("model_version", "other-model"),
        ("step_s", 0.4),
        ("schema_version", "2.1.0"),
        ("input_sha256", {"plant.json": "c" * 64}),
    ],
)
def test_recipe_cannot_change_policy_fields(key, value):
    policy, manifest = metadata()
    manifest[key] = value
    with pytest.raises(Rejected):
        validate_manifest(encoded(manifest), policy)


def test_policy_rejects_competing_owners_and_incoherent_timing():
    policy, _ = metadata()
    second = copy.deepcopy(policy["sources"][0])
    second["source_id"] = "competing-source"
    policy["sources"].append(second)
    with pytest.raises(ValueError, match="Competing"):
        validate_policy(encoded(policy))


def test_recipe_sampling_is_bound_and_hidden_fault_schedule_is_not_public():
    policy, manifest = metadata()
    manifest["sampling"]["default_period_s"] = 2
    with pytest.raises(Rejected, match="manifest_sampling_mismatch"):
        validate_manifest(encoded(manifest), policy)
    policy, manifest = metadata()
    manifest["sampling"]["seed"] = 123
    with pytest.raises(Rejected, match="metadata_schema"):
        validate_manifest(encoded(manifest), policy)
    policy, _ = metadata()
    policy["sources"][0]["expected"]["signals"]["location.latitude"]["period_s"] = 0.3
    with pytest.raises(ValueError, match="cadence"):
        validate_policy(encoded(policy))


def test_zero_unknown_and_invalid_are_separate_from_freshness():
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    timing = {"period_s": 1, "freshness_s": 5}
    clock = {"state": "running", "observed_at": now, "sim_time_s": 10, "pause_started_at": None}
    assert presentation(None, None, None, clock, timing, now, 5)["status"] == "unknown"
    good = {"value": 0, "quality": "good"}
    assert presentation(good, 10, now, clock, timing, now, 5)["status"] == "fresh"
    invalid = {"value": None, "quality": "invalid", "reason": "sensor_fault"}
    result = presentation(invalid, 10, now, clock, timing, now, 5)
    assert result["quality"] == "invalid" and result["freshness"] == "fresh"
    result = presentation(invalid, 10, now - timedelta(seconds=10), clock, timing, now, 5)
    assert result["quality"] == "invalid" and result["freshness"] == "stale"


def test_pause_keeps_eligible_reading_paused_but_does_not_resurrect_old_values():
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    clock = {
        "state": "paused",
        "observed_at": now,
        "sim_time_s": 10,
        "pause_started_at": now - timedelta(seconds=20),
    }
    timing = {"period_s": 1, "freshness_s": 5}
    good = {"value": 0, "quality": "good"}
    assert (
        presentation(good, 10, now - timedelta(seconds=21), clock, timing, now, 5)["freshness"]
        == "paused"
    )
    assert (
        presentation(good, 10, now - timedelta(seconds=30), clock, timing, now, 5)["freshness"]
        == "stale"
    )
    assert (
        presentation(good, 1, now - timedelta(seconds=21), clock, timing, now, 5)["freshness"]
        == "stale"
    )
    clock["observed_at"] = now - timedelta(seconds=6)
    result = presentation(good, 10, now - timedelta(seconds=21), clock, timing, now, 5)
    assert result["source_state"] == "disconnected" and result["freshness"] == "stale"
    clock.update(state="running", observed_at=now, pause_started_at=None)
    assert (
        presentation(good, 10, now - timedelta(seconds=21), clock, timing, now, 5)["freshness"]
        == "stale"
    )


def test_sensor_missing_invalid_recovery_and_nonfinite_are_encoded_honestly():
    catalog = {"signals": {"x": {"minimum": 0, "maximum": 10, "unit": "1", "freshness_s": 5}}}
    bank = SensorBank(
        catalog,
        0.2,
        {
            "default_period_s": 1,
            "period_overrides_s": {},
            "faults": {"x": {"missing": [[1, 2]], "invalid": [[2, 3]]}},
        },
    )
    missing = bank.sample(5, {"x": 1})[0]
    assert (missing["value"], missing["quality"], missing["reason"]) == (None, "missing", "dropout")
    invalid = bank.sample(10, {"x": 1})[0]
    assert (
        invalid["value"] is None and invalid["raw_value"] == 1 and invalid["quality"] == "invalid"
    )
    assert bank.sample(15, {"x": 0})[0]["value"] == 0
    bad = bank.sample(20, {"x": float("nan")})[0]
    assert bad["reason"] == "nonfinite" and "raw_value" not in bad
    json.dumps(bad, allow_nan=False)
    outside = bank.sample(25, {"x": 99})[0]
    assert outside["quality"] == "invalid" and outside["raw_value"] == 99


def test_noise_is_reproducible_and_bias_quantization_are_not_clipping():
    catalog = {"signals": {"x": {"minimum": -10, "maximum": 10, "unit": "1"}}}
    config = {
        "default_period_s": 1,
        "period_overrides_s": {},
        "seed": 100,
        "faults": {"x": {"noise_std": 0.1}},
    }
    a, b = SensorBank(catalog, 0.2, config), SensorBank(catalog, 0.2, config)
    left = [a.sample(t, {"x": 1}) for t in range(5, 51, 5)]
    assert left == [b.sample(t, {"x": 1}) for t in range(5, 51, 5)]
    assert len({row[0]["value"] for row in left}) > 1
    config["faults"]["x"] = {"bias": 0.1, "quantum": 0.5}
    c = SensorBank(catalog, 0.2, config)
    assert c.sample(5, {"x": 1.27})[0]["value"] == 1.5
    assert c.timings()["x"] == {"period_s": 1, "freshness_s": 5}
