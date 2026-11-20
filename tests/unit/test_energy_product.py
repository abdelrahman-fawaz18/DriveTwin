"""Operational admission, deterministic records and independent observation lineage."""

import copy
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evdt.energy.calibration import MotionTemplate, TemplatePoint
from evdt.energy.jobs import execute_job, run_request
from evdt.energy.observations import fingerprint
from evdt.energy.product import make_record, prepare
from evdt.monitoring.energy_views import admission_reasons
from evdt.monitoring.http_readers import energy_reader, reader

NOW = datetime(2026, 11, 4, 20, tzinfo=timezone.utc)
RELEASE = "a" * 64


def trusted():
    packets, refs = [], []
    for t in range(60, 121):
        p = {
            "schema_version": "3.0.0",
            "mode": "simulation",
            "clock_domain": "simulation",
            "run_id": "00000000-0000-0000-0000-000000000001",
            "asset_id": "bus-001",
            "source_id": "sumo-adapter",
            "session_id": "00000000-0000-0000-0000-000000000002",
            "source_epoch": 1,
            "event_id": f"00000000-0000-0000-0000-{t:012}",
            "stream_seq": t,
            "sim_time_s": t,
            "samples": [
                {
                    "signal_id": s,
                    "value": v,
                    "unit": unit,
                    "quality": "good",
                    "provenance": "simulated-observation",
                }
                for s, v, unit in (
                    ("battery.pack.energy", (280 - t * 0.003) * 3_600_000, "J"),
                    ("motion.distance", t * 10, "m"),
                    ("battery.pack.soc", 0.8 - t * 0.003 / 350, "1"),
                    ("motion.speed", 10, "m/s"),
                    ("auxiliary.power", 4000, "W"),
                )
            ],
        }
        packets.append(p)
        refs.append(
            {
                "event_id": p["event_id"],
                "wire_sha256": fingerprint(p),
                "observed_at": (NOW - timedelta(seconds=120 - t)).isoformat(),
                "received_at": NOW.isoformat(),
            }
        )
    a = {
        "version": "energy-assignment/1.0.0",
        "source_id": "sumo-adapter",
        "route_sha256": "b" * 64,
        "release_sha256": RELEASE,
        "declared_capacity_kwh": 350,
        "reserve_soc": 0.1,
        "external_charging_excluded": True,
        "cutoff_sim_s": 120,
        "history_s": 60,
        "max_age_s": 5,
    }
    value = {
        "version": "trusted-energy-input/1.0.0",
        "mode": "simulation",
        "run_id": packets[0]["run_id"],
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "as_of": NOW.isoformat(),
        "assignment": a,
        "assignment_sha256": fingerprint(a),
        "clock": {
            "state": "running",
            "source_epoch": 1,
            "head_epoch": 1,
            "session_id": packets[0]["session_id"],
            "sim_time_s": 120,
            "observed_at": NOW.isoformat(),
        },
        "packets": packets,
        "wire_references": refs,
        "heartbeat_s": 5,
        "route_sha256": a["route_sha256"],
        "reason_codes": [],
    }
    return identify(value)


def identify(value):
    value["input_id"] = fingerprint(
        {k: v for k, v in value.items() if k not in ("input_id", "as_of")}
    )
    return value


def model():
    template = MotionTemplate(
        "historical-motion-template/1.0.0",
        "training",
        "c" * 64,
        2000,
        (TemplatePoint(0, 0, 10), TemplatePoint(200, 2000, 10)),
    )
    development = json.loads(
        (Path(__file__).parents[2] / "qualification/energy/development-protocol.json").read_bytes()
    )
    u = development["sensitivity"]
    u["joint_parameter_pairs"] = [[0.008, 0.86], [0.01, 0.9], [0.012, 0.94]]
    return {
        "parameters": development["estimator_parameters"],
        "template": asdict(template),
        "template_sha256": template.sha256,
        "uncertainty": u,
    }


def test_trusted_inputs_produce_minimized_deterministic_worker_records():
    v = trusted()
    assert not admission_reasons(v, NOW)
    p = prepare(v, model(), RELEASE)
    assert all(
        len(packet["samples"]) == 4 for job in p["jobs"].values() for packet in job["observations"]
    )
    outputs = {k: run_request(json.dumps(j).encode()) for k, j in p["jobs"].items()}
    r = make_record(v, p, outputs, RELEASE, "sha256:image")
    assert r == make_record(copy.deepcopy(v), p, outputs, RELEASE, "sha256:image")
    assert r["status"] == "advisory" and r["values"]["projected_final_soc"] > 0
    assert r["observation_references"] == v["wire_references"]
    assert not r["model_qualified"] and not r["automatic_dispatch_approved"]
    assert r["record_id"] == fingerprint({k: v for k, v in r.items() if k != "record_id"})


@pytest.mark.parametrize(
    "change,expected",
    [
        (lambda v: v.update(mode="replay"), "route_or_mode_outside_domain"),
        (lambda v: v.update(route_sha256="d" * 64), "route_or_mode_outside_domain"),
        (lambda v: v.update(clock=None), "source_identity_not_ready"),
        (lambda v: v["clock"].update(head_epoch=2), "source_identity_not_ready"),
        (lambda v: v["clock"].update(state="paused"), "source_paused"),
        (lambda v: v["clock"].update(state="finished"), "source_finished"),
        (
            lambda v: v["clock"].update(observed_at=(NOW - timedelta(seconds=6)).isoformat()),
            "source_disconnected",
        ),
        (
            lambda v: v["clock"].update(observed_at=(NOW + timedelta(seconds=1)).isoformat()),
            "source_disconnected",
        ),
        (lambda v: v["clock"].update(sim_time_s=121), "prediction_cutoff_outside_evaluated_domain"),
        (lambda v: v.update(packets=[]), "insufficient_or_excessive_history"),
        (lambda v: v["packets"].pop(), "latest_observation_missing"),
        (lambda v: v["packets"][-1].update(asset_id="bus-002"), "observation_scope_mismatch"),
        (lambda v: v["packets"][-1].update(session_id="other"), "observation_scope_mismatch"),
        (
            lambda v: v["wire_references"][-1].update(
                observed_at=(NOW - timedelta(seconds=6)).isoformat()
            ),
            "stale_or_future_observation",
        ),
        (
            lambda v: v["packets"][-1]["samples"][0].update(quality="missing", value=None),
            "required_observation_invalid",
        ),
        (
            lambda v: v["packets"][-1]["samples"][-1].update(quality="missing", value=None),
            "auxiliary_observation_invalid",
        ),
    ],
)
def test_untrusted_or_unusable_live_state_never_makes_a_prediction(change, expected):
    v = trusted()
    change(v)
    v["reason_codes"] = admission_reasons(v, NOW)
    assert v["reason_codes"] == [expected]
    identify(v)
    if v["mode"] != "simulation":
        with pytest.raises(ValueError, match="format"):
            prepare(v, model(), RELEASE)
        return
    p = prepare(v, model(), RELEASE)
    r = make_record(v, p, {}, RELEASE, "sha256:image")
    assert r["status"] == "unavailable" and r["values"] is None and r["uncertainty"] is None
    assert not p["jobs"]


def test_tampering_model_release_and_template_are_refused():
    v = trusted()
    m = model()
    v["packets"][-1]["samples"][0]["value"] += 1
    with pytest.raises(ValueError, match="fingerprint"):
        prepare(v, m, RELEASE)
    with pytest.raises(ValueError, match="release"):
        prepare(trusted(), m, "e" * 64)
    m["template"]["terminal_distance_m"] += 1
    with pytest.raises(ValueError, match="template"):
        prepare(trusted(), m, RELEASE)


def test_bad_worker_response_is_not_a_saved_forecast():
    v = trusted()
    p = prepare(v, model(), RELEASE)
    with pytest.raises(ValueError, match="Complete"):
        make_record(v, p, {}, RELEASE, "image")
    output = {k: run_request(json.dumps(j).encode()) for k, j in p["jobs"].items()}
    output["candidate"]["job_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="mismatch"):
        make_record(v, p, output, RELEASE, "image")


def test_gapped_history_fails_in_worker_without_misleading_zero_forecast():
    v = trusted()
    v["packets"].pop(30)
    v["wire_references"].pop(30)
    identify(v)
    p = prepare(v, model(), RELEASE)
    out = {k: run_request(json.dumps(j).encode()) for k, j in p["jobs"].items()}
    r = make_record(v, p, out, RELEASE, "image")
    assert r["status"] == "unavailable" and r["values"] is None
    assert r["reason_codes"] == ["observation_gap"]


def test_original_packets_are_not_mutated():
    v = trusted()
    original = copy.deepcopy(v)
    prepare(v, model(), RELEASE)
    assert v == original


def test_predicted_shortfall_is_a_value_not_unavailable():
    v = trusted()
    for p in v["packets"]:
        p["samples"][2]["value"] = 0.10001
    identify(v)
    p = prepare(v, model(), RELEASE)
    assert execute_job(p["jobs"]["candidate"]).status == "advisory"


def test_model_credential_is_asset_scoped_and_cannot_read_recipes(monkeypatch, tmp_path):
    from fastapi import HTTPException

    file = tmp_path / "readers.json"
    file.write_text(
        json.dumps(
            {
                "readers": [
                    {"token": "ordinary", "assets": ["bus-001"]},
                    {"token": "energy", "assets": ["bus-002"], "purpose": "energy_input"},
                    {"token": "unknown", "assets": ["bus-003"], "purpose": "unknown"},
                ]
            }
        )
    )
    monkeypatch.setenv("READERS_FILE", str(file))
    assert reader("Bearer ordinary") == ["bus-001"]
    assert energy_reader("Bearer energy") == ["bus-002"]
    for token in ("energy", "unknown", "wrong"):
        with pytest.raises(HTTPException) as error:
            reader("Bearer " + token)
        assert error.value.status_code == 401
    with pytest.raises(HTTPException):
        energy_reader("Bearer unknown")


def test_dashboard_operator_key_reads_only_its_scoped_vehicle(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from evdt.monitoring.http_readers import dashboard_operator_asset

    readers = tmp_path / "readers.json"
    readers.write_text(json.dumps({"readers": [{"token": "reader", "assets": ["bus-002"]}]}))
    operator = tmp_path / "operator"
    operator.write_text("scoped-operator\n")
    monkeypatch.setenv("READERS_FILE", str(readers))
    monkeypatch.setenv("EVDT_DASHBOARD_OPERATOR_FILE", str(operator))
    scope = "EVDT_MOVING_CHARGE_OPERATOR_ASSET"
    assert dashboard_operator_asset("Bearer scoped-operator", scope) is None
    monkeypatch.setenv(scope, "bus-001")
    assert reader("Bearer scoped-operator") == ["bus-001"]
    assert reader("Bearer reader") == ["bus-002"]
    assert dashboard_operator_asset("Bearer reader", scope) is None
    assert dashboard_operator_asset("Bearer scoped-operator", "EVDT_OPERATOR_ASSET") is None
    with pytest.raises(HTTPException):
        energy_reader("Bearer scoped-operator")
