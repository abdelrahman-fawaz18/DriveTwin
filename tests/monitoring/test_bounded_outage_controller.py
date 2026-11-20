import copy
import hashlib
import importlib.util
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/monitoring/qualify_storage_outage.py"
spec = importlib.util.spec_from_file_location("bounded_outage_controller", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_request_cannot_select_other_projects_paths_or_reduced_capacity():
    run = uuid.uuid4()
    request = {
        "version": "bounded-fixture-request/1.0.0",
        "run_id": str(run),
        "project": "evdt-e1-" + run.hex[:12],
        "runtime": ".runtime/p1-uplink/" + run.hex,
        "image_id": "sha256:" + "a" * 64,
        "specs": [
            {
                "asset_id": "bus-001",
                "source_id": "sumo-adapter",
                "catalog_version": "1.2.0",
                "encoded_limit_bytes": 32 * 1024**2,
                "services": ["source", "sender", "probe"],
            }
        ],
    }
    assert module.validate_request(request) == run.hex
    for field, value in (
        ("project", "unrelated"),
        ("runtime", "../other"),
        ("image_id", "image:latest"),
    ):
        with pytest.raises(RuntimeError, match="explicit scope"):
            module.validate_request({**request, field: value})
    reduced = copy.deepcopy(request)
    reduced["specs"][0]["encoded_limit_bytes"] = 16384
    with pytest.raises(RuntimeError, match="explicit scope"):
        module.validate_request(reduced)


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    sources = {}
    for name in (
        "src/evdt/monitoring/transport.py",
        "src/evdt/monitoring/vehicle_storage.py",
        "src/evdt/workload/bound_storage_health.py",
        "scripts/monitoring/bounded_fixture.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"checked\n")
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "passed": True,
        "bounded_storage": {"merged_mounts_checked": True},
        "source_sha256": sources,
        "cleanup": {"containers": "", "volumes": ""},
        "outage": {
            "passed": True,
            "bounded_vehicle_storage": True,
            "outage_seconds": 300.01,
            "final": {"passed": True},
            "bounded_operational_health": {
                k: {"passed": True, "state": state}
                for k, state in (
                    ("warmup", "connected"),
                    ("sender_stopped_during_outage", "unavailable"),
                    ("reconnected_and_drained", "connected"),
                    ("final", "connected"),
                )
            },
        },
    }


def test_acceptance_requires_actual_health_stages_sources_and_cleanup(report):
    assert module.accepted_worker(report)
    report["outage"]["bounded_operational_health"]["final"]["state"] = "unavailable"
    assert not module.accepted_worker(report)


@pytest.mark.parametrize(
    "change",
    [
        {"passed": False},
        {"source_sha256": {}},
        {"error": "incomplete"},
        {"cleanup_error": "failed"},
        {"bounded_storage": {}},
        {"cleanup": {"containers": "remaining", "volumes": ""}},
    ],
)
def test_partial_worker_results_cannot_pass(report, change):
    assert not module.accepted_worker({**report, **change})


def test_short_outage_missing_health_or_changed_sources_fail(report):
    for changed in (299.99, 0):
        value = copy.deepcopy(report)
        value["outage"]["outage_seconds"] = changed
        assert not module.accepted_worker(value)
    value = copy.deepcopy(report)
    value["outage"]["bounded_operational_health"].pop("warmup")
    assert not module.accepted_worker(value)
    (module.ROOT / "src/evdt/monitoring/transport.py").write_bytes(b"changed")
    assert not module.accepted_worker(report)
