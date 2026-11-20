"""Explicit history-only checkpoints; the development queue-backup format stays intact."""

import json
import re
import uuid
from pathlib import PurePosixPath

from evdt.contract_profile import PROFILE, decode
from evdt.recovery.bundle import regular_files, sha

FORMAT = "evdt-history-backup/1.0.0"
ASSETS = [f"bus-{i:03}" for i in range(1, 11)]
BASE = {
    "database.dump",
    "inventory.json",
    "queries.json",
    "counts.json",
    "run-config/run_policy.json",
    "artifacts/contracts/monitoring.profile.json",
}
SOURCE_FILES = (
    "originals.jsonl",
    "source-summary.json",
    "plant/run-manifest.json",
    "plant/plant-steps.json",
)


def required(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("Invalid history checkpoint metadata")
    assets, count = metadata.get("assets"), metadata.get("samples_per_asset")
    if (
        metadata.get("scope") != "database-history-only"
        or metadata.get("protocol") != "history-volume/1.0.0"
        or not isinstance(assets, list)
        or not assets
        or assets != ASSETS[: len(assets)]
        or type(count) is not int
        or not 1 <= count <= 7200
        or not isinstance(metadata.get("source_cluster"), str)
        or not re.fullmatch(r"[0-9]+", metadata["source_cluster"])
        or not isinstance(metadata.get("application_image_id"), str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", metadata["application_image_id"])
    ):
        raise ValueError("Invalid history checkpoint metadata")
    try:
        if str(uuid.UUID(metadata["run_id"])) != metadata["run_id"]:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Invalid history checkpoint recording") from None
    maps = {
        "run-config/" + ("map_context.json" if i == 1 else f"map_context{i}.json")
        for i in range(1, 11)
    }
    return BASE | maps | {f"sources/{asset}/{name}" for asset in assets for name in SOURCE_FILES}


def allowed(name, expected):
    path = PurePosixPath(name)
    safe = (
        not path.is_absolute() and ".." not in path.parts and "\\" not in name and str(path) == name
    )
    artifact = name.startswith(
        ("artifacts/contracts/", "artifacts/catalog/", "artifacts/migrations/monitoring/")
    ) and path.suffix in (".json", ".sql")
    return safe and (name in expected or artifact)


def seal(root, metadata):
    files = regular_files(root)
    expected = required(metadata)
    if (
        "manifest.json" in files
        or not expected <= files.keys()
        or not all(allowed(n, expected) for n in files)
    ):
        raise ValueError("Incomplete, unexpected or already sealed history checkpoint")
    manifest = {
        "format": FORMAT,
        "metadata": metadata,
        "files": {
            name: {"size": p.stat().st_size, "sha256": sha(p)} for name, p in sorted(files.items())
        },
    }
    with (root / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    verify(root, metadata["application_image_id"])
    return manifest


def verify(root, image_id):
    files = regular_files(root)
    if "manifest.json" not in files:
        raise ValueError("Unsealed history checkpoint")
    if files["manifest.json"].stat().st_size > 1024 * 1024:
        raise ValueError("Unbounded history checkpoint manifest")
    manifest = decode(
        files.pop("manifest.json").read_bytes(), {**PROFILE, "max_payload_bytes": 1024 * 1024}
    )
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"format", "metadata", "files"}
        or manifest.get("format") != FORMAT
    ):
        raise ValueError("Unsupported history backup format")
    metadata = manifest["metadata"]
    expected = required(metadata)
    if metadata["application_image_id"] != image_id:
        raise ValueError("Matching history application image required")
    indexed = manifest["files"]
    if (
        not isinstance(indexed, dict)
        or not expected <= indexed.keys()
        or set(indexed) != set(files)
    ):
        raise ValueError("Missing or unexpected history checkpoint files")
    for name, entry in indexed.items():
        if (
            not allowed(name, expected)
            or not isinstance(entry, dict)
            or set(entry) != {"size", "sha256"}
            or type(entry["size"]) is not int
            or entry["size"] < 0
            or not isinstance(entry["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
        ):
            raise ValueError("Unsafe or unapproved history checkpoint path")
        if files[name].stat().st_size != entry["size"] or sha(files[name]) != entry["sha256"]:
            raise ValueError("History checkpoint integrity mismatch")
    return manifest
