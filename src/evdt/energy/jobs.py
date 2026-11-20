"""Bounded, allowlisted inputs for an isolated one-request advisory worker."""

import json
from dataclasses import asdict, fields
from typing import Any, Mapping

from evdt.energy.forecasts import (
    AdvisoryForecast,
    ForecastClock,
    ForecastPolicy,
    MissionRequest,
    planned_forecast,
    reference_forecast,
)
from evdt.energy.model import DrivingInterval, EnergyParameters
from evdt.energy.observations import (
    ObservationScope,
    SignalBindings,
    fingerprint,
    number,
    observation_point,
)

JOB_VERSION = "isolated-energy-job/1.0.0"
MAX_JOB_BYTES = 1_048_576
MAX_POINTS = 1000
MAX_INTERVALS = 1000
MAX_STEPS = 100_000
PACKET_FIELDS = {f.name for f in fields(ObservationScope)} | {
    "schema_version",
    "event_id",
    "stream_seq",
    "sim_time_s",
    "samples",
}
SAMPLE_FIELDS = {"signal_id", "value", "unit", "quality", "provenance"}


def exact_object(value: Any, names: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("Unexpected or missing input fields")
    return value


def record(value: Any, cls: Any) -> Any:
    return cls(**exact_object(value, {f.name for f in fields(cls)}))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON field")
        result[name] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def decode_job(raw: bytes) -> dict[str, Any]:
    if not 0 < len(raw) <= MAX_JOB_BYTES:
        raise ValueError("Job byte budget exceeded")
    data = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant
    )
    return exact_object(
        data,
        {
            "version",
            "method",
            "scope",
            "clock",
            "mission",
            "policy",
            "bindings",
            "observations",
            "parameters",
            "plan",
            "step_s",
        },
    )


def project_observations(
    packets: tuple[Mapping[str, Any], ...],
    scope: ObservationScope,
    clock: ForecastClock,
    bindings: SignalBindings = SignalBindings(),
) -> list[dict[str, Any]]:
    """Copy only required already-admitted quantities at or before a trusted cutoff.

    The caller supplies the authorized scope and clock. No future packet is silently
    filtered: supplying one rejects the request. This function does not authenticate.
    """
    if not isinstance(packets, tuple) or not 0 < len(packets) <= MAX_POINTS:
        raise ValueError("Observation count budget exceeded")
    result = []
    for packet in packets:
        point = observation_point(packet, bindings)
        if point.scope != scope or point.sim_time_s > clock.as_of_sim_time_s:
            raise ValueError("Observation outside scope or cutoff")
        quantities = (
            (bindings.energy, point.energy_j, "J"),
            (bindings.distance, point.distance_m, "m"),
            (bindings.soc, point.soc, "1"),
            (bindings.speed, point.speed_m_s, "m/s"),
        )
        result.append(
            {
                **asdict(scope),
                "schema_version": packet["schema_version"],
                "event_id": point.event_id,
                "stream_seq": point.stream_seq,
                "sim_time_s": point.sim_time_s,
                "samples": [
                    {
                        "signal_id": name,
                        "value": value,
                        "unit": unit,
                        "quality": "good",
                        "provenance": provenance,
                    }
                    for (name, value, unit), provenance in zip(
                        quantities, point.provenance, strict=True
                    )
                ],
            }
        )
    return result


def make_job(
    packets: tuple[Mapping[str, Any], ...],
    scope: ObservationScope,
    clock: ForecastClock,
    mission: MissionRequest,
    policy: ForecastPolicy,
    bindings: SignalBindings = SignalBindings(),
    *,
    parameters: EnergyParameters | None = None,
    plan: tuple[DrivingInterval, ...] | None = None,
    step_s: float = 0.2,
) -> dict[str, Any]:
    if (parameters is None) != (plan is None):
        raise ValueError("Supply both declared parameters and plan")
    return {
        "version": JOB_VERSION,
        "method": "reference" if parameters is None else "planned",
        "scope": asdict(scope),
        "clock": asdict(clock),
        "mission": asdict(mission),
        "policy": asdict(policy),
        "bindings": asdict(bindings),
        "observations": project_observations(packets, scope, clock, bindings),
        "parameters": asdict(parameters) if parameters else None,
        "plan": [asdict(p) for p in plan] if plan is not None else None,
        "step_s": step_s if parameters else None,
    }


def execute_job(data: dict[str, Any]) -> AdvisoryForecast:
    # Validate the object even for a direct Python caller, not only decoded JSON.
    exact_object(
        data,
        {
            "version",
            "method",
            "scope",
            "clock",
            "mission",
            "policy",
            "bindings",
            "observations",
            "parameters",
            "plan",
            "step_s",
        },
    )
    if data["version"] != JOB_VERSION or data["method"] not in ("reference", "planned"):
        raise ValueError("Unsupported job format or method")
    scope = record(data["scope"], ObservationScope)
    clock = record(data["clock"], ForecastClock)
    mission = record(data["mission"], MissionRequest)
    policy_data = dict(exact_object(data["policy"], {f.name for f in fields(ForecastPolicy)}))
    provenance = policy_data["accepted_provenance"]
    if not isinstance(provenance, (list, tuple)):
        raise ValueError("Expected provenance list")
    policy_data["accepted_provenance"] = tuple(provenance)
    policy = ForecastPolicy(**policy_data)
    if policy.max_points > MAX_POINTS or policy.max_integration_steps > MAX_STEPS:
        raise ValueError("Worker work budget exceeded")
    bindings = record(data["bindings"], SignalBindings)
    observations = data["observations"]
    if not isinstance(observations, list) or not 0 < len(observations) <= MAX_POINTS:
        raise ValueError("Worker observation budget exceeded")
    for packet in observations:
        exact_object(packet, PACKET_FIELDS)
        samples = packet["samples"]
        if not isinstance(samples, list) or len(samples) != 4:
            raise ValueError("Only four selected quantities may cross the worker boundary")
        for sample in samples:
            exact_object(sample, SAMPLE_FIELDS)
    packets = tuple(observations)
    if data["method"] == "reference":
        if any(data[k] is not None for k in ("parameters", "plan", "step_s")):
            raise ValueError("Reference job cannot carry hidden plan or parameter data")
        return reference_forecast(packets, scope, clock, mission, policy, bindings)
    parameters = record(data["parameters"], EnergyParameters)
    plan_data = data["plan"]
    if not isinstance(plan_data, list) or len(plan_data) > MAX_INTERVALS:
        raise ValueError("Worker plan budget exceeded")
    plan = tuple(record(p, DrivingInterval) for p in plan_data)
    step_s = number(data["step_s"], "step_s")
    return planned_forecast(
        packets, scope, clock, mission, policy, parameters, plan, bindings, step_s=step_s
    )


def run_request(raw: bytes) -> dict[str, Any]:
    """Return one deterministic envelope; rejected payload contents are not echoed."""
    data = decode_job(raw)
    result = execute_job(data)
    return {
        "version": "isolated-energy-result/1.0.0",
        "job_sha256": fingerprint(data),
        "forecast": asdict(result),
    }
