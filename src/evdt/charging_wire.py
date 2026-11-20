"""Strict Phase 4 command service inputs. Times use seconds unless stated."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

UUIDText = Annotated[str, Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Name = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Seconds = Annotated[float, Field(ge=0, le=86400)]
Energy = Annotated[float, Field(ge=0, le=350)]
PositivePower = Annotated[float, Field(gt=0, le=1_000_000)]
Count = Annotated[int, Field(ge=0, le=10000)]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Scope(Wire):
    run_id: UUIDText
    asset_id: Name


class Departure(Scope):
    observation_id: UUIDText
    manifest_sha256: Digest
    sim_s: Seconds
    observed_at: Annotated[str, Field(max_length=40)]
    departure_route_index: Count
    departure_m: Annotated[float, Field(ge=0, le=10000)]
    speed_m_s: Annotated[float, Field(ge=0, le=100)]
    mission_stop_served: bool
    connector_id: Name
    initial_energy_kwh: Energy
    auxiliary_power_w: PositivePower
    remaining_minimum_kwh: Energy | None
    post_charge_draw_budget_kwh: Energy | None
    bus_acceptance_w: PositivePower
    bus_charge_limit_kwh: Energy
    operator_override: bool
    thermal_block: bool
    local_protection_block: bool


class StationStatus(Wire):
    station_id: Name
    observed_sim_s: Seconds
    state: Literal["open", "closed", "outage"]
    occupied_bays: Count
    queued_vehicles: Count
    expected_wait_s: Seconds | None


class StationSnapshot(Scope):
    observation_id: UUIDText
    sim_s: Seconds
    observed_at: Annotated[str, Field(max_length=40)]
    stations: Annotated[list[StationStatus], Field(min_length=1, max_length=64)]


class PolicyRequest(Scope):
    command_id: UUIDText


class Submit(PolicyRequest):
    decision_sha256: Digest


class Claim(Scope):
    command_id: UUIDText
    sim_s: Seconds


class Effect(Scope):
    command_id: UUIDText
    manifest_sha256: Digest
    station_id: Name
    arrived_sim_s: Seconds
    target_sim_s: Seconds
    resumed_sim_s: Seconds
    finished_sim_s: Seconds
    arrival_kwh: Energy
    target_kwh: Energy
    finish_kwh: Energy
    minimum_kwh: Energy
    delivered_kwh: Energy
    trace_sha256: Digest
