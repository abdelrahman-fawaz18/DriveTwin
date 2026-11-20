"""Prepare held-out jobs or score sealed worker outputs with frozen criteria."""

import argparse
import json
import statistics
from pathlib import Path

from analyze_energy_development import summary
from energy_holdout import (
    ROOT,
    prepare_case,
    sha,
    validation_parity,
    verify_claim,
    verify_release,
    write,
)

from evdt.energy.observations import fingerprint


def verify_collection(folder, release):
    dataset = json.loads((folder / "dataset.json").read_bytes())
    if not dataset["passed"] or dataset["release_sha256"] != release["manifest_sha256"]:
        raise ValueError("Accepted held-out collection required")
    expected = {m.mission_id for m in release["design"].missions if m.partition == "held_out"}
    if len(dataset["missions"]) != 6 or {r["mission_id"] for r in dataset["missions"]} != expected:
        raise ValueError("Incomplete or wrong held-out inventory")
    for row in dataset["missions"]:
        base = folder / row["mission_id"]
        if sha(base / "collection.json") != row["collection_sha256"]:
            raise ValueError("Held-out collection record changed")
        record = json.loads((base / "collection.json").read_bytes())
        for name, expected_sha in record["files_sha256"].items():
            if sha(base / name) != expected_sha:
                raise ValueError("Held-out source bytes changed")
    return dataset


def prepare(root, release):
    output = root / "prepared"
    if output.exists():
        raise ValueError("Prepared outputs already exist; preserve the original attempt")
    dataset = verify_collection(root / "collection", release)
    output.mkdir()
    records = []
    for row in dataset["missions"]:
        prefix = []
        with (root / "collection" / row["mission_id"] / "observations.jsonl").open("rb") as stream:
            for raw in stream:
                packet = json.loads(raw)
                if packet["sim_time_s"] > release["protocol"]["cutoff_sim_s"]:
                    break
                prefix.append(packet)
        prepared = prepare_case(
            release["model"], release["development"], release["protocol"], prefix, row["mission_id"]
        )
        jobs = {}
        for method in ("planned", "baseline"):
            name = row["mission_id"] + "-" + method + "-job.json"
            write(output / name, prepared.pop(method))
            jobs[method] = {
                "filename": name,
                "file_sha256": sha(output / name),
                "normalized_job_sha256": fingerprint(json.loads((output / name).read_bytes())),
            }
        records.append({**prepared, "jobs": jobs})
    write(
        output / "cases.json",
        {
            "version": "held-out-preparation/1.0.0",
            "release_sha256": release["manifest_sha256"],
            "truth_scoring_started": False,
            "cases": records,
            "files_sha256": {p.name: sha(p) for p in output.iterdir() if p.is_file()},
        },
    )
    return {"prepared_cases": len(records), "prepared_cases_sha256": sha(output / "cases.json")}


def score(root, release):
    output = root / "evaluation.json"
    if output.exists():
        raise ValueError("This attempt was already scored; do not overwrite or retune")
    dataset = verify_collection(root / "collection", release)
    prepared = json.loads((root / "prepared/cases.json").read_bytes())
    batch = json.loads((root / "worker-batch.json").read_bytes())
    if (
        not batch["completed"]
        or batch["cases"] != 12
        or batch["prepared_cases_sha256"] != sha(root / "prepared/cases.json")
        or prepared["release_sha256"] != release["manifest_sha256"]
    ):
        raise ValueError("Complete sealed predictions must precede outcome scoring")
    for name, expected in prepared["files_sha256"].items():
        if sha(root / "prepared" / name) != expected:
            raise ValueError("Prepared job bytes changed")
    for name, expected in batch["files_sha256"].items():
        if sha(root / "worker-results" / name) != expected:
            raise ValueError("Worker prediction bytes changed")
    records = []
    for case in prepared["cases"]:
        base = root / "collection" / case["mission_id"]
        results = {}
        for method in ("planned", "baseline"):
            receipt = json.loads(
                (
                    root / "worker-results" / (case["mission_id"] + "-" + method + ".json")
                ).read_bytes()
            )
            if receipt["exit_code"] != 0 or receipt["controller_exit_code"] != 0:
                raise ValueError(
                    "A worker failed; retain the failure instead of scoring fabricated values"
                )
            prediction = receipt["result"]
            if prediction["job_sha256"] != case["jobs"][method]["normalized_job_sha256"]:
                raise ValueError("Worker result does not belong to prepared job")
            results[method] = prediction["forecast"]
        # Outcomes become scoring inputs only after the complete prediction batch.
        trace = json.loads((base / "plant/plant-steps.json").read_bytes())
        start = next(
            r["values"] for r in trace if abs(r["sim_time_s"] - case["cutoff_sim_s"]) < 1e-8
        )
        end = json.loads((base / "collection.json").read_bytes())["final_truth"]
        energy = (start["battery.pack.energy"] - end["energy_j"]) / 3_600_000
        soc = end["soc"]
        candidate, baseline = results["planned"], results["baseline"]
        c, b = candidate["values"], baseline["values"]
        e = case["envelope"]
        records.append(
            {
                "mission_id": case["mission_id"],
                "regime": next(
                    m.regime
                    for m in release["design"].missions
                    if m.mission_id == case["mission_id"]
                ),
                "status": candidate["status"],
                "baseline_status": baseline["status"],
                "truth_net_energy_kwh": energy,
                "truth_final_soc": soc,
                "energy_error_kwh": c["net_mission_energy_kwh"] - energy if c else None,
                "soc_error_pp": 100 * (c["projected_final_soc"] - soc) if c else None,
                "baseline_energy_error_kwh": b["net_mission_energy_kwh"] - energy if b else None,
                "baseline_soc_error_pp": 100 * (b["projected_final_soc"] - soc) if b else None,
                "joint_covered": e["minimum_energy_kwh"] <= energy <= e["maximum_energy_kwh"]
                and e["minimum_final_soc"] <= soc <= e["maximum_final_soc"],
                "energy_width_kwh": e["maximum_energy_kwh"] - e["minimum_energy_kwh"],
                "soc_width_pp": 100 * (e["maximum_final_soc"] - e["minimum_final_soc"]),
                "refinement_relative": case["refinement_relative"],
            }
        )
    if len(records) != release["protocol"]["expected_missions"] or {
        r["mission_id"] for r in records
    } != {r["mission_id"] for r in dataset["missions"]}:
        raise ValueError("Independent evaluation case count mismatch")
    metrics = summary(records, release["targets"])
    baseline = [r for r in records if r["baseline_status"] == "advisory"]
    metrics["baseline_soc_mae_percentage_points"] = (
        statistics.mean(abs(r["baseline_soc_error_pp"]) for r in baseline) if baseline else None
    )
    metrics["baseline_soc_max_absolute_error_percentage_points"] = max(
        (abs(r["baseline_soc_error_pp"]) for r in baseline), default=None
    )
    record = {
        "version": "held-out-energy-evaluation/1.0.0",
        "completed": True,
        "held_out_criteria_met": metrics["targets_met"],
        "release_sha256": release["manifest_sha256"],
        "model_targets_protocol_unchanged": True,
        "model_promotion": "pending_P2_E1",
        "automatic_dispatch_approved": False,
        "population_coverage_guarantee": False,
        "predictions_sealed_before_outcome_scoring": True,
        "summary": metrics,
        "rows": records,
        "prepared_cases_sha256": sha(root / "prepared/cases.json"),
        "worker_batch_sha256": sha(root / "worker-batch.json"),
    }
    write(output, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["parity", "prepare", "score"])
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_dir.resolve()
    if not root.is_relative_to(ROOT / "evidence/p2"):
        parser.error("Use the owned private evidence directory")
    release = verify_release()
    if args.action == "parity":
        path = root / "validation-parity.json"
        if path.exists():
            raise ValueError("Preserve the existing parity result")
        cases = validation_parity(release)
        write(path, {"passed": True, "cases": cases})
        result = {"validation_cases": len(cases)}
    else:
        verify_claim(ROOT, release["manifest_sha256"], root)
        result = prepare(root, release) if args.action == "prepare" else score(root, release)
    verify_release()
    print(
        json.dumps(
            {
                "stage": args.action,
                "completed": True,
                "held_out_criteria_met": result.get("held_out_criteria_met"),
                "cases": result.get(
                    "validation_cases",
                    result.get("prepared_cases", result.get("summary", {}).get("missions")),
                ),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
