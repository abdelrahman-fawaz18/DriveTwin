import copy
import sqlite3
import uuid
from pathlib import Path

import pytest

from evdt.monitoring import vehicle_storage as module
from evdt.monitoring.critical_fault import FaultUnavailable
from evdt.monitoring.spool import Identity, SpoolFull


@pytest.fixture
def binding():
    identity = Identity(str(uuid.uuid4()), "bus-003", "sumo-adapter")
    return identity, {
        "version": module.VERSION,
        "mode": "nominal",
        "owner": "evdt-disk-" + "a" * 32,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "identity": identity.__dict__,
        "queue": {
            "schema_version": "3.2.0",
            "catalog_version": "1.4.0",
            "encoded_limit_bytes": module.LIMIT,
            "loss_record_limit": 16384,
        },
        "roles": {
            role: {"device": n, "filesystem_bytes": budget - 4096, "image_bytes": budget}
            for n, (role, budget) in enumerate(module.BUDGETS.items(), 100)
        },
    }


def test_explicit_nominal_and_labeled_reduced_profiles(binding):
    identity, body = binding
    assert module.checked_binding(module.canonical(body), identity) == body
    body["queue"]["encoded_limit_bytes"] = 1000
    with pytest.raises(module.StorageUnavailable, match="test"):
        module.checked_binding(module.canonical(body), identity)
    body["mode"] = "reduced-test"
    assert module.checked_binding(module.canonical(body), identity) == body


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "owner",
        "identity",
        "same_disk",
        "budget",
        "extra",
        "boolean_limit",
        "huge_limit",
        "audit",
        "format",
    ],
)
def test_binding_identity_budget_and_format_changes_are_refused(binding, change):
    identity, body = binding
    if change in ("version", "owner"):
        body[change] = "wrong"
    elif change == "identity":
        body["identity"] = {**body["identity"], "asset_id": "bus-004"}
    elif change == "same_disk":
        body["roles"]["critical"]["device"] = body["roles"]["telemetry"]["device"]
    elif change == "budget":
        body["roles"]["critical"]["image_bytes"] += 1
    elif change == "extra":
        body["unexpected"] = True
    elif change == "boolean_limit":
        body["queue"]["encoded_limit_bytes"] = True
    elif change == "huge_limit":
        body["queue"]["encoded_limit_bytes"] = module.LIMIT + 1
    elif change == "audit":
        body["queue"]["loss_record_limit"] = 1
    else:
        body["queue"]["schema_version"] = "3.1.0"
    with pytest.raises(module.StorageUnavailable):
        module.checked_binding(module.canonical(body), identity)


def test_duplicate_and_oversized_binding_refused(binding):
    identity, body = binding
    with pytest.raises(ValueError, match="Duplicate"):
        module.checked_binding(b'{"version":1,"version":2}', identity)
    with pytest.raises(module.StorageUnavailable, match="bound"):
        module.checked_binding(b" " * 16385 + module.canonical(body), identity)


@pytest.fixture
def storage(tmp_path, monkeypatch, binding):
    identity, body = binding
    roots = {role: tmp_path / role for role in module.BUDGETS}
    for path in roots.values():
        path.mkdir()
    monkeypatch.setattr(module, "MOUNTS", roots)
    # These component tests use real SQLite/fault files on temporary storage.
    # Actual mount/boot/device validation is tested separately and physically.
    monkeypatch.setattr(module.BoundedStorage, "check_mounts", lambda self: None)
    return module.BoundedStorage(module.canonical(body), identity)


def test_normal_open_never_initializes_missing_storage(storage):
    path = module.MOUNTS["telemetry"] / "queue.db"
    with pytest.raises(FileNotFoundError):
        storage.open(path, "1.4.0")
    assert not path.exists() and not storage.fault_folder.exists() and not storage.marker.exists()


def test_initialize_reopen_and_refuse_reinitialization_or_changed_binding(storage):
    storage.initialize()
    path = module.MOUNTS["telemetry"] / "queue.db"
    assert storage.open(path, "1.4.0").counts()["generated"] == 0
    assert storage.status()["critical_fault"]["healthy"]
    original = storage.marker.read_bytes()
    with pytest.raises(module.StorageUnavailable, match="existing or partial"):
        storage.initialize()
    body = copy.deepcopy(storage.binding)
    body["owner"] = "evdt-disk-" + "b" * 32
    changed = module.BoundedStorage(module.canonical(body), storage.identity)
    with pytest.raises(module.StorageUnavailable, match="binding differs"):
        changed.open(path, "1.4.0")
    assert storage.marker.read_bytes() == original


def test_wrong_queue_path_catalog_missing_queue_and_fault_copy_refused(storage):
    storage.initialize()
    path = module.MOUNTS["telemetry"] / "queue.db"
    for candidate, catalog in ((path.with_name("other.db"), "1.4.0"), (path, "1.3.0")):
        with pytest.raises(module.StorageUnavailable, match="path or selected catalog"):
            storage.open(candidate, catalog)
    path.rename(path.with_name("saved.db"))
    with pytest.raises(FileNotFoundError):
        storage.open(path, "1.4.0")
    assert not path.exists()
    path.with_name("saved.db").rename(path)
    for n in (0, 1):
        (storage.fault_folder / f"fault.{n}").unlink()
    with pytest.raises(FaultUnavailable):
        storage.open(path, "1.4.0")


def test_capacity_failure_is_sticky_and_preserves_empty_queue(storage):
    storage.binding["queue"]["encoded_limit_bytes"] = 1
    storage.binding["mode"] = "reduced-test"
    storage.raw = module.canonical(storage.binding)
    storage.initialize()
    queue = storage.open(module.MOUNTS["telemetry"] / "queue.db", "1.4.0")
    queue.start_session()
    with pytest.raises(SpoolFull):
        queue.enqueue(
            [
                {
                    "signal_id": "location.latitude",
                    "value": 45.51,
                    "unit": "deg",
                    "quality": "good",
                    "provenance": "simulated-observation",
                }
            ],
            1,
            manifest_sha256="0" * 64,
        )
    state = storage.status()
    assert state["critical_fault"]["first_reason"] == "telemetry_capacity_refused"
    assert not state["critical_fault"]["healthy"]
    reopened = storage.open(module.MOUNTS["telemetry"] / "queue.db", "1.4.0")
    assert reopened.counts()["generated"] == 0
    assert storage.status()["critical_fault"]["occurrences"] == 1


@pytest.mark.parametrize("change", ["empty", "missing_table"])
def test_normal_open_does_not_heal_an_existing_incomplete_database(storage, change):
    storage.initialize()
    path = module.MOUNTS["telemetry"] / "queue.db"
    if change == "empty":
        path.write_bytes(b"")
    else:
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TABLE queue_capacity")
    damaged = path.read_bytes()
    with pytest.raises(module.StorageUnavailable, match="schema differs"):
        storage.open(path, "1.4.0")
    assert path.read_bytes() == damaged


def test_one_missing_fault_copy_refuses_open_without_recreating_it(storage):
    storage.initialize()
    missing = storage.fault_folder / "fault.1"
    missing.unlink()
    with pytest.raises(module.StorageUnavailable, match="degraded"):
        storage.open(module.MOUNTS["telemetry"] / "queue.db", "1.4.0")
    assert not missing.exists()


def factory_environment(monkeypatch, storage):
    for key, value in (
        ("EVDT_RUN_ID", storage.identity.run_id),
        ("ASSET_ID", storage.identity.asset_id),
        ("SOURCE_ID", storage.identity.source_id),
        ("CATALOG_VERSION", "1.4.0"),
        ("SPOOL_FILE", str(module.MOUNTS["telemetry"] / "queue.db")),
    ):
        monkeypatch.setenv(key, value)


def test_normal_factory_selects_explicit_binding_and_current_metrics(
    storage, tmp_path, monkeypatch
):
    import threading

    from evdt.monitoring.metrics import queue_diagnostics, spool_status
    from evdt.monitoring.transport import open_spool

    factory_environment(monkeypatch, storage)
    storage.initialize()
    selected = tmp_path / "selected-binding.json"
    selected.write_bytes(storage.raw)
    monkeypatch.setenv("EVDT_STORAGE_BINDING_FILE", str(selected))
    queue = open_spool()
    assert isinstance(queue, module.BoundedSpool)
    measured = spool_status(queue, threading.Event())
    assert measured["dropped_records"] == 0 and measured["overflow_indication"] is False
    assert measured["physical_quota_enforced"] is True
    assert queue_diagnostics({"available": True, **measured})["dropped_records"] == 0
    storage.fault.trip("controlled_test_fault")
    assert (
        spool_status(queue, threading.Event())["storage"]["critical_fault"]["first_reason"]
        == "controlled_test_fault"
    )


@pytest.mark.parametrize("configured", ["missing", "empty"])
def test_selected_but_unavailable_binding_never_falls_back_to_legacy(
    storage, tmp_path, monkeypatch, configured
):
    from evdt.monitoring.transport import open_spool

    factory_environment(monkeypatch, storage)
    monkeypatch.setenv(
        "EVDT_STORAGE_BINDING_FILE",
        str(tmp_path / "missing.json") if configured == "missing" else "",
    )
    with pytest.raises((OSError, module.StorageUnavailable)):
        open_spool()
    assert not (module.MOUNTS["telemetry"] / "queue.db").exists()


def test_unselected_legacy_factory_retains_unknown_drop_and_quota_status(storage, monkeypatch):
    import threading

    from evdt.monitoring.metrics import spool_status
    from evdt.monitoring.transport import open_spool

    factory_environment(monkeypatch, storage)
    monkeypatch.delenv("EVDT_STORAGE_BINDING_FILE", raising=False)
    queue = open_spool()
    assert queue.capacity_policy is None and not isinstance(queue, module.BoundedSpool)
    measured = spool_status(queue, threading.Event())
    assert measured["dropped_records"] is None and measured["overflow_indication"] is None
    assert measured["physical_quota_enforced"] is False and measured["storage"] is None


def test_linked_or_changed_durable_marker_is_not_trusted(storage):
    storage.initialize()
    saved = storage.marker.with_name("saved.json")
    storage.marker.rename(saved)
    storage.marker.symlink_to(saved)
    with pytest.raises(OSError):
        storage.check_ready()
    storage.marker.unlink()
    storage.marker.write_bytes(b"{}")
    with pytest.raises(module.StorageUnavailable, match="binding differs"):
        storage.check_ready()


@pytest.mark.parametrize(
    "line,allowed",
    [
        ("1 0 7:2 / /spool rw,nosuid,nodev,noexec - ext4 /dev/loop2 rw", True),
        ("1 0 7:2 / /spool rw - ext4 /dev/loop2 rw", False),
        ("1 0 7:2 /inner /spool rw,nosuid,nodev,noexec - ext4 /dev/loop2 rw", False),
        ("1 0 7:2 / /spool rw,nosuid,nodev,noexec - tmpfs tmpfs rw", False),
        ("1 0 7:2 / /other rw,nosuid,nodev,noexec - ext4 /dev/loop2 rw", False),
    ],
)
def test_mount_parser_requires_exact_independent_restricted_filesystem(monkeypatch, line, allowed):
    real_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **kw: (
            line if str(self) == "/proc/self/mountinfo" else real_read(self, *a, **kw)
        ),
    )
    if allowed:
        assert module.mount_record(Path("/spool")) == module.os.makedev(7, 2)
    else:
        with pytest.raises(module.StorageUnavailable):
            module.mount_record(Path("/spool"))


def test_actual_ordinary_directory_cannot_substitute_for_a_mount(tmp_path, binding, monkeypatch):
    identity, body = binding
    roots = {role: tmp_path / role for role in module.BUDGETS}
    for folder in roots.values():
        folder.mkdir()
    monkeypatch.setattr(module, "MOUNTS", roots)
    storage = module.BoundedStorage(module.canonical(body), identity)
    with pytest.raises(module.StorageUnavailable, match="exact storage mount"):
        storage.check_mounts()
    body["boot_id"] = str(uuid.uuid4())
    with pytest.raises(module.StorageUnavailable, match="another Ubuntu boot"):
        module.BoundedStorage(module.canonical(body), identity).check_mounts()
