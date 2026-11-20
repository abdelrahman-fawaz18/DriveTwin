"""Acceptance guard for the reviewed pre-extension shared-code baseline."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def verify_shared():
    baseline = json.loads((ROOT / "qualification/extension/shared-core.json").read_text())
    hashes = {item["path"]: item["sha256"] for item in baseline["files"]}
    changed = [
        name
        for name, digest in hashes.items()
        if hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() != digest
    ]
    if changed:
        raise RuntimeError("Shared code changed after extension baseline: " + ", ".join(changed))
    return {
        "passed": True,
        "baseline_commit": baseline["baseline_commit"],
        "frozen_files": len(hashes),
        "text_normalization": baseline["text_normalization"],
        "sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


if __name__ == "__main__":
    print(json.dumps(verify_shared()))
