"""Collect the six reserved missions only after verified frozen release/claim."""

import argparse
import json
from pathlib import Path

from collect_energy_missions import collect
from energy_holdout import ROOT, sha, verify_claim, verify_release, write

from evdt.energy.partitions import MissionArtifact, validate_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    args = parser.parse_args()
    release = verify_release()
    if release["manifest_sha256"] != args.release_sha:
        parser.error("Release fingerprint mismatch")
    output = args.output.resolve()
    verify_claim(ROOT, args.release_sha, output.parent)
    if output.exists():
        parser.error("Held-out collection refuses an existing output directory")
    output.mkdir()
    selected = [m for m in release["design"].missions if m.partition == "held_out"]
    if len(selected) != release["protocol"]["expected_missions"]:
        raise ValueError("Frozen held-out mission count mismatch")
    report = {
        "passed": False,
        "release_sha256": args.release_sha,
        "scope": "Reserved complete missions, one bus at a time; not live capacity proof",
        "missions": [],
    }
    try:
        for i, mission in enumerate(selected, 1):
            result = collect(
                mission, output / mission.mission_id, args.osm, release["development"]["collection"]
            )
            report["missions"].append(
                {
                    "mission_id": mission.mission_id,
                    "partition": "held_out",
                    "artifact": result["artifact"],
                    "collection_sha256": sha(output / mission.mission_id / "collection.json"),
                }
            )
            write(
                output / "progress.json",
                {"completed": i, "total": len(selected), "latest": mission.mission_id},
            )
            print(
                f"Collected held-out mission {i}/{len(selected)}: {mission.mission_id}", flush=True
            )
        q = json.loads((release["analysis"] / "qualification.json").read_bytes())
        dataset_paths = [
            ROOT / p for p in q["source_sha256"] if p.endswith("/collection/dataset.json")
        ]
        if len(dataset_paths) != 1:
            raise ValueError("Expected one sealed development inventory")
        development = json.loads(dataset_paths[0].read_bytes())
        combined = tuple(
            MissionArtifact(**r["artifact"])
            for r in [*development["missions"], *report["missions"]]
        )
        validate_inventory(release["design"], combined, require_complete=True)
        report.update(
            passed=True,
            held_out_missions=len(selected),
            combined_design_missions=len(combined),
            cross_partition_copy_checks_passed=True,
        )
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    write(output / "dataset.json", report)
    print(f"Held-out collection passed: {report['passed']}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
