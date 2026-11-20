"""Recovery refuses incomplete, tampered, incompatible and redirected artifacts."""

import json
import sqlite3

import pytest

from evdt.recovery.bundle import REQUIRED, seal, verify
from evdt.recovery.probe import queue_inventory


@pytest.fixture
def backup(tmp_path):
    for name in REQUIRED:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"checkpoint fixture\n")
    seal(tmp_path, {"application_image_id": "sha256:fixture"})
    return tmp_path


def test_backup_exact_inventory_and_format(backup):
    assert set(verify(backup, "sha256:fixture")["files"]) == REQUIRED
    with pytest.raises(ValueError, match="Matching application"):
        verify(backup, "sha256:other")
    manifest = json.loads((backup / "manifest.json").read_bytes())
    manifest["format"] = "evdt-backup/99.0.0"
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsupported"):
        verify(backup, "sha256:fixture")


@pytest.mark.parametrize("change", ["truncate", "same_size", "missing", "extra", "traversal"])
def test_corruption_rejected(backup, change):
    dump = backup / "database.dump"
    if change == "truncate":
        dump.write_bytes(b"")
    elif change == "same_size":
        raw = dump.read_bytes()
        dump.write_bytes(b"X" + raw[1:])
    elif change == "missing":
        dump.unlink()
    elif change == "extra":
        (backup / "unexpected").write_text("unexpected")
    else:
        path = backup / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["files"]["../outside"] = manifest["files"].pop("database.dump")
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify(backup, "sha256:fixture")


def test_incomplete_and_symlink_are_not_backups(tmp_path):
    with pytest.raises(ValueError, match="Incomplete"):
        seal(tmp_path, {"application_image_id": "fixture"})
    (tmp_path / "link").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="link"):
        verify(tmp_path, "fixture")


def test_sqlite_backup_preserves_binary_packets_and_detects_corruption(tmp_path):
    source, dest = tmp_path / "source.sqlite", tmp_path / "restored.sqlite"
    with sqlite3.connect(source) as conn:
        for table in ("metadata", "queue_format", "pending", "terminal"):
            conn.execute(f"CREATE TABLE {table}(id integer,raw blob)")
            conn.execute(f"INSERT INTO {table} VALUES(?,?)", (1, b"\x00\xff\r\n"))
        conn.commit()
        with sqlite3.connect(dest) as target:
            conn.backup(target)
    assert queue_inventory(source) == queue_inventory(dest)
    with sqlite3.connect(dest) as conn:
        conn.execute("UPDATE pending SET raw=?", (b"changed",))
    assert queue_inventory(source) != queue_inventory(dest)
