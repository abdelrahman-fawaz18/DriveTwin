"""Frozen-release gates and causal preparation for the one-time held-out study."""

import hashlib
import json
import math
import sys
from dataclasses import asdict

from energy_runtime import ROOT

sys.path.insert(0, str(ROOT / "src"))
from evdt.energy.calibration import (
    MotionTemplate,
    TemplatePoint,
    assumed_plan,
    sensitivity_envelope,
)  # noqa: E402
from evdt.energy.forecasts import ForecastClock, ForecastPolicy, MissionRequest  # noqa: E402
from evdt.energy.jobs import make_job  # noqa: E402
from evdt.energy.model import EnergyParameters, estimate_energy  # noqa: E402
from evdt.energy.observations import fingerprint, observation_point  # noqa: E402
from evdt.energy.partitions import authorize_partition, parse_design  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def child(root, relative):
    value = (root / relative).resolve()
    if not value.is_relative_to(root.resolve()):
        raise ValueError("Release path escapes the canonical project")
    return value


def verify_release(root=ROOT):
    manifest_path = root / "docs/models/energy-validation-release.json"
    manifest = json.loads(manifest_path.read_bytes())
    if (
        manifest["version"] != "energy-validation-release/1.0.0"
        or not manifest["held_out_release_eligible"]
        or not manifest["validation_targets_met"]
        or not manifest["worker_outputs_match"]
        or manifest["held_out_generated_or_scored"]
    ):
        raise ValueError("The reviewed validation release is not eligible")
    analysis = child(root, manifest["analysis_path"])
    if not analysis.is_relative_to(root / "evidence/p2"):
        raise ValueError("Frozen analysis must be in private project evidence")
    for name, expected in manifest["source_sha256"].items():
        if sha(child(root, name)) != expected:
            raise ValueError("Frozen release source mismatch: " + name)
    for name, expected in manifest["analysis_artifact_sha256"].items():
        path = child(analysis, name)
        if path.parent != analysis or sha(path) != expected:
            raise ValueError("Sealed analysis artifact mismatch: " + name)
    frozen = {"model.json", "targets.json", "evaluation-protocol.json"}
    if set(manifest["frozen_artifact_sha256"]) != frozen:
        raise ValueError("Unexpected frozen artifact inventory")
    for name, expected in manifest["frozen_artifact_sha256"].items():
        if sha(analysis / name) != expected:
            raise ValueError("Frozen artifact mismatch: " + name)
    qualification = json.loads((analysis / "qualification.json").read_bytes())
    if not qualification["passed"] or not qualification["validation"]["targets_met"]:
        raise ValueError("Successful actual validation is required")
    for name, expected in qualification["source_sha256"].items():
        if sha(child(root, name)) != expected:
            raise ValueError("Actual analysis source/input mismatch: " + name)
    model = json.loads((analysis / "model.json").read_bytes())
    targets = json.loads((analysis / "targets.json").read_bytes())
    protocol = json.loads((analysis / "evaluation-protocol.json").read_bytes())
    development = json.loads((root / "qualification/energy/development-protocol.json").read_bytes())
    design = parse_design(
        json.loads((root / "qualification/energy/mission-design.json").read_bytes())
    )
    if (
        not model["training_only"]
        or model["model_qualified"]
        or manifest["model_qualified"]
        or model["version"] != "synthetic-energy-candidate/1.1.0"
        or targets["version"] != "D18-synthetic-energy/1.0.0"
        or protocol["version"] != "held-out-energy-protocol/1.0.0"
        or not targets["validation_targets_met"]
        or targets["automatic_dispatch_approved"]
        or targets["targets"] != development["proposed_D18_targets"]
        or protocol["required_model_sha256"] != sha(analysis / "model.json")
        or protocol["required_targets_sha256"] != sha(analysis / "targets.json")
        or protocol["design_sha256"] != design.design_sha256
        or protocol["development_protocol_sha256"]
        != sha(root / "qualification/energy/development-protocol.json")
        or protocol["partition"] != "held_out"
        or protocol["expected_missions"] != 6
        or protocol["cutoff_sim_s"] != 120
        or protocol["history_s"] != 60
    ):
        raise ValueError("Frozen study design, targets or authority mismatch")
    train = {m.mission_id for m in design.missions if m.partition == "train"}
    if (
        set(model["fit"]["training_ids"]) != train
        or model["template"]["training_mission_id"] not in train
    ):
        raise ValueError("Fit/template provenance is not training-only")
    authorize_partition(
        "held_out",
        "evaluate",
        calibration_sha256=sha(analysis / "model.json"),
        targets_sha256=sha(analysis / "targets.json"),
        protocol_sha256=sha(analysis / "evaluation-protocol.json"),
    )
    return {
        "manifest": manifest,
        "manifest_sha256": sha(manifest_path),
        "analysis": analysis,
        "model": model,
        "targets": targets["targets"],
        "protocol": protocol,
        "development": development,
        "design": design,
    }


def claim_path(root, release_sha):
    if len(release_sha) != 64 or any(c not in "0123456789abcdef" for c in release_sha):
        raise ValueError("Invalid release fingerprint")
    return root / ".runtime/p2-held-out" / (release_sha + ".json")


def claim_once(root, release_sha, output):
    output = output.resolve()
    if not output.is_relative_to(root / "evidence/p2"):
        raise ValueError("Held-out evidence must remain private and canonical")
    path = claim_path(root, release_sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "version": "held-out-attempt/1.0.0",
                "release_sha256": release_sha,
                "evidence_root": output.relative_to(root).as_posix(),
                "state": "reserved_before_collection",
            },
            stream,
            indent=2,
        )
        stream.write("\n")
    return path


def verify_claim(root, release_sha, output):
    value = json.loads(claim_path(root, release_sha).read_bytes())
    if (
        value["release_sha256"] != release_sha
        or value["evidence_root"] != output.resolve().relative_to(root).as_posix()
    ):
        raise ValueError("Held-out attempt does not own this output")
    return value


def prepare_case(model, development, protocol, prefix, mission_id):
    """Only frozen training artifacts and observations through cutoff are inputs."""
    if not prefix:
        raise ValueError("An observation prefix is required")
    cutoff = protocol["cutoff_sim_s"]
    if any(p["sim_time_s"] > cutoff for p in prefix):
        raise ValueError("Future observation supplied to causal preparation")
    latest = observation_point(prefix[-1])
    if not math.isclose(latest.sim_time_s, cutoff, abs_tol=1e-8, rel_tol=0):
        raise ValueError("The frozen cutoff observation is missing")
    packets = tuple(p for p in prefix if p["sim_time_s"] >= cutoff - protocol["history_s"])
    samples = [s for s in prefix[-1]["samples"] if s["signal_id"] == "auxiliary.power"]
    if len(samples) != 1 or samples[0]["quality"] != "good" or samples[0]["unit"] != "W":
        raise ValueError("Good current auxiliary observation required")
    parameters = EnergyParameters(**model["parameters"])
    t = model["template"]
    template = MotionTemplate(
        t["version"],
        t["training_mission_id"],
        t["training_content_sha256"],
        t["terminal_distance_m"],
        tuple(TemplatePoint(**p) for p in t["points"]),
    )
    if (
        template.sha256 != model["template_sha256"]
        or template.sha256 != protocol["template_frozen_sha256"]
    ):
        raise ValueError("Historical template content mismatch")
    remaining = max(0, template.terminal_distance_m - latest.distance_m)
    plan = assumed_plan(
        template,
        current_distance_m=latest.distance_m,
        current_speed_m_s=latest.speed_m_s,
        remaining_distance_m=remaining,
        auxiliary_power_w=samples[0]["value"],
    )
    mission = MissionRequest(
        mission_id + "-forecast",
        mission_id,
        template.sha256,
        remaining,
        parameters.declared_capacity_kwh,
        "independent-estimator-capacity/1",
        development["prediction"]["reserve_soc"],
        "absent",
    )
    clock = ForecastClock(latest.sim_time_s, "recorded_analysis", "frozen")
    policy = ForecastPolicy("development-prefix/1", 10, 120, 10, 1.5, 5, 1000)
    planned = make_job(
        packets, latest.scope, clock, mission, policy, parameters=parameters, plan=plan
    )
    baseline = make_job(packets, latest.scope, clock, mission, policy)
    uncertainty = model["uncertainty"]
    pairs = tuple(tuple(pair) for pair in uncertainty["joint_parameter_pairs"])
    envelope = sensitivity_envelope(
        parameters, plan, latest.soc, uncertainty, parameter_pairs=pairs
    )
    ordinary = estimate_energy(parameters, plan, latest.soc, step_s=0.2)
    refined = estimate_energy(parameters, plan, latest.soc, step_s=0.1)
    refinement = abs(refined.final_energy_kwh - ordinary.final_energy_kwh) / max(
        1e-9, abs(ordinary.initial_energy_kwh - ordinary.final_energy_kwh)
    )
    return {
        "mission_id": mission_id,
        "planned": planned,
        "baseline": baseline,
        "cutoff_sim_s": cutoff,
        "cutoff_event_id": latest.event_id,
        "prefix_sha256": fingerprint(packets),
        "envelope": asdict(envelope),
        "refinement_relative": refinement,
    }


def validation_parity(release):
    """Prove unchanged preparation using sealed, already-seen validation inputs."""
    analysis = release["analysis"]
    records = json.loads((analysis / "predictions.json").read_bytes())
    checks = []
    for record in records:
        name = record["mission_id"]
        old_plan = json.loads((analysis / (name + "-planned-job.json")).read_bytes())
        old_baseline = json.loads((analysis / (name + "-baseline-job.json")).read_bytes())
        prefix = old_baseline["observations"]
        prefix[-1]["samples"].append(
            {
                "signal_id": "auxiliary.power",
                "quality": "good",
                "unit": "W",
                "value": old_plan["plan"][0]["auxiliary_power_w"],
            }
        )
        prepared = prepare_case(
            release["model"], release["development"], release["protocol"], prefix, name
        )
        # Compare serialized values: native tuples and decoded JSON lists denote
        # the same payload, without changing any numeric values or tolerances.
        # Remove the synthetic auxiliary addition from the independently loaded
        # expected baseline view; make_job projects it away in the prepared job.
        old_baseline["observations"][-1]["samples"].pop()
        if (
            canonical(prepared["planned"]) != canonical(old_plan)
            or canonical(prepared["baseline"]) != canonical(old_baseline)
            or prepared["envelope"] != record["envelope"]
            or prepared["refinement_relative"] != record["refinement_relative"]
        ):
            raise ValueError("Frozen validation preparation parity failed: " + name)
        checks.append({"mission_id": name, "jobs_envelope_refinement_identical": True})
    if len(checks) != 6:
        raise ValueError("Six sealed validation cases required for parity")
    return checks
