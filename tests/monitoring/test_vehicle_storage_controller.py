import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/monitoring/qualify_vehicle_storage.py"
spec = importlib.util.spec_from_file_location("vehicle_storage_controller", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    sources = {}
    for name in (
        "scripts/monitoring/qualify_disk_boundaries.py",
        "scripts/monitoring/disk_boundary_probe.py",
        "src/evdt/monitoring/vehicle_storage.py",
        "src/evdt/monitoring/spool.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"reviewed\n")
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "passed": True,
        "temporary_vehicle_storage_mode": True,
        "temporary_vehicle_storage_lifecycle_passed": True,
        "mechanism_checks_passed": True,
        "queue_packaged_sources_match": True,
        "source_sha256": sources,
        "cleanup": {"owned_containers": [], "owned_volumes": [], "owned_loop_associations": []},
        "checks": {
            "vehicle_initialize": {"passed": True},
            "vehicle_fresh_reopen": {"passed": True},
        },
    }


def test_exact_current_sources_and_lifecycle_cleanup_required(report):
    assert module.accepted_service(report)
    (module.ROOT / "src/evdt/monitoring/spool.py").write_bytes(b"changed")
    assert not module.accepted_service(report)


@pytest.mark.parametrize(
    "change",
    [
        {"passed": False},
        {"temporary_vehicle_storage_mode": False},
        {"temporary_vehicle_storage_lifecycle_passed": 1},
        {"mechanism_checks_passed": False},
        {"queue_packaged_sources_match": False},
        {"source_sha256": {}},
        {"checks": {}},
        {"error": "incomplete"},
        {"cleanup_error": "still referenced"},
        {
            "cleanup": {
                "owned_containers": [],
                "owned_volumes": ["remaining"],
                "owned_loop_associations": [],
            }
        },
    ],
)
def test_partial_or_inconsistent_pass_cannot_be_accepted(report, change):
    assert not module.accepted_service({**report, **change})


@pytest.mark.parametrize("name", ["../escaped.py", "/absolute.py", "src/../escaped.py"])
def test_sources_cannot_escape_or_use_noncanonical_paths(report, name):
    report["source_sha256"][name] = "0" * 64
    assert not module.accepted_service(report)
