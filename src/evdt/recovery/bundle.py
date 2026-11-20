"""Versioned, integrity-checked private backup directories without archive extraction."""

import hashlib
import json
from pathlib import PurePosixPath

FORMAT = "evdt-backup/1.0.0"
REQUIRED = {
    "application-queries.json",
    "recordings/bus-001.json",
    "recordings/bus-002.json",
    "database.dump",
    "inventory.json",
    "run-config/run_policy.json",
    "run-config/map_context.json",
    "run-config/map_context2.json",
    "queues/bus-001.sqlite",
    "queues/bus-002.sqlite",
    "artifacts/contracts/monitoring.profile.json",
}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_files(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Backup must be a real directory")
    paths = list(root.rglob("*"))
    if any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths):
        raise ValueError("Backup contains a link or special file")
    return {p.relative_to(root).as_posix(): p for p in paths if p.is_file()}


def seal(root, metadata):
    files = regular_files(root)
    if "manifest.json" in files or not REQUIRED <= files.keys():
        raise ValueError("Incomplete checkpoint or already sealed")
    manifest = {
        "format": FORMAT,
        "metadata": metadata,
        "files": {
            name: {"size": p.stat().st_size, "sha256": sha(p)} for name, p in sorted(files.items())
        },
    }
    with (root / "manifest.json").open("x", newline="\n") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    verify(root, metadata["application_image_id"])
    return manifest


def verify(root, image_id):
    files = regular_files(root)
    if "manifest.json" not in files:
        raise ValueError("Unsealed backup")
    manifest = json.loads(files.pop("manifest.json").read_bytes())
    if manifest.get("format") != FORMAT:
        raise ValueError("Unsupported backup format")
    metadata = manifest["metadata"]
    if metadata["application_image_id"] != image_id:
        raise ValueError("Matching application image required")
    expected = manifest["files"]
    if not REQUIRED <= expected.keys() or set(expected) != set(files):
        raise ValueError("Missing or unexpected backup files")
    for name, entry in expected.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name:
            raise ValueError("Unsafe backup path")
        if files[name].stat().st_size != entry["size"] or sha(files[name]) != entry["sha256"]:
            raise ValueError("Backup integrity mismatch")
    return manifest
