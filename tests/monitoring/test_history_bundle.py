import json
import uuid

import pytest

from evdt.recovery import bundle
from evdt.recovery import history_bundle as history


def fixture(root):
    metadata = {
        "scope": "database-history-only",
        "protocol": "history-volume/1.0.0",
        "assets": ["bus-001"],
        "samples_per_asset": 60,
        "source_cluster": "123",
        "application_image_id": "sha256:" + "a" * 64,
        "run_id": str(uuid.uuid4()),
    }
    for name in history.required(metadata):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"synthetic checkpoint component\n")
    return metadata


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "wrong_image",
        "changed",
        "missing",
        "extra",
        "format",
        "unsafe",
        "link",
        "duplicate_key",
        "boolean_size",
    ],
)
def test_explicit_history_checkpoint_integrity_and_legacy_queue_boundary(tmp_path, fault):
    root = tmp_path / "checkpoint"
    metadata = fixture(root)
    original_required = set(bundle.REQUIRED)
    manifest = history.seal(root, metadata)
    assert history.verify(root, metadata["application_image_id"]) == manifest
    assert bundle.FORMAT == "evdt-backup/1.0.0" and bundle.REQUIRED == original_required
    assert {"queues/bus-001.sqlite", "queues/bus-002.sqlite"} <= bundle.REQUIRED
    with pytest.raises(ValueError, match="format"):
        bundle.verify(root, metadata["application_image_id"])
    image = metadata["application_image_id"]
    if fault == "wrong_image":
        image = "sha256:" + "b" * 64
    elif fault == "changed":
        (root / "database.dump").write_bytes(b"changed")
    elif fault == "missing":
        (root / "inventory.json").unlink()
    elif fault == "extra":
        (root / "unexpected-secret.txt").write_bytes(b"synthetic")
    elif fault == "format":
        manifest["format"] = bundle.FORMAT
        (root / "manifest.json").write_text(json.dumps(manifest))
    elif fault == "unsafe":
        manifest["files"]["../outside"] = manifest["files"].pop("queries.json")
        (root / "manifest.json").write_text(json.dumps(manifest))
    elif fault == "link":
        (root / "database.dump").unlink()
        outside = tmp_path / "outside"
        outside.write_text("synthetic")
        (root / "database.dump").symlink_to(outside)
    elif fault == "duplicate_key":
        text = (root / "manifest.json").read_text()
        (root / "manifest.json").write_text(
            text.replace('"format":', '"format": "ignored", "format":', 1)
        )
    elif fault == "boolean_size":
        manifest["files"]["queries.json"]["size"] = True
        (root / "manifest.json").write_text(json.dumps(manifest))
    if fault:
        with pytest.raises(ValueError):
            history.verify(root, image)


@pytest.mark.parametrize(
    "change",
    [
        {"scope": "queues"},
        {"assets": ["../escape"]},
        {"assets": ["bus-001", "bus-001"]},
        {"samples_per_asset": True},
        {"samples_per_asset": 0},
        {"samples_per_asset": 7201},
        {"source_cluster": 123},
        {"application_image_id": "tag"},
        {"run_id": "other"},
    ],
)
def test_history_scope_metadata_cannot_remove_required_source_provenance(tmp_path, change):
    metadata = fixture(tmp_path)
    metadata.update(change)
    with pytest.raises(ValueError):
        history.required(metadata)
