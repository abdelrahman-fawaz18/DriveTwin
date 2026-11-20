import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/monitoring/qualify_disk_boundaries.py"
spec = importlib.util.spec_from_file_location("disk_boundary_guards", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
probe_spec = importlib.util.spec_from_file_location(
    "disk_boundary_probe", SCRIPT.with_name("disk_boundary_probe.py")
)
probe = importlib.util.module_from_spec(probe_spec)
probe_spec.loader.exec_module(probe)
OWNER = "evdt-disk-" + "a" * 32
NAME = OWNER + "-backing"


def volume(path):
    return {
        "Name": NAME,
        "Driver": "local",
        "Mountpoint": str(path),
        "Labels": {"com.evdt.owner": OWNER, "com.evdt.scope": module.SCOPE},
    }


def test_only_exact_daemon_owned_backing_path_is_accepted(tmp_path):
    root = tmp_path / "engine"
    backing = root / "volumes" / NAME / "_data"
    backing.mkdir(parents=True)
    assert module.backing_directory(volume(backing), root, NAME, OWNER) == backing
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(RuntimeError, match="escaped"):
        module.backing_directory(volume(other), root, NAME, OWNER)


@pytest.mark.parametrize(
    "change",
    [
        {"Driver": "foreign"},
        {"Name": "other"},
        {"Labels": {}},
        {"Labels": {"com.evdt.owner": "other", "com.evdt.scope": module.SCOPE}},
        {"Labels": {"com.evdt.owner": OWNER, "com.evdt.scope": "other"}},
    ],
)
def test_changed_volume_identity_or_labels_fail_before_file_access(tmp_path, change):
    record = {**volume(tmp_path), **change}
    with pytest.raises(RuntimeError):
        module.owned_volume(record, NAME, OWNER)


def test_closed_names_cannot_become_arbitrary_root_paths(tmp_path):
    for owner, name in (("other", "other-backing"), (OWNER, "/root"), (OWNER, OWNER + "-unknown")):
        with pytest.raises(RuntimeError, match="namespace"):
            module.owned_volume(volume(tmp_path), name, owner)


def test_image_must_be_exact_regular_unlinked_file_with_unchanged_identity(tmp_path):
    image = tmp_path / "telemetry.ext4"
    image.write_bytes(b"x" * 1024)
    original = module.checked_file(image, tmp_path, 1024)
    assert module.checked_file(image, tmp_path, 1024, original) == original
    with pytest.raises(RuntimeError, match="size"):
        module.checked_file(image, tmp_path, 2048)
    renamed = tmp_path / "moved"
    image.rename(renamed)
    image.write_bytes(b"x" * 1024)
    with pytest.raises(RuntimeError, match="identity"):
        module.checked_file(image, tmp_path, 1024, original)
    with pytest.raises(RuntimeError, match="approved"):
        module.checked_file(renamed, tmp_path, 1024)


def test_symlink_hardlink_directory_and_path_escape_are_refused(tmp_path):
    image = tmp_path / "telemetry.ext4"
    image.mkdir()
    with pytest.raises(RuntimeError):
        module.checked_file(image, tmp_path, 0)
    image.rmdir()
    original = tmp_path / "original"
    original.write_bytes(b"x" * 1024)
    image.symlink_to(original)
    with pytest.raises(RuntimeError):
        module.checked_file(image, tmp_path, 1024)
    image.unlink()
    image.hardlink_to(original)
    with pytest.raises(RuntimeError):
        module.checked_file(image, tmp_path, 1024)
    with pytest.raises(RuntimeError, match="approved"):
        module.checked_file(image, tmp_path / "other", 1024)


def test_reopen_check_never_recreates_a_missing_marker(tmp_path):
    with pytest.raises(RuntimeError, match="missing"):
        probe.ensure_marker(tmp_path, "telemetry")
    assert not (tmp_path / "marker.bin").exists()
    digest = probe.ensure_marker(tmp_path, "telemetry", create=True)
    assert probe.ensure_marker(tmp_path, "telemetry") == digest
    (tmp_path / "marker.bin").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        probe.ensure_marker(tmp_path, "telemetry")
    assert not (tmp_path / "marker.bin").exists()


def test_reopen_rejects_changed_marker_without_repairing_it(tmp_path):
    path = tmp_path / "marker.bin"
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="differs"):
        probe.ensure_marker(tmp_path, "telemetry")
    assert path.read_bytes() == b"changed"


def test_reopen_rejects_a_marker_link_even_if_the_target_matches(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(probe.marker("telemetry"))
    (tmp_path / "marker.bin").symlink_to(target)
    with pytest.raises(RuntimeError, match="linked"):
        probe.ensure_marker(tmp_path, "telemetry")
