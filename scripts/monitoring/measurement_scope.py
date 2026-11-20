"""Exact reviewed instrumentation changes; retain historical D2/E2-A guards."""

import hashlib
import json
import subprocess
from pathlib import Path

from workload_scope import verify_registry

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "apps/dashboard/src/main.tsx",
    "src/evdt/monitoring/api.py",
    "src/evdt/monitoring/ingest.py",
    "src/evdt/monitoring/transport.py",
    "src/evdt/monitoring/repository.py",
}


def verify_shared():
    baseline = json.loads((ROOT / "qualification/extension/shared-core.json").read_bytes())
    previous = json.loads((ROOT / "qualification/workload/core-changes.json").read_bytes())
    current = json.loads((ROOT / "qualification/workload/measurement-core.json").read_bytes())
    if set(current) != EXPECTED or set(previous) != {"src/evdt/monitoring/run_policy.py"}:
        raise RuntimeError("Unexpected measurement core exception set")
    changes = {**previous, **current}
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
                raise RuntimeError("Reviewed measurement core differs: " + name)
        elif hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise RuntimeError("Unreviewed core change: " + name)
    return {
        "passed": True,
        "baseline_commit": baseline["baseline_commit"],
        "unchanged_files": len(baseline["files"]) - 1 - len(changes),
        "reviewed_core_changes": changes,
        "profile_change": "five exact E2-A additions only",
    }
