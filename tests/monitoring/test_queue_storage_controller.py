import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/monitoring/qualify_queue_storage.py"
spec = importlib.util.spec_from_file_location("queue_storage_controller", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    sources = {}
    for name in module.REQUIRED_SOURCE:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"reviewed\n")
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "passed": True,
        "queue_storage_integration_passed": True,
        "critical_fault_full_disk_passed": True,
        "queue_packaged_sources_match": True,
        "source_sha256": sources,
        "cleanup": {"owned_containers": [], "owned_volumes": [], "owned_loop_associations": []},
    }


def test_complete_exact_source_and_cleanup_proof_is_required(report):
    assert module.accepted(report)
    path = module.ROOT / next(iter(report["source_sha256"]))
    path.write_bytes(b"changed\n")
    assert not module.accepted(report)


@pytest.mark.parametrize(
    "change",
    [
        {"passed": False},
        {"queue_storage_integration_passed": False},
        {"critical_fault_full_disk_passed": False},
        {"queue_packaged_sources_match": 1},
        {"error": "failed"},
        {"cleanup_error": "still mounted"},
        {"source_sha256": {}},
        {
            "cleanup": {
                "owned_containers": [],
                "owned_volumes": ["leftover"],
                "owned_loop_associations": [],
            }
        },
    ],
)
def test_partial_or_inconsistent_child_pass_cannot_close_controller(report, change):
    assert not module.accepted({**report, **change})


def test_missing_required_hash_is_not_accepted(report):
    removed = next(iter(module.REQUIRED_SOURCE))
    del report["source_sha256"][removed]
    assert not module.accepted(report)


@pytest.mark.parametrize("absolute", [False, True])
def test_source_manifest_requires_canonical_project_relative_paths(report, absolute):
    name = (
        str(module.ROOT / "benchmarks/storage.json")
        if absolute
        else "scripts/../benchmarks/storage.json"
    )
    report["source_sha256"][name] = report["source_sha256"]["benchmarks/storage.json"]
    assert not module.accepted(report)
