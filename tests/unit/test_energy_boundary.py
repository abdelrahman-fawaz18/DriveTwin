"""Worker input minimization, bounded requests and whole-mission leak guards."""

import copy
import json
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from evdt.energy.forecasts import (
    ForecastClock,
    ForecastPolicy,
    MissionRequest,
    planned_forecast,
    reference_forecast,
)
from evdt.energy.jobs import MAX_JOB_BYTES, decode_job, execute_job, make_job, run_request
from evdt.energy.model import DrivingInterval, EnergyParameters
from evdt.energy.observations import ObservationScope, fingerprint
from evdt.energy.partitions import (
    MissionArtifact,
    authorize_partition,
    parse_design,
    validate_inventory,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def inputs():
    scope = ObservationScope(
        "simulation",
        "example-run",
        "example-bus",
        "example-source",
        "example-session",
        1,
        "simulation",
    )
    packets = []
    for seq, (time, distance, energy) in enumerate([(0, 0, 3), (50, 500, 2.5), (100, 1000, 2)]):
        quantities = [
            ("battery.pack.energy", energy * 3_600_000, "J"),
            ("motion.distance", distance, "m"),
            ("battery.pack.soc", energy / 10, "1"),
            ("motion.speed", 10, "m/s"),
        ]
        packets.append(
            {
                **asdict(scope),
                "schema_version": "3.0.0",
                "event_id": f"event-{seq}",
                "stream_seq": seq,
                "sim_time_s": time,
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
    return (
        tuple(packets),
        scope,
        ForecastClock(100, "recorded_analysis", "frozen"),
        MissionRequest("request", "mission", "plan/1", 1000, 10, "capacity/1", 0.05, "absent"),
        ForecastPolicy("example/1", 10, 300, 10, 60, 5, 100),
    )


def encoded(job):
    return json.dumps(job, allow_nan=False).encode()


def test_reference_worker_matches_existing_math_and_is_repeatable(inputs):
    job = make_job(*inputs)
    first = run_request(encoded(job))
    assert first == run_request(encoded(copy.deepcopy(job)))
    assert first["forecast"] == asdict(reference_forecast(*inputs))
    assert first["forecast"]["values"]["net_mission_energy_kwh"] == 1
    assert first["forecast"]["model_qualified"] is False


def test_planned_worker_preserves_declared_assumptions_and_step(inputs):
    params = EnergyParameters(10, 1000, 0, 2, 0, 0, 1, 1, 1, 100_000, 100_000, 10)
    plan = (DrivingInterval(100, 10, 10, 0, 36000),)
    job = make_job(*inputs, parameters=params, plan=plan, step_s=0.1)
    result = run_request(encoded(job))["forecast"]
    assert result == asdict(planned_forecast(*inputs, params, plan, step_s=0.1))
    assert result["integration_step_s"] == 0.1


def test_projection_strips_unrelated_values_hidden_annotations_and_artifact_metadata(inputs):
    expected = make_job(*inputs)
    for p in inputs[0]:
        p.update(
            annotations={"actual_capacity": object(), "future_traffic": object()},
            manifest_sha256="opaque",
            observed_at="unused",
        )
        p["samples"].append({"signal_id": "hidden_truth", "value": object()})
    actual = make_job(*inputs)
    assert actual == expected
    assert "annotations" not in encoded(actual).decode()
    assert len(actual["observations"][0]["samples"]) == 4


@pytest.mark.parametrize(
    "kind", ["future", "wrong_asset", "wrong_run", "wrong_session", "wrong_epoch"]
)
def test_projection_rejects_instead_of_silently_filtering(inputs, kind):
    packet = inputs[0][-1]
    if kind == "future":
        packet["sim_time_s"] = 101
    else:
        packet[
            {
                "wrong_asset": "asset_id",
                "wrong_run": "run_id",
                "wrong_session": "session_id",
                "wrong_epoch": "source_epoch",
            }[kind]
        ] = 2 if kind == "wrong_epoch" else "other"
    with pytest.raises(ValueError):
        make_job(*inputs)


@pytest.mark.parametrize(
    "section", [None, "scope", "clock", "mission", "policy", "bindings", "packet", "sample"]
)
def test_strict_worker_rejects_unknown_hidden_fields_at_every_boundary(inputs, section):
    job = make_job(*inputs)
    target = (
        job
        if section is None
        else job["observations"][0]
        if section == "packet"
        else job["observations"][0]["samples"][0]
        if section == "sample"
        else job[section]
    )
    target["hidden_truth"] = {"actual_capacity": 999, "future_traffic": [1, 2, 3]}
    with pytest.raises(ValueError):
        run_request(encoded(job))


@pytest.mark.parametrize("section", ["parameters", "plan"])
def test_planned_unknown_fields_are_not_open_extension_bags(inputs, section):
    params = EnergyParameters(10, 1000, 0, 2, 0, 0, 1, 1, 1, 100_000, 100_000, 10)
    job = make_job(*inputs, parameters=params, plan=(DrivingInterval(100, 10, 10, 0, 0),))
    target = job["parameters"] if section == "parameters" else job["plan"][0]
    target["fault_label"] = "private"
    with pytest.raises(ValueError):
        run_request(encoded(job))


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}",
        b"[]",
        b"null",
        b'{"version":1,"version":2}',
        b'{"x":NaN}',
        b"\xff",
        b" " * (MAX_JOB_BYTES + 1),
    ],
)
def test_invalid_or_oversized_wire_inputs_fail(raw):
    with pytest.raises(ValueError):
        decode_job(raw)


@pytest.mark.parametrize(
    "kind",
    [
        "method",
        "version",
        "point_budget",
        "step_budget",
        "extra_sample",
        "reference_plan",
        "too_many_intervals",
    ],
)
def test_worker_enforces_its_own_budget_and_method(inputs, kind):
    job = make_job(*inputs)
    if kind == "method":
        job["method"] = "import arbitrary.module"
    elif kind == "version":
        job["version"] = "future"
    elif kind == "point_budget":
        job["policy"]["max_points"] = 1001
    elif kind == "step_budget":
        job["policy"]["max_integration_steps"] = 100001
    elif kind == "extra_sample":
        job["observations"][0]["samples"].append(dict(job["observations"][0]["samples"][0]))
    elif kind == "reference_plan":
        job["plan"] = []
    else:
        params = EnergyParameters(10, 1000, 0, 2, 0, 0, 1, 1, 1, 100_000, 100_000, 10)
        job = make_job(*inputs, parameters=params, plan=(DrivingInterval(1, 10, 10, 0, 0),) * 1001)
    with pytest.raises(ValueError):
        run_request(encoded(job))


def test_stale_input_is_unavailable_not_zero_or_worker_crash(inputs):
    job = make_job(*inputs)
    job["clock"]["as_of_sim_time_s"] = 106
    result = run_request(encoded(job))["forecast"]
    assert result["status"] == "unavailable" and result["values"] is None
    assert result["reason_codes"] == ("stale_observation",)


def test_direct_execute_still_validates_job_shape():
    with pytest.raises(ValueError):
        execute_job({"hidden": "value"})


@pytest.fixture
def design_data():
    return json.loads((ROOT / "qualification/energy/mission-design.json").read_bytes())


def test_design_reserves_whole_unseen_realizations_for_every_regime(design_data):
    design = parse_design(design_data)
    assert Counter(m.partition for m in design.missions) == {
        "train": 12,
        "validation": 6,
        "held_out": 6,
    }
    assert (
        len(design.missions) == 24
        and len({m.recipe.realization_seed for m in design.missions}) == 24
    )
    assert all(m.family_id == m.mission_id for m in design.missions)
    assert design.design_sha256 == parse_design(copy.deepcopy(design_data)).design_sha256
    assert any(
        m.recipe.actual_capacity_kwh != m.recipe.declared_capacity_kwh for m in design.missions
    )


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate_id",
        "family",
        "seed",
        "recipe",
        "missing_regime_partition",
        "unknown_partition",
        "claim_collected",
    ],
)
def test_design_rejects_cross_partition_leakage_and_unearned_collection(design_data, kind):
    first = design_data["missions"][0]
    other = design_data["missions"][3]
    if kind == "duplicate_id":
        other["mission_id"] = first["mission_id"]
    elif kind == "family":
        other["family_id"] = first["family_id"]
    elif kind == "seed":
        other["recipe"]["realization_seed"] = first["recipe"]["realization_seed"]
    elif kind == "recipe":
        other["recipe"] = copy.deepcopy(first["recipe"])
    elif kind == "missing_regime_partition":
        design_data["missions"].remove(other)
    elif kind == "unknown_partition":
        other["partition"] = "random_rows"
    else:
        design_data["status"] = "collected"
    with pytest.raises(ValueError):
        parse_design(design_data)


def artifacts_for(design):
    return tuple(
        MissionArtifact(
            f"artifact-{m.mission_id}",
            m.mission_id,
            m.family_id,
            m.partition,
            m.recipe_sha256,
            fingerprint([m.mission_id, "trace"]),
            fingerprint([m.mission_id, "observation-content"]),
        )
        for m in design.missions
    )


def test_inventory_binds_copies_and_replays_to_frozen_mission_assignment(design_data):
    design = parse_design(design_data)
    artifacts = artifacts_for(design)
    validate_inventory(design, artifacts, require_complete=True)
    validate_inventory(
        design,
        (*artifacts, replace(artifacts[0], artifact_id="same-trip-replay")),
        require_complete=True,
    )
    with pytest.raises(ValueError):
        validate_inventory(design, (), require_complete=True)
    validate_inventory(design, (), require_complete=False)


@pytest.mark.parametrize(
    "kind",
    [
        "family",
        "recipe",
        "partition",
        "unknown_mission",
        "duplicate_id",
        "copied_trace",
        "copied_observations",
    ],
)
def test_inventory_blocks_mislabeling_and_renamed_copies(design_data, kind):
    design = parse_design(design_data)
    records = list(artifacts_for(design))
    first = records[0]
    other = records[3]
    if kind == "family":
        other = replace(other, family_id=first.family_id)
    elif kind == "recipe":
        other = replace(other, recipe_sha256=first.recipe_sha256)
    elif kind == "partition":
        other = replace(other, partition="train")
    elif kind == "unknown_mission":
        other = replace(other, mission_id="unassigned")
    elif kind == "duplicate_id":
        other = replace(other, artifact_id=first.artifact_id)
    elif kind == "copied_trace":
        other = replace(other, causal_trace_sha256=first.causal_trace_sha256)
    else:
        other = replace(other, observation_content_sha256=first.observation_content_sha256)
    records[3] = other
    with pytest.raises(ValueError):
        validate_inventory(design, tuple(records), require_complete=True)


@pytest.mark.parametrize(
    "partition,purpose",
    [
        ("held_out", "fit"),
        ("validation", "fit"),
        ("train", "tune"),
        ("held_out", "tune"),
        ("train", "evaluate"),
        ("validation", "evaluate"),
    ],
)
def test_partition_purpose_gate(partition, purpose):
    with pytest.raises(ValueError):
        authorize_partition(partition, purpose)


def test_final_evaluation_gate_requires_all_frozen_references():
    authorize_partition("train", "fit")
    authorize_partition("validation", "tune")
    for missing in ("calibration_sha256", "targets_sha256", "protocol_sha256"):
        refs = {
            k: fingerprint(k) for k in ("calibration_sha256", "targets_sha256", "protocol_sha256")
        }
        del refs[missing]
        with pytest.raises(ValueError):
            authorize_partition("held_out", "evaluate", **refs)
    authorize_partition(
        "held_out",
        "evaluate",
        calibration_sha256=fingerprint("candidate"),
        targets_sha256=fingerprint("targets"),
        protocol_sha256=fingerprint("protocol"),
    )
