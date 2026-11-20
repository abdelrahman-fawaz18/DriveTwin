import copy
import importlib.util
import json
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/monitoring/storage_lease.py"
spec = importlib.util.spec_from_file_location("storage_lease", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def identity():
    return {"run_id": str(uuid.uuid4()), "asset_id": "bus-007", "source_id": "sumo-adapter"}


def ready(identity):
    owner = "evdt-disk-" + "a" * 32
    return {
        "version": "temporary-vehicle-storage/1.0.0",
        "owner": owner,
        "volumes": {r: owner + "-" + r for r in ("backing", "telemetry", "critical")},
        "binding": {
            "identity": identity,
            "owner": owner,
            "version": "vehicle-queue-storage/1.0.0",
            "mode": "nominal",
            "queue": {"catalog_version": "1.4.0", "encoded_limit_bytes": 32 * 1024**2},
        },
    }


def test_exact_readiness_identity_catalog_capacity_and_closed_names_required(identity):
    value = ready(identity)
    assert module.validate_ready(value, identity, "1.4.0", 32 * 1024**2) == value["owner"]
    assert value == ready(identity)
    for change in ({"asset_id": "bus-008"}, {"run_id": str(uuid.uuid4())}):
        with pytest.raises(RuntimeError, match="readiness"):
            module.validate_ready(value, {**identity, **change}, "1.4.0", 32 * 1024**2)
    for catalog, cap in (("1.3.0", 32 * 1024**2), ("1.4.0", 16384)):
        with pytest.raises(RuntimeError, match="readiness"):
            module.validate_ready(value, identity, catalog, cap)


@pytest.mark.parametrize(
    "change",
    ["owner", "volume", "binding_owner", "binding_version", "mode", "queue_type", "missing"],
)
def test_malformed_or_changed_readiness_cannot_authorize_mounts(identity, change):
    value = copy.deepcopy(ready(identity))
    if change == "owner":
        value["owner"] = "unrelated"
    elif change == "volume":
        value["volumes"]["telemetry"] = "unrelated"
    elif change == "binding_owner":
        value["binding"]["owner"] = "evdt-disk-" + "b" * 32
    elif change == "binding_version":
        value["binding"]["version"] = "other"
    elif change == "mode":
        value["binding"]["mode"] = "reduced-test"
    elif change == "queue_type":
        value["binding"]["queue"] = None
    else:
        value.pop("binding")
    with pytest.raises(RuntimeError, match="readiness"):
        module.validate_ready(value, identity, "1.4.0", 32 * 1024**2)


@pytest.fixture
def lease(tmp_path, monkeypatch, identity):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module.StorageLease(
        {},
        tmp_path / "evidence/p1/fixture",
        tmp_path / ".runtime/bounded-storage/fixture",
        identity,
        "sha256:" + "a" * 64,
    )


def test_release_requires_original_owner_and_identity(lease):
    state = lease.output / "services/state.json"
    state.parent.mkdir(parents=True)
    original = {"owner": ready(lease.identity)["owner"], "fault_identity": lease.identity}
    state.write_text(json.dumps(original))
    lease.ready = ready(lease.identity)
    assert lease.release_owner() == original["owner"]
    for changed in (
        {**original, "owner": "unrelated"},
        {**original, "owner": "evdt-disk-" + "b" * 32},
        {**original, "fault_identity": {**lease.identity, "asset_id": "bus-008"}},
    ):
        state.write_text(json.dumps(changed))
        with pytest.raises(RuntimeError, match="changed identity/owner"):
            lease.release_owner()
    assert lease.child is None and not (lease.control / "release.json").exists()


def test_no_worker_starts_for_outside_paths_or_unbounded_parameters(
    tmp_path, monkeypatch, identity
):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    output, runtime = (
        tmp_path / "evidence/p1/fixture",
        tmp_path / ".runtime/bounded-storage/fixture",
    )
    for change in (
        {"seconds": 3601},
        {"seconds": True},
        {"encoded_limit": 0},
        {"encoded_limit": True},
    ):
        with pytest.raises(RuntimeError, match="lifetime or capacity"):
            module.StorageLease({}, output, runtime, identity, "sha256:" + "a" * 64, **change)
    with pytest.raises(RuntimeError, match="canonical private"):
        module.StorageLease({}, tmp_path / "outside", runtime, identity, "sha256:" + "a" * 64)
    assert not output.exists() and not runtime.exists()
