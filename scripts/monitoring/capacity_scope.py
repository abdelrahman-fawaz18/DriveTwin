"""Exact successor connection-budget review, preserving earlier guard manifests."""

import hashlib
import json
import subprocess
from pathlib import Path

from workload_scope import verify_registry

ROOT = Path(__file__).resolve().parents[2]


def verify_shared():
    baseline = json.loads((ROOT / "qualification/extension/shared-core.json").read_bytes())
    source = json.loads((ROOT / "qualification/workload/core-changes.json").read_bytes())
    measured = json.loads((ROOT / "qualification/workload/measurement-core.json").read_bytes())
    current = json.loads((ROOT / "qualification/workload/capacity-core.json").read_bytes())
    if (
        set(source) != {"src/evdt/monitoring/run_policy.py"}
        or set(measured)
        != {
            "apps/dashboard/src/main.tsx",
            "src/evdt/monitoring/api.py",
            "src/evdt/monitoring/ingest.py",
            "src/evdt/monitoring/transport.py",
            "src/evdt/monitoring/repository.py",
        }
        or set(current)
        != {"src/evdt/monitoring/repository.py", "src/evdt/monitoring/registration_client.py"}
    ):
        raise RuntimeError("Unexpected connection-budget core review set")
    for name, review in current.items():
        original = next(item["sha256"] for item in baseline["files"] if item["path"] == name)
        predecessor = measured[name]["reviewed_sha256"] if name in measured else original
        if (
            review["previous_reviewed_sha256"] != predecessor
            or review["baseline_sha256"] != original
        ):
            raise RuntimeError("Connection-budget review is not bound to the preceding bytes")
    changes = {**source, **measured, **current}
    for item in baseline["files"]:
        name = item["path"]
        raw = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        if name == "contracts/monitoring.profile.json":
            old = subprocess.check_output(
                ["git", "show", baseline["baseline_commit"] + ":" + name], cwd=ROOT
            )
            if hashlib.sha256(old.replace(b"\r\n", b"\n")).hexdigest() != item["sha256"]:
                raise RuntimeError("Historical registry object changed")
            verify_registry(json.loads(old), json.loads(raw))
        elif name in changes:
            if (
                changes[name]["baseline_sha256"] != item["sha256"]
                or hashlib.sha256(raw).hexdigest() != changes[name]["reviewed_sha256"]
            ):
                raise RuntimeError("Reviewed connection-budget core differs: " + name)
        elif hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise RuntimeError("Unreviewed core change: " + name)
    return {
        "passed": True,
        "baseline_commit": baseline["baseline_commit"],
        "unchanged_files": len(baseline["files"]) - 1 - len(changes),
        "reviewed_core_changes": changes,
        "profile_change": "five exact E2-A additions only",
        "successor_review": "ADR-0021 bounded database admission; historical manifests retained",
    }
