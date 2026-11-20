"""Reconcile contract parity, frozen-reader compatibility, and service regressions."""

import argparse
import base64
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    folder, output = args.evidence_dir.resolve(), args.report.resolve()
    if output.exists() or any(not p.is_relative_to(ROOT / "evidence/p0") for p in (folder, output)):
        parser.error("Use project-local evidence and a new acceptance filename")
    paths = {
        "contracts": folder / "qualification.json",
        "dataflow": ROOT / "evidence/p0/2026-11-03-p0-02-acceptance-p004.json",
        "browser": ROOT / "evidence/p0/2026-11-03-p0-03-acceptance-p004.json",
    }
    records = {name: json.loads(path.read_text()) for name, path in paths.items()}
    sources = {}
    for record in records.values():
        if not record["passed"]:
            raise RuntimeError("A prerequisite failed")
        for name, expected in record["source_sha256"].items():
            if digest(ROOT / name) != expected:
                raise RuntimeError(f"Source changed since qualification: {name}")
            sources[name] = expected
    result = records["contracts"]
    if result["discrepancies"]:
        raise RuntimeError("Contract discrepancies remain")
    cases = json.loads((folder / "cases.json").read_text())
    py = json.loads((folder / "python.json").read_text())
    ts = json.loads((folder / "typescript.json").read_text())
    if len(cases) != result["case_count"] or len(py) != len(cases) or len(ts) != len(cases):
        raise RuntimeError("Corpus result count mismatch")
    compatibility = []
    for case, python, typescript in zip(cases, py, ts, strict=True):
        expected = {"id": case["id"], **case["expected"]}
        for actual in (python, typescript):
            if {key: actual[key] for key in expected} != expected:
                raise RuntimeError(f"Golden result mismatch: {case['id']}")
        if case.get("downgrade"):
            transformed = json.loads(base64.b64decode(case["raw_base64"]))
            transformed.pop("annotations", None)
            transformed["schema_version"] = "1.0.0"
            if python["downgrade"] != transformed or typescript["downgrade"] != transformed:
                raise RuntimeError("Adapter altered a required field")
            if (
                not python["legacy_typescript_adapter_accepted"]
                or python["legacy_accepted"] != case["legacy_expected"]
            ):
                raise RuntimeError("Frozen-reader expectation mismatch")
            compatibility.append(
                {
                    "id": case["id"],
                    "legacy_direct": python["legacy_accepted"],
                    "both_adapters_read_by_frozen_consumer": True,
                    "all_non_annotation_fields_preserved": True,
                }
            )
    original = json.loads(
        (ROOT / "evidence/p0/2026-11-03-dataflow-qualification-final.json").read_text()
    )
    if (
        digest(ROOT / "qualification/contracts/legacy_contract_1_0.py")
        != original["source_sha256"]["src/evdt/contracts.py"]
    ):
        raise RuntimeError("Legacy reader is not the original qualified source")
    if (
        digest(ROOT / "contracts/telemetry/1.0.0.schema.json")
        != original["source_sha256"]["contracts/telemetry/1.0.0.schema.json"]
    ):
        raise RuntimeError("Historical schema was modified")
    compiled = ROOT / "packages/contracts-ts/.build/index.js"
    if digest(compiled) != result["compiled_sha256"]:
        raise RuntimeError("Compiled validator changed")
    for name in ("index.js", "index.d.ts"):
        destination = folder / (
            "typescript-validator" + (".d.ts" if name.endswith(".d.ts") else ".js")
        )
        if destination.exists():
            raise RuntimeError("Compiled export already exists; preserve prior evidence")
        shutil.copyfile(compiled.parent / name, destination)
    sources["scripts/platform/accept_contracts.py"] = digest(Path(__file__).resolve())
    artifact_paths = [
        *paths.values(),
        folder / "cases.json",
        folder / "python.json",
        folder / "typescript.json",
        folder / "typescript-validator.js",
        folder / "typescript-validator.d.ts",
    ]
    accepted = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "gate": "P0-04",
        "passed": True,
        "status": "passed",
        "profile": "p0-portable/1.0.0",
        "schema_versions": ["1.0.0", "1.1.0"],
        "case_count": result["case_count"],
        "accepted_cases": result["accepted_cases"],
        "rejected_cases": result["rejected_cases"],
        "discrepancies": [],
        "compatibility": compatibility,
        "packages": result["packages"],
        "python_context": result["python_context"],
        "node": result["node"],
        "historical_reader_and_schema_unmodified": True,
        "source_sha256": sources,
        "artifacts": [
            {"path": str(p.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(p)}
            for p in artifact_paths
        ],
        "scope_limits": [
            "Qualification-only scalar, good-quality synthetic telemetry; not all future message types or modes.",
            "Portable profile explicitly narrows the older schema's broad numeric/timestamp/encoding acceptance.",
            "Legacy 1.0 reader requires explicit annotation-only adaptation for 1.1; no direct forward-compatibility claim.",
            "Parser/validation agreement on declared golden vectors, not a formal proof over all possible inputs.",
            "No automatic history rewrite, TypeScript/Python hash equivalence, production schema registry, VSS certification, or complete Phase 0 claim.",
        ],
    }
    with output.open("x", encoding="utf-8") as file:
        json.dump(accepted, file, indent=2)
        file.write("\n")
    print(
        json.dumps(
            {
                "gate": "P0-04",
                "passed": True,
                "case_count": result["case_count"],
                "source_files_verified": len(sources),
                "artifacts_verified": len(artifact_paths),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
