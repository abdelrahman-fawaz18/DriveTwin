"""Layer exact reviewed runtime changes without rewriting historical proof."""

import hashlib
import json
import subprocess
from pathlib import Path

from workload_scope import verify_registry

ROOT = Path(__file__).resolve().parents[2]
LAYERS = (
    (
        "core-changes.json",
        {"src/evdt/monitoring/run_policy.py"},
        "2f6b0646898c50ef9af7f74a5743234fb53781fca4f243116e43b5b4d4fb0b28",
    ),
    (
        "measurement-core.json",
        {
            "apps/dashboard/src/main.tsx",
            "src/evdt/monitoring/api.py",
            "src/evdt/monitoring/ingest.py",
            "src/evdt/monitoring/transport.py",
            "src/evdt/monitoring/repository.py",
        },
        "d27c27199eafe3ea9f1d35f0bf9aff9ca8766ac03239e2351a892e478d21fc90",
    ),
    (
        "capacity-core.json",
        {
            "src/evdt/monitoring/repository.py",
            "src/evdt/monitoring/registration_client.py",
        },
        "a683a90099e9aff2c0a67697996cc41bf8646df9fcb271a7f93d9060dc479863",
    ),
    (
        "catalog-view-core.json",
        {"src/evdt/monitoring/asset_catalog.py"},
        "e5ecec58daf9f77253fedd6a6a157d893178736e56524108f679d003697c875f",
    ),
    (
        "history-backend-core.json",
        {"src/evdt/monitoring/api.py"},
        "813e1ad2a6340c9deed136c5459dd059ac07245ae9360709150ad402e1deb8d6",
    ),
    (
        "history-ui-core.json",
        {"apps/dashboard/src/HistoryExplorer.tsx", "apps/dashboard/src/Trend.tsx"},
        "f51593709f59df6280ea967610700e4b90424991c25c84e791242032be978dcb",
    ),
)


def verify_shared():
    baseline = json.loads((ROOT / "qualification/extension/shared-core.json").read_bytes())
    original = {item["path"]: item["sha256"] for item in baseline["files"]}
    changes = {}
    layers = list(LAYERS)
    if (ROOT / "qualification/workload/presentation-core.json").is_file():
        layers.append(
            (
                "presentation-core.json",
                {"apps/dashboard/src/model.ts"},
                None,
            )
        )
    if (ROOT / "qualification/workload/fleet-ui-core.json").is_file():
        layers.append(
            (
                "fleet-ui-core.json",
                {"apps/dashboard/src/main.tsx", "apps/dashboard/src/style.css"},
                None,
            )
        )
    if (ROOT / "qualification/workload/queue-capacity-core.json").is_file():
        layers.append(
            (
                "queue-capacity-core.json",
                {"src/evdt/monitoring/spool.py"},
                "fa5bb0b9f5792a5409c1c03b73ef46352a0baa3e135dd317d9d222a80be97d18",
            )
        )
    if (ROOT / "qualification/workload/queue-count-core.json").is_file():
        layers.append(
            (
                "queue-count-core.json",
                {"src/evdt/monitoring/spool.py"},
                "6a266e7c927617cdd163ab2e35dee445e1dffec98b405851b90ff536dd5452dc",
            )
        )
    if (ROOT / "qualification/workload/storage-transport-core.json").is_file():
        layers.append(
            (
                "storage-transport-core.json",
                {
                    "src/evdt/monitoring/transport.py",
                    "src/evdt/monitoring/metrics.py",
                    "src/evdt/monitoring/analysis_views.py",
                    "apps/dashboard/src/Operations.tsx",
                },
                "fab05efe1c5a520a0eafd7b8057ff90a1919b76815d4588cb3e357da35c7742b",
            )
        )
    if (ROOT / "qualification/workload/broker-ack-core.json").is_file():
        layers.append(
            (
                "broker-ack-core.json",
                {"src/evdt/monitoring/transport.py"},
                None,
            )
        )
    for filename, expected, frozen_hash in layers:
        raw = (ROOT / "qualification/workload" / filename).read_bytes()
        if frozen_hash and hashlib.sha256(raw).hexdigest() != frozen_hash:
            raise RuntimeError("Historical review manifest changed: " + filename)
        layer = json.loads(raw)
        if set(layer) != expected:
            raise RuntimeError("Unexpected runtime review set: " + filename)
        for name, review in layer.items():
            predecessor = changes[name]["reviewed_sha256"] if name in changes else original[name]
            if review["baseline_sha256"] != original[name] or (
                "previous_reviewed_sha256" in review
                and review["previous_reviewed_sha256"] != predecessor
            ):
                raise RuntimeError("Runtime review predecessor changed: " + name)
            if (
                filename
                in (
                    "capacity-core.json",
                    "catalog-view-core.json",
                    "history-backend-core.json",
                    "history-ui-core.json",
                    "presentation-core.json",
                    "fleet-ui-core.json",
                    "queue-capacity-core.json",
                    "queue-count-core.json",
                    "storage-transport-core.json",
                    "broker-ack-core.json",
                )
                and "previous_reviewed_sha256" not in review
            ):
                raise RuntimeError("Runtime review must identify its predecessor: " + name)
            changes[name] = review
    for name, previous in original.items():
        raw = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        if name == "contracts/monitoring.profile.json":
            old = subprocess.check_output(
                ["git", "show", baseline["baseline_commit"] + ":" + name], cwd=ROOT
            )
            if hashlib.sha256(old.replace(b"\r\n", b"\n")).hexdigest() != previous:
                raise RuntimeError("Historical registry object changed")
            verify_registry(json.loads(old), json.loads(raw))
        elif hashlib.sha256(raw).hexdigest() != changes.get(name, {}).get(
            "reviewed_sha256", previous
        ):
            raise RuntimeError("Unreviewed runtime bytes: " + name)
    return {
        "passed": True,
        "baseline_commit": baseline["baseline_commit"],
        "unchanged_files": len(original) - 1 - len(changes),
        "reviewed_core_changes": changes,
        "profile_change": "five exact E2-A additions only",
        "successor_review": "Exact catalog/history runtime review layers; historical manifests retained",
    }
