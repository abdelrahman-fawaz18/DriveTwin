"""Trusted advisory orchestration; never packaged in the isolated point worker."""

from dataclasses import asdict

from evdt.energy.calibration import (
    MotionTemplate,
    TemplatePoint,
    assumed_plan,
    sensitivity_envelope,
)
from evdt.energy.forecasts import ForecastClock, ForecastPolicy, MissionRequest
from evdt.energy.jobs import make_job
from evdt.energy.model import MODEL_VERSION, EnergyParameters
from evdt.energy.navigation import finish_stations, prepare_stations
from evdt.energy.observations import fingerprint, number, observation_point


def prepare(value, model, release_sha, navigation=None):
    if value["version"] != "trusted-energy-input/1.0.0" or value["mode"] != "simulation":
        raise ValueError("Unknown trusted input format")
    if (
        fingerprint({k: v for k, v in value.items() if k not in ("input_id", "as_of")})
        != value["input_id"]
    ):
        raise ValueError("Trusted input fingerprint mismatch")
    if not value["assignment"] or value["assignment"]["release_sha256"] != release_sha:
        raise ValueError("Configured model release mismatch")
    if navigation is not None and fingerprint(navigation) != value["assignment"].get(
        "navigation_content_sha256"
    ):
        raise ValueError("Configured navigation profile mismatch")
    stations, station_jobs = prepare_stations(value, model, navigation)
    if value["reason_codes"]:
        return {
            "reason_codes": value["reason_codes"],
            "jobs": {},
            "uncertainty": None,
            "explanation": None,
            "stations": stations,
        }
    packets = tuple(value["packets"])
    point = observation_point(packets[-1])
    a = value["assignment"]
    p = EnergyParameters(**model["parameters"])
    if (
        a["declared_capacity_kwh"] != p.declared_capacity_kwh
        or a["reserve_soc"] != 0.1
        or a["cutoff_sim_s"] != 120
    ):
        raise ValueError("Assignment changes frozen study assumptions")
    t = model["template"]
    template = MotionTemplate(
        t["version"],
        t["training_mission_id"],
        t["training_content_sha256"],
        t["terminal_distance_m"],
        tuple(TemplatePoint(**x) for x in t["points"]),
    )
    if template.sha256 != model["template_sha256"]:
        raise ValueError("Frozen template mismatch")
    aux = next(s for s in packets[-1]["samples"] if s["signal_id"] == "auxiliary.power")
    power = number(aux["value"], "auxiliary_power_w")
    if power < 0:
        raise ValueError("Negative auxiliary power")
    remaining = max(0, template.terminal_distance_m - point.distance_m)
    plan = assumed_plan(
        template,
        current_distance_m=point.distance_m,
        current_speed_m_s=point.speed_m_s,
        remaining_distance_m=remaining,
        auxiliary_power_w=power,
    )
    mission = MissionRequest(
        value["input_id"],
        "montreal-core-loop-v1",
        template.sha256,
        remaining,
        p.declared_capacity_kwh,
        "independent-estimator-capacity/1",
        a["reserve_soc"],
        "absent",
    )
    clock = ForecastClock(point.sim_time_s, "live_snapshot", "running")
    policy = ForecastPolicy("development-prefix/1", 10, 120, 10, 1.5, 5, 1000)
    jobs = {
        "candidate": make_job(
            packets, point.scope, clock, mission, policy, parameters=p, plan=plan
        ),
        "reference": make_job(packets, point.scope, clock, mission, policy),
    }
    u = model["uncertainty"]
    envelope = sensitivity_envelope(
        p, plan, point.soc, u, parameter_pairs=tuple(tuple(x) for x in u["joint_parameter_pairs"])
    )
    jobs.update(station_jobs)
    explanation = {
        "version": "energy-explanation/1.0.0",
        "horizon_duration_s": sum(x.duration_s for x in plan),
        "horizon_distance_m": remaining,
        "declared_capacity_kwh": p.declared_capacity_kwh,
        "loaded_mass_kg": p.loaded_mass_kg,
        "reserve_soc": a["reserve_soc"],
        "auxiliary_power_w": power,
        "grade_assumption": "Flat road",
        "motion_assumption": "Frozen training-only historical speed profile, anchored at current observations and re-timed to remaining route distance",
        "capacity_basis": "Independent declared estimator capacity; actual plant capacity is not read",
        "external_charging_basis": a.get("charging_basis", "Declared excluded"),
        "uncertainty_method": envelope.method,
        "uncertainty_assumptions": u,
        "conditional_reference_assumption": "Recent net consumption and driving pattern continue; time and distance are to the declared reserve, not a validated empty-battery range",
    }
    return {
        "reason_codes": [],
        "jobs": jobs,
        "uncertainty": asdict(envelope),
        "explanation": explanation,
        "stations": stations,
    }


def make_record(value, prepared, outputs, release_sha, worker_image):
    reasons = list(prepared["reason_codes"])
    values = None
    if not reasons:
        if set(outputs) != set(prepared["jobs"]):
            raise ValueError("Complete worker batch required")
        for method, response in outputs.items():
            if response.get("version") != "isolated-energy-result/1.0.0" or response.get(
                "job_sha256"
            ) != fingerprint(prepared["jobs"][method]):
                raise ValueError("Worker output/input mismatch")
        forecast = outputs["candidate"]["forecast"]
        if forecast["status"] != "advisory":
            reasons = list(forecast["reason_codes"])
        elif outputs["reference"]["forecast"]["status"] != "advisory":
            reasons = ["reference_unavailable"]
        else:
            values = forecast["values"]
    record = {
        "version": "persistent-energy-forecast/1.0.0",
        "run_id": value["run_id"],
        "asset_id": value["asset_id"],
        "source_id": value["source_id"],
        "input_id": value["input_id"],
        "assignment_sha256": value["assignment_sha256"],
        "release_sha256": release_sha,
        "model_version": "synthetic-energy-candidate/1.1.0",
        "numerical_model_version": MODEL_VERSION,
        "status": "unavailable" if reasons else "advisory",
        "reason_codes": reasons or ["synthetic_advisory_pending_final_review"],
        "values": values,
        "uncertainty": None if reasons else prepared["uncertainty"],
        "observation_references": value["wire_references"],
        "source_clock": value["clock"],
        "assumptions": value["assignment"],
        "input_as_of": value["as_of"],
        "job_sha256": {k: fingerprint(v) for k, v in prepared["jobs"].items()},
        "worker_image_id": worker_image,
        "worker_outputs": outputs,
        "model_qualified": False,
        "automatic_dispatch_approved": False,
        "extensions": {
            "version": "energy-mission-extensions/1.0.0",
            "explanation": prepared["explanation"],
            "stations": finish_stations(prepared["stations"], outputs, prepared["jobs"]),
        },
    }
    # The observation as-of time is metadata but must be stable on retries.
    record["input_as_of"] = value["clock"]["observed_at"] if value["clock"] else None
    record["record_id"] = fingerprint(record)
    return record
