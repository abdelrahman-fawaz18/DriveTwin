"""Training-only fitting, causal validation forecasts and pre-held-out artifacts."""

import argparse
import hashlib
import json
import statistics
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.energy.calibration import (  # noqa: E402
    TrainingTrip,
    assumed_plan,
    fit_grid,
    joint_parameter_pairs,
    select_template,
    sensitivity_envelope,
)
from evdt.energy.forecasts import ForecastClock, ForecastPolicy, MissionRequest  # noqa: E402
from evdt.energy.jobs import make_job, run_request  # noqa: E402
from evdt.energy.model import EnergyParameters, estimate_energy  # noqa: E402
from evdt.energy.observations import fingerprint, observation_point  # noqa: E402
from evdt.energy.partitions import MissionArtifact, parse_design, validate_inventory  # noqa: E402


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def auxiliary(packet):
    items = [s for s in packet["samples"] if s["signal_id"] == "auxiliary.power"]
    if len(items) != 1 or items[0]["quality"] != "good" or items[0]["unit"] != "W":
        raise ValueError("Good auxiliary observation required")
    return items[0]["value"]


def summary(rows, targets):
    usable = [r for r in rows if r["status"] == "advisory"]
    baseline = [r for r in rows if r["baseline_status"] == "advisory"]
    n = len(rows)
    result = {
        "missions": n,
        "available": len(usable),
        "failed_or_unavailable": [r["mission_id"] for r in rows if r["status"] != "advisory"],
        "baseline_available": len(baseline),
        "energy_mae_kwh": statistics.mean(abs(r["energy_error_kwh"]) for r in usable)
        if usable
        else None,
        "energy_max_absolute_error_kwh": max(
            (abs(r["energy_error_kwh"]) for r in usable), default=None
        ),
        "soc_mae_percentage_points": statistics.mean(abs(r["soc_error_pp"]) for r in usable)
        if usable
        else None,
        "soc_max_absolute_error_percentage_points": max(
            (abs(r["soc_error_pp"]) for r in usable), default=None
        ),
        "baseline_energy_mae_kwh": statistics.mean(
            abs(r["baseline_energy_error_kwh"]) for r in baseline
        )
        if baseline
        else None,
        "joint_envelope_covered": sum(r["joint_covered"] for r in usable),
        "joint_envelope_coverage": sum(r["joint_covered"] for r in usable) / n if n else 0,
        "maximum_energy_envelope_width_kwh": max(
            (r["energy_width_kwh"] for r in usable), default=None
        ),
        "maximum_soc_envelope_width_pp": max((r["soc_width_pp"] for r in usable), default=None),
        "maximum_fixed_plan_refinement_relative_change": max(
            (r["refinement_relative"] for r in usable), default=None
        ),
    }
    checks = {"all_available": n > 0 and len(usable) == len(baseline) == n}
    if checks["all_available"]:
        checks.update(
            energy_mae=result["energy_mae_kwh"] <= targets["energy_mae_kwh_max"],
            energy_max=result["energy_max_absolute_error_kwh"]
            <= targets["energy_absolute_error_kwh_max"],
            soc_mae=result["soc_mae_percentage_points"] <= targets["soc_mae_percentage_points_max"],
            soc_max=result["soc_max_absolute_error_percentage_points"]
            <= targets["soc_absolute_error_percentage_points_max"],
            baseline_comparison=result["energy_mae_kwh"] <= result["baseline_energy_mae_kwh"],
            coverage=result["joint_envelope_coverage"]
            >= targets["empirical_joint_envelope_coverage_min"],
            energy_width=result["maximum_energy_envelope_width_kwh"]
            <= targets["energy_envelope_width_kwh_max"],
            soc_width=result["maximum_soc_envelope_width_pp"]
            <= targets["soc_envelope_width_percentage_points_max"],
            refinement=result["maximum_fixed_plan_refinement_relative_change"]
            <= targets["fixed_plan_refinement_relative_energy_max"],
        )
    result.update(checks=checks, targets_met=all(checks.values()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uncertainty-selection", type=Path)
    args = parser.parse_args()
    collection = args.collection.resolve()
    output = args.output.resolve()
    if (
        not collection.is_relative_to(ROOT / "evidence/p2")
        or output.exists()
        or not output.is_relative_to(ROOT / "evidence/p2")
    ):
        parser.error("Use the existing private collection and a new private output directory")
    output.mkdir(parents=True)
    protocol_path = ROOT / "qualification/energy/development-protocol.json"
    paths = [
        Path(__file__),
        ROOT / "src/evdt/energy/calibration.py",
        protocol_path,
        *sorted((ROOT / "src/evdt/energy").glob("*.py")),
        collection / "qualification.json",
        collection / "collection/dataset.json",
    ]
    selection_path = args.uncertainty_selection.resolve() if args.uncertainty_selection else None
    if selection_path:
        if selection_path != ROOT / "qualification/energy/uncertainty-selection.json":
            parser.error("Use the reviewed uncertainty-selection policy")
        paths.append(selection_path)
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    report = {
        "passed": False,
        "scope": "Synthetic training/validation; no held-out outcomes or model promotion",
        "held_out_generated_or_scored": False,
        "phase_2_complete": False,
    }
    try:
        qualification = json.loads((collection / "qualification.json").read_bytes())
        if not qualification["passed"] or qualification["held_out_collected"]:
            raise ValueError("Accepted development collection required")
        for name, h in qualification["source_sha256"].items():
            if sha(ROOT / name) != h:
                raise ValueError("Collection source/configuration changed: " + name)
        dataset = json.loads((collection / "collection/dataset.json").read_bytes())
        design = parse_design(
            json.loads((ROOT / "qualification/energy/mission-design.json").read_bytes())
        )
        protocol = json.loads(protocol_path.read_bytes())
        if (
            dataset["protocol_sha256"] != sha(protocol_path)
            or dataset["design_sha256"] != design.design_sha256
        ):
            raise ValueError("Dataset protocol/design mismatch")
        expected = {m.mission_id for m in design.missions if m.partition in ("train", "validation")}
        if {r["mission_id"] for r in dataset["missions"]} != expected:
            raise ValueError("Development inventory incomplete or includes held-out input")
        validate_inventory(
            design,
            tuple(MissionArtifact(**r["artifact"]) for r in dataset["missions"]),
            require_complete=False,
        )
        folders = {
            r["mission_id"]: collection / "collection" / r["mission_id"]
            for r in dataset["missions"]
        }
        # Verify every original collection file before reusing it. Never regenerate
        # accepted data because later estimator-only files were added.
        for row in dataset["missions"]:
            folder = folders[row["mission_id"]]
            if sha(folder / "collection.json") != row["collection_sha256"]:
                raise ValueError("Collection record changed")
            meta = json.loads((folder / "collection.json").read_bytes())
            for name, h in meta["files_sha256"].items():
                if sha(folder / name) != h:
                    raise ValueError("Collected input bytes changed")
        train = []
        for row in dataset["missions"]:
            if row["partition"] != "train":
                continue
            packets = [
                json.loads(line)
                for line in (folders[row["mission_id"]] / "observations.jsonl")
                .read_bytes()
                .splitlines()
            ]
            aux = auxiliary(packets[0])
            if any(auxiliary(p) != aux for p in packets):
                raise ValueError("Initial fit requires constant observed equipment load")
            train.append(
                TrainingTrip(
                    row["mission_id"], "train", tuple(observation_point(p) for p in packets), aux
                )
            )
        base = EnergyParameters(**protocol["estimator_parameters"])
        print("Fitting the bounded grid on 12 complete training trips...", flush=True)
        fit = fit_grid(
            tuple(train),
            base,
            tuple(protocol["fit"]["rolling_candidates"]),
            tuple(protocol["fit"]["efficiency_candidates"]),
            step_s=protocol["fit"]["step_s"],
        )
        template = select_template(tuple(train))
        selection = json.loads(selection_path.read_bytes()) if selection_path else None
        pairs = (
            joint_parameter_pairs(
                fit,
                relative_mse_slack=selection["relative_training_mse_slack"],
                absolute_mse_slack_kwh2=selection["absolute_training_mse_slack_kwh2"],
            )
            if selection
            else None
        )
        uncertainty = {
            **protocol["sensitivity"],
            "joint_parameter_selection": selection,
            "joint_parameter_pairs": pairs,
        }
        if selection:
            uncertainty["version"] = selection["version"]
        model = {
            "version": "synthetic-energy-candidate/1.1.0"
            if selection
            else "synthetic-energy-candidate/1.0.0",
            "model_qualified": False,
            "parameters": asdict(fit.parameters),
            "fit": asdict(fit),
            "template": asdict(template),
            "template_sha256": template.sha256,
            "training_only": True,
            "training_content_sha256": fingerprint(
                [r["artifact"] for r in dataset["missions"] if r["partition"] == "train"]
            ),
            "development_protocol_sha256": sha(protocol_path),
            "source_sha256": before,
            "uncertainty": uncertainty,
        }
        write(output / "model.json", model)
        print(
            "Preparing causal validation jobs from prefixes and the frozen training template...",
            flush=True,
        )
        predictions = []
        for row in dataset["missions"]:
            if row["partition"] != "validation":
                continue
            prefix = []
            with (folders[row["mission_id"]] / "observations.jsonl").open("rb") as stream:
                for raw in stream:
                    packet = json.loads(raw)
                    if packet["sim_time_s"] > protocol["prediction"]["cutoff_sim_s"]:
                        break
                    prefix.append(packet)
            latest = observation_point(prefix[-1])
            packets = tuple(
                p
                for p in prefix
                if p["sim_time_s"] >= latest.sim_time_s - protocol["prediction"]["history_s"]
            )
            remaining = max(0, template.terminal_distance_m - latest.distance_m)
            aux = auxiliary(prefix[-1])
            plan = assumed_plan(
                template,
                current_distance_m=latest.distance_m,
                current_speed_m_s=latest.speed_m_s,
                remaining_distance_m=remaining,
                auxiliary_power_w=aux,
            )
            mission = MissionRequest(
                row["mission_id"] + "-forecast",
                row["mission_id"],
                template.sha256,
                remaining,
                base.declared_capacity_kwh,
                "independent-estimator-capacity/1",
                protocol["prediction"]["reserve_soc"],
                "absent",
            )
            clock = ForecastClock(latest.sim_time_s, "recorded_analysis", "frozen")
            policy = ForecastPolicy("development-prefix/1", 10, 120, 10, 1.5, 5, 1000)
            planned = make_job(
                packets, latest.scope, clock, mission, policy, parameters=fit.parameters, plan=plan
            )
            reference = make_job(packets, latest.scope, clock, mission, policy)
            candidate = run_request(json.dumps(planned).encode())
            baseline = run_request(json.dumps(reference).encode())
            envelope = sensitivity_envelope(
                fit.parameters, plan, latest.soc, protocol["sensitivity"], parameter_pairs=pairs
            )
            refined = estimate_energy(fit.parameters, plan, latest.soc, step_s=0.1)
            ordinary = estimate_energy(fit.parameters, plan, latest.soc, step_s=0.2)
            refinement = abs(refined.final_energy_kwh - ordinary.final_energy_kwh) / max(
                1e-9, abs(ordinary.initial_energy_kwh - ordinary.final_energy_kwh)
            )
            write(output / (row["mission_id"] + "-planned-job.json"), planned)
            write(output / (row["mission_id"] + "-baseline-job.json"), reference)
            predictions.append(
                {
                    "mission_id": row["mission_id"],
                    "cutoff_sim_s": latest.sim_time_s,
                    "cutoff_event_id": latest.event_id,
                    "prefix_sha256": fingerprint(packets),
                    "candidate": candidate,
                    "baseline": baseline,
                    "envelope": asdict(envelope),
                    "refinement_relative": refinement,
                }
            )
        # Persist the complete prediction batch before reading any evaluation
        # truth rows for scoring. No validation future movement is used as a plan.
        write(output / "predictions.json", predictions)
        prediction_sha = sha(output / "predictions.json")
        rows = []
        for prediction in predictions:
            folder = folders[prediction["mission_id"]]
            trace = json.loads((folder / "plant/plant-steps.json").read_bytes())
            start = next(
                r["values"]
                for r in trace
                if abs(r["sim_time_s"] - prediction["cutoff_sim_s"]) < 1e-8
            )
            end = json.loads((folder / "collection.json").read_bytes())["final_truth"]
            actual_energy = (start["battery.pack.energy"] - end["energy_j"]) / 3_600_000
            actual_soc = end["soc"]
            f = prediction["candidate"]["forecast"]
            b = prediction["baseline"]["forecast"]
            e = prediction["envelope"]
            row = {
                "mission_id": prediction["mission_id"],
                "regime": next(
                    m.regime for m in design.missions if m.mission_id == prediction["mission_id"]
                ),
                "status": f["status"],
                "baseline_status": b["status"],
                "truth_net_energy_kwh": actual_energy,
                "truth_final_soc": actual_soc,
                "energy_error_kwh": f["values"]["net_mission_energy_kwh"] - actual_energy
                if f["values"]
                else None,
                "soc_error_pp": 100 * (f["values"]["projected_final_soc"] - actual_soc)
                if f["values"]
                else None,
                "baseline_energy_error_kwh": b["values"]["net_mission_energy_kwh"] - actual_energy
                if b["values"]
                else None,
                "joint_covered": e["minimum_energy_kwh"] <= actual_energy <= e["maximum_energy_kwh"]
                and e["minimum_final_soc"] <= actual_soc <= e["maximum_final_soc"],
                "energy_width_kwh": e["maximum_energy_kwh"] - e["minimum_energy_kwh"],
                "soc_width_pp": 100 * (e["maximum_final_soc"] - e["minimum_final_soc"]),
                "refinement_relative": prediction["refinement_relative"],
            }
            rows.append(row)
        targets = protocol["proposed_D18_targets"]
        metrics = summary(rows, targets)
        write(
            output / "validation.json",
            {
                "rows": rows,
                "summary": metrics,
                "predictions_sha256": prediction_sha,
                "outcome_scope": "Last sampled state in each complete mission; physical truth, not noisy label",
                "probability_guarantee": False,
            },
        )
        # Freeze the proposed advisory error budget without changing it to make a
        # failed validation pass. The later gate must verify all artifact bytes.
        write(
            output / "targets.json",
            {
                "version": "D18-synthetic-energy/1.0.0",
                "status": "frozen_before_held_out",
                "targets": targets,
                "uncertainty_method": uncertainty,
                "validation_targets_met": metrics["targets_met"],
                "automatic_dispatch_approved": False,
            },
        )
        write(
            output / "evaluation-protocol.json",
            {
                "version": "held-out-energy-protocol/1.0.0",
                "partition": "held_out",
                "expected_missions": 6,
                "design_sha256": design.design_sha256,
                "development_protocol_sha256": sha(protocol_path),
                "cutoff_sim_s": 120,
                "history_s": 60,
                "endpoint": "Last sampled state of complete mission",
                "one_independent_case_per_mission": True,
                "template_frozen_sha256": template.sha256,
                "required_model_sha256": sha(output / "model.json"),
                "required_targets_sha256": sha(output / "targets.json"),
                "must_verify_actual_frozen_artifacts": True,
                "validation_must_pass_before_release": True,
                "post_evaluation_tuning_requires_new_independent_evaluation": True,
            },
        )
        report.update(
            passed=True,
            training_missions=len(train),
            validation_missions=len(rows),
            validation=metrics,
            model_parameters=asdict(fit.parameters),
            template_training_mission=template.training_mission_id,
            training_only_fit=True,
            predictions_persisted_before_scoring=True,
            frozen_artifact_sha256={
                n: sha(output / n)
                for n in ("model.json", "targets.json", "evaluation-protocol.json")
            },
        )
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    report["source_sha256"] = before
    if any(sha(ROOT / p) != h for p, h in before.items()):
        report.update(
            passed=False, error="Analysis sources or input dataset changed during the attempt"
        )
    write(output / "qualification.json", report)
    print(
        f"Development analysis completed: {report['passed']}; validation targets met: {report.get('validation', {}).get('targets_met')}",
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
