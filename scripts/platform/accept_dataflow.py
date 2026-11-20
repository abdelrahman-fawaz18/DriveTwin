"""Reconcile a new data-flow run with unchanged engine/SUMO prerequisites."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataflow-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--sumo-report",
        type=Path,
        default=ROOT / "evidence/p0/2026-11-03-sumo-verification-02/qualification.json",
    )
    args = parser.parse_args()
    output = args.report.resolve()
    paths = {
        "engine": ROOT / "evidence/p0/2026-11-03-native-engine-prerequisite-acceptance.json",
        "dataflow": args.dataflow_report.resolve(),
        "sumo": args.sumo_report.resolve(),
    }
    if output.exists() or any(
        not p.is_relative_to(ROOT / "evidence/p0") for p in [output, *paths.values()]
    ):
        parser.error("Use project-local evidence and a new acceptance filename")
    records = {name: json.loads(path.read_text()) for name, path in paths.items()}
    engine, dataflow, sumo = (records[name] for name in ("engine", "dataflow", "sumo"))
    if (
        not all(cycle["passed"] for cycle in engine["cycles"])
        or len(engine["cycles"]) != 2
        or not engine["test_volume_removed"]
    ):
        raise RuntimeError("Engine prerequisite not passed")
    if not dataflow["passed"] or len(dataflow["cycles"]) != 2 or any(dataflow["cleanup"].values()):
        raise RuntimeError("Data-flow/cleanup not passed")
    if not dataflow["cycles"][1]["persisted_after_recreate"]["passed"]:
        raise RuntimeError("Persistence not verified")
    if (
        not sumo["passed"]
        or len(sumo["probe"]["runs"]) != 2
        or sumo["cleanup"]["owned_containers_remaining"]
    ):
        raise RuntimeError("SUMO prerequisite not passed")
    if len({r["trajectory_sha256"] for r in sumo["probe"]["runs"]}) != 1:
        raise RuntimeError("SUMO repeatability not passed")
    if len({records[name]["engine"]["ID"] for name in records}) != 1:
        raise RuntimeError("Prerequisites refer to different engines")
    sources = {}
    for report in records.values():
        for name, expected in report["source_sha256"].items():
            actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError(f"Previously qualified prerequisite changed: {name}")
            sources[name] = expected
    sources["scripts/platform/accept_dataflow.py"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    record = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "gate": "P0-02",
        "passed": True,
        "status": "passed/requalified",
        "scope": "Updated receiver; engine and movement code unchanged and fingerprints verified",
        "source_sha256": sources,
        "evidence": [
            {
                "role": name,
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in paths.items()
        ],
        "scope_limits": [
            "Separate engine/data-flow/SUMO fixtures, not a complete bus application.",
            "No full Windows/WSL reboot, operational security, command-loop, or workload target claim.",
        ],
    }
    with output.open("x") as file:
        json.dump(record, file, indent=2)
        file.write("\n")
    print(
        json.dumps({"gate": record["gate"], "passed": True, "source_files_verified": len(sources)})
    )


if __name__ == "__main__":
    main()
