"""Check an explicitly additive registry extension against the historical shared core."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDITIONS = {
    "schemas": {"3.2.0": "telemetry/3.2.0.schema.json"},
    "catalogs": {"1.4.0": "catalog/monitoring-fleet-1.4.0.json"},
    "catalog_schemas": {"1.4.0": "monitoring.catalog-1.4.schema.json"},
    "operational_formats": {
        "1.4.0": {"schema_version": "3.2.0", "manifest_version": "source-run/2.2.0"}
    },
    "manifest_schemas": {"source-run/2.2.0": "run-manifest-2.2.schema.json"},
}


def verify_registry(original, current):
    current = json.loads(json.dumps(current))
    for section, entries in ADDITIONS.items():
        for key, value in entries.items():
            if key in original[section] or current[section].pop(key, None) != value:
                raise ValueError("Unexpected workload registry entry")
    if current != original:
        raise ValueError("Historical registry values changed")


def verify_shared():
    baseline = json.loads((ROOT / "qualification/extension/shared-core.json").read_bytes())
    changes = json.loads((ROOT / "qualification/workload/core-changes.json").read_bytes())
    if set(changes) != {"src/evdt/monitoring/run_policy.py"}:
        raise RuntimeError("Unexpected shared-core exception set")
    for item in baseline["files"]:
        name = item["path"]
        raw = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        if name == "contracts/monitoring.profile.json":
            old = subprocess.check_output(
                ["git", "show", baseline["baseline_commit"] + ":" + name], cwd=ROOT
            )
            if hashlib.sha256(old.replace(b"\r\n", b"\n")).hexdigest() != item["sha256"]:
                raise RuntimeError("Historical profile object does not match the stored baseline")
            verify_registry(json.loads(old), json.loads(raw))
        elif name in changes:
            change = changes[name]
            if (
                change["baseline_sha256"] != item["sha256"]
                or hashlib.sha256(raw).hexdigest() != change["reviewed_sha256"]
            ):
                raise RuntimeError("Reviewed workload core change differs: " + name)
        elif hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise RuntimeError("Existing shared core changed: " + name)
    return {
        "passed": True,
        "baseline_commit": baseline["baseline_commit"],
        "unchanged_files": len(baseline["files"]) - 1 - len(changes),
        "reviewed_core_changes": changes,
        "profile_change": "only five reviewed new format-registry entries; all prior values preserved",
    }


if __name__ == "__main__":
    print(json.dumps(verify_shared()))
