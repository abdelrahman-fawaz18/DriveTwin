"""Sealed-release eligibility, one-time claims and causal evaluation boundaries."""

import copy
import importlib.util
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from evdt.energy.calibration import MotionTemplate, TemplatePoint
from evdt.energy.observations import ObservationScope
from evdt.energy.partitions import parse_design

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/known_route_energy"))
    import energy_holdout

    return energy_holdout


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def frozen(tmp_path, helper):
    design_data = json.loads((ROOT / "qualification/energy/mission-design.json").read_bytes())
    development = json.loads((ROOT / "qualification/energy/development-protocol.json").read_bytes())
    put(tmp_path, "qualification/energy/mission-design.json", design_data)
    devpath = put(tmp_path, "qualification/energy/development-protocol.json", development)
    source = put(tmp_path, "fixed-source.json", {"version": "unchanged"})
    design = parse_design(design_data)
    training = sorted(m.mission_id for m in design.missions if m.partition == "train")
    analysis = tmp_path / "evidence/p2/frozen"
    analysis.mkdir(parents=True)
    put(
        analysis,
        "model.json",
        {
            "version": "synthetic-energy-candidate/1.1.0",
            "training_only": True,
            "model_qualified": False,
            "fit": {"training_ids": training},
            "template": {"training_mission_id": training[0]},
        },
    )
    put(
        analysis,
        "targets.json",
        {
            "version": "D18-synthetic-energy/1.0.0",
            "validation_targets_met": True,
            "automatic_dispatch_approved": False,
            "targets": development["proposed_D18_targets"],
        },
    )
    put(
        analysis,
        "evaluation-protocol.json",
        {
            "version": "held-out-energy-protocol/1.0.0",
            "required_model_sha256": helper.sha(analysis / "model.json"),
            "required_targets_sha256": helper.sha(analysis / "targets.json"),
            "design_sha256": design.design_sha256,
            "development_protocol_sha256": helper.sha(devpath),
            "partition": "held_out",
            "expected_missions": 6,
            "cutoff_sim_s": 120,
            "history_s": 60,
        },
    )
    put(
        analysis,
        "qualification.json",
        {
            "passed": True,
            "validation": {"targets_met": True},
            "source_sha256": {"fixed-source.json": helper.sha(source)},
        },
    )
    artifacts = {p.name: helper.sha(p) for p in analysis.iterdir()}
    manifest = {
        "version": "energy-validation-release/1.0.0",
        "model_qualified": False,
        "held_out_release_eligible": True,
        "validation_targets_met": True,
        "worker_outputs_match": True,
        "held_out_generated_or_scored": False,
        "analysis_path": "evidence/p2/frozen",
        "source_sha256": {"fixed-source.json": helper.sha(source)},
        "analysis_artifact_sha256": artifacts,
        "frozen_artifact_sha256": {
            n: artifacts[n] for n in ("model.json", "targets.json", "evaluation-protocol.json")
        },
    }
    put(tmp_path, "docs/models/energy-validation-release.json", manifest)
    return tmp_path, analysis, manifest


def test_actual_release_bytes_and_validation_are_required(frozen, helper):
    root, _, _ = frozen
    value = helper.verify_release(root)
    assert len([m for m in value["design"].missions if m.partition == "held_out"]) == 6
    assert value["targets"]["energy_mae_kwh_max"] == 1


@pytest.mark.parametrize(
    "target",
    ["model.json", "targets.json", "evaluation-protocol.json", "qualification.json", "source"],
)
def test_tampered_frozen_bytes_never_open_the_gate(frozen, helper, target):
    root, analysis, _ = frozen
    put(
        root if target == "source" else analysis,
        "fixed-source.json" if target == "source" else target,
        {"changed": True},
    )
    with pytest.raises(ValueError):
        helper.verify_release(root)


@pytest.mark.parametrize(
    "flag",
    [
        "held_out_release_eligible",
        "validation_targets_met",
        "worker_outputs_match",
        "held_out_generated_or_scored",
    ],
)
def test_manifest_eligibility_flags_fail_closed(frozen, helper, flag):
    root, _, manifest = frozen
    manifest[flag] = flag == "held_out_generated_or_scored"
    put(root, "docs/models/energy-validation-release.json", manifest)
    with pytest.raises(ValueError):
        helper.verify_release(root)


def test_one_time_claim_cannot_create_another_trial(tmp_path, helper):
    output = tmp_path / "evidence/p2/final-evaluation"
    path = helper.claim_once(tmp_path, "a" * 64, output)
    assert path.is_file()
    assert helper.verify_claim(tmp_path, "a" * 64, output)["state"] == "reserved_before_collection"
    with pytest.raises(FileExistsError):
        helper.claim_once(tmp_path, "a" * 64, tmp_path / "evidence/p2/another")
    with pytest.raises(ValueError):
        helper.verify_claim(tmp_path, "a" * 64, tmp_path / "evidence/p2/another")


def test_claim_rejects_escape_and_bad_release_fingerprints(tmp_path, helper):
    with pytest.raises(ValueError):
        helper.claim_once(tmp_path, "a" * 64, tmp_path / "outside-evidence")
    for value in ("short", "../" + "a" * 61, "G" * 64):
        with pytest.raises(ValueError):
            helper.claim_path(tmp_path, value)


@pytest.fixture
def case():
    development = json.loads((ROOT / "qualification/energy/development-protocol.json").read_bytes())
    parameters = json.loads((ROOT / "qualification/energy/forecast-cases.json").read_bytes())[
        "parameters"
    ]
    template = MotionTemplate(
        "historical-motion-template/1.0.0",
        "training-only",
        "b" * 64,
        3000,
        tuple(TemplatePoint(float(i), float(i * 10), 10.0) for i in range(301)),
    )
    model = {
        "parameters": parameters,
        "template": asdict(template),
        "template_sha256": template.sha256,
        "uncertainty": {**development["sensitivity"], "joint_parameter_pairs": [[0.0, 1.0]]},
    }
    scope = ObservationScope(
        "simulation", "heldout-run", "bus", "source", "session", 1, "simulation"
    )
    prefix = []
    for i in range(121):
        energy = 3 - i / 120
        quantities = [
            ("battery.pack.energy", energy * 3_600_000, "J"),
            ("motion.distance", i * 10, "m"),
            ("battery.pack.soc", energy / 10, "1"),
            ("motion.speed", 10, "m/s"),
            ("auxiliary.power", 500, "W"),
        ]
        prefix.append(
            {
                **asdict(scope),
                "schema_version": "3.0.0",
                "event_id": f"event-{i}",
                "stream_seq": i,
                "sim_time_s": float(i),
                "samples": [
                    {
                        "signal_id": n,
                        "value": v,
                        "unit": u,
                        "quality": "good",
                        "provenance": "simulated-observation",
                    }
                    for n, v, u in quantities
                ],
            }
        )
    protocol = {"cutoff_sim_s": 120, "history_s": 60, "template_frozen_sha256": template.sha256}
    return model, development, protocol, prefix


def test_preparation_uses_frozen_template_and_current_prefix_only(case, helper, monkeypatch):
    import evdt.energy.calibration as calibration

    def no_fit(*args, **kwargs):
        raise AssertionError("Final evaluation must never fit or choose another template")

    monkeypatch.setattr(calibration, "fit_grid", no_fit)
    monkeypatch.setattr(calibration, "select_template", no_fit)
    value = helper.prepare_case(*case, "heldout-case")
    assert value["planned"]["parameters"] == case[0]["parameters"]
    assert len(value["planned"]["observations"]) == 61
    assert max(p["sim_time_s"] for p in value["planned"]["observations"]) == 120
    assert value["envelope"]["probability_guarantee"] is False


@pytest.mark.parametrize("kind", ["future", "missing_cutoff", "bad_auxiliary", "changed_template"])
def test_causal_preparation_rejects_unusable_or_changed_inputs(case, helper, kind):
    if kind == "future":
        case[3].append({**case[3][-1], "sim_time_s": 121})
    elif kind == "missing_cutoff":
        case[3].pop()
    elif kind == "bad_auxiliary":
        case[3][-1]["samples"][-1]["quality"] = "invalid"
    else:
        case[0]["template"]["terminal_distance_m"] = 3001
    with pytest.raises(ValueError):
        helper.prepare_case(*case, "heldout-case")


def test_hidden_annotations_do_not_change_jobs_or_scenarios(case, helper):
    expected = helper.prepare_case(*case, "heldout-case")
    changed = copy.deepcopy(case)
    for packet in changed[3]:
        packet["annotations"] = {"future_capacity": "forbidden metadata"}
    actual = helper.prepare_case(*changed, "heldout-case")
    assert actual["planned"] == expected["planned"]
    assert actual["baseline"] == expected["baseline"]
    assert actual["envelope"] == expected["envelope"]


def test_incomplete_batch_cannot_start_outcome_scoring(tmp_path, helper, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/known_route_energy"))
    spec = importlib.util.spec_from_file_location(
        "heldout_score_guard", ROOT / "scripts/known_route_energy/evaluate_energy_holdout.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "verify_collection", lambda *args: {"missions": []})
    put(tmp_path, "prepared/cases.json", {"release_sha256": "a" * 64})
    put(tmp_path, "worker-batch.json", {"completed": False})
    with pytest.raises(ValueError):
        module.score(tmp_path, {"manifest_sha256": "a" * 64})
    assert not (tmp_path / "evaluation.json").exists()
