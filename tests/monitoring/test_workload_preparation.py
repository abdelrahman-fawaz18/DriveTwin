import copy
import json
import uuid

import pytest

from evdt.config import ROOT
from evdt.monitoring.contracts import monitoring_contract, observation_topic
from evdt.monitoring.formats import operational_format
from evdt.workload.measurement import (
    OffsetBound,
    account,
    availability,
    bracket,
    common_offset,
    percentile,
)
from scripts.monitoring.workload_scope import ADDITIONS, verify_registry


def test_exact_additive_registry_and_existing_core():
    current = json.loads((ROOT / "contracts/monitoring.profile.json").read_bytes())
    old = copy.deepcopy(current)
    for section, entries in ADDITIONS.items():
        for key in entries:
            del old[section][key]
    verify_registry(old, current)
    changed = copy.deepcopy(current)
    changed["max_payload_bytes"] += 1
    with pytest.raises(ValueError, match="Historical"):
        verify_registry(old, changed)


def test_workload_pair_and_all_ten_assets_use_exact_initial_signals():
    contract = monitoring_contract()
    fleet = contract.readers["1.4.0"].catalog
    assert fleet["signals"] == contract.readers["1.2.0"].catalog["signals"]
    assert set(fleet["assets"]) == {f"bus-{i:03}" for i in range(1, 11)}
    assert operational_format("1.4.0")["schema_version"] == "3.2.0"
    from evdt.workload.capacity import samples

    for asset in fleet["assets"]:
        body = {
            "schema_version": "3.2.0",
            "catalog_version": "1.4.0",
            "run_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "asset_id": asset,
            "source_id": "sumo-adapter",
            "source_epoch": 1,
            "session_id": str(uuid.uuid4()),
            "stream_seq": 0,
            "sim_time_s": 1,
            "mode": "simulation",
            "clock_domain": "simulation",
            "observed_at": "2026-11-03T00:00:00Z",
            "manifest_sha256": "0" * 64,
            "samples": samples(),
        }
        contract.validate(
            json.dumps(body).encode(),
            observation_topic(body["run_id"], asset, "sumo-adapter"),
            body["run_id"],
        )
        body["schema_version"] = "3.0.0"
        with pytest.raises(ValueError):
            contract.validate(
                json.dumps(body).encode(),
                observation_topic(body["run_id"], asset, "sumo-adapter"),
                body["run_id"],
            )


def test_sustained_route_preserves_connected_boundary_without_repeated_duplicate_edge():
    original = json.loads((ROOT / "simulation/montreal/mission.json").read_bytes())
    sustained = json.loads((ROOT / "simulation/montreal-sustained/mission.json").read_bytes())
    assert sustained["route_edges"] == original["route_edges"] + original["route_edges"][1:] * 4
    assert sustained["stop"] == original["stop"]
    assert sustained["step_s"] == original["step_s"]
    assert not any(a == b for a, b in zip(sustained["route_edges"], sustained["route_edges"][1:]))


def test_nearest_rank_and_missing_measurements():
    assert percentile(list(range(100)), 0.95) == 94
    assert percentile(list(range(100)), 0.99) == 98
    for values in [[], [float("nan")], [-1]]:
        with pytest.raises(ValueError):
            percentile(values, 0.95)
    with pytest.raises(ValueError, match="Missing"):
        availability({"a": 100, "b": 101}, {"a": 99.4}, OffsetBound(0.99, 1.01))
    result = availability({"a": 100}, {"a": 99.4}, OffsetBound(0.99, 1.01))
    assert result["p95_upper_s"] == pytest.approx(0.41)


def test_clock_brackets_and_uncertainty_are_not_hidden():
    value = bracket(100, 101.01, 101.02, 100.04)
    assert value.lower_s == pytest.approx(0.98)
    assert value.upper_s == pytest.approx(1.01)
    assert common_offset(
        OffsetBound(-0.02, 0.02), OffsetBound(-0.01, 0.03)
    ).uncertainty_s == pytest.approx(0.025)
    with pytest.raises(ValueError, match="changed"):
        common_offset(OffsetBound(0, 0.01), OffsetBound(1, 1.01))
    with pytest.raises(ValueError, match="uncertainty"):
        common_offset(OffsetBound(-0.2, 0.2), OffsetBound(-0.01, 0.01))


def test_event_conservation_rejects_missing_duplicate_or_multiple_dispositions():
    assert account(["a", "b"], ["a"], [], [], ["b"])["pending"] == 1
    for args in [
        (["a"], [], [], [], []),
        (["a", "a"], ["a"], [], [], []),
        (["a"], ["a"], [], [], ["a"]),
    ]:
        with pytest.raises(ValueError):
            account(*args)


@pytest.mark.parametrize("bounds", [(1, -1), (float("nan"), 1), (0, float("inf"))])
def test_invalid_clock_bounds_cannot_create_optimistic_latency(bounds):
    with pytest.raises(ValueError, match="clock bounds"):
        OffsetBound(*bounds)


def test_bounded_trusted_fleet_configuration_keeps_wire_limit_and_strict_json():
    from evdt.contract_profile import Rejected, decode
    from evdt.monitoring.run_policy import RUN_POLICY_MAX_BYTES, encoded, validate_policy

    catalog = monitoring_contract().readers["1.4.0"].catalog
    expected = {
        "schema_version": "3.2.0",
        "catalog_version": "1.4.0",
        "model_version": "synthetic-bus-plant/1.0.0",
        "input_sha256": {f"source-{i}.py": "a" * 64 for i in range(80)},
        "step_s": 0.2,
        "signals": {name: {"period_s": 1, "freshness_s": 5} for name in catalog["signals"]},
        "heartbeat_deadline_s": 5,
        "paced": True,
        "initial_energy_j": 100,
        "capacity_j": 200,
        "packages": {"eclipse-sumo": "1.28.0"},
        "traci_protocol": 22,
        "sumo_version": "SUMO 1.28.0",
    }
    policy = {
        "policy_version": "run-policy/1.0.0",
        "mode": "simulation",
        "run_id": str(uuid.uuid4()),
        "sources": [
            {"asset_id": asset, "source_id": "sumo-adapter", "expected": copy.deepcopy(expected)}
            for asset in catalog["assets"]
        ],
    }
    raw = encoded(policy)
    assert 65536 < len(raw) < RUN_POLICY_MAX_BYTES
    assert validate_policy(raw) == policy
    with pytest.raises(Rejected, match="payload_too_large"):
        decode(raw)
    with pytest.raises(Rejected, match="payload_too_large"):
        validate_policy(b" " * (RUN_POLICY_MAX_BYTES + 1))
    with pytest.raises(Rejected, match="invalid_json"):
        validate_policy(b'{"policy_version":1,"policy_version":2}')
