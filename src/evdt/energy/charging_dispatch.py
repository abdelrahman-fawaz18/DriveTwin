"""Admission checks for a charging diversion and its bound observations."""

import math
from dataclasses import dataclass

from evdt.command_timing import ClockLease, HostClock
from evdt.contract_profile import Rejected

CHARGING_COMMAND_VERSION = "charging-diversion/1.0.0"


@dataclass(frozen=True)
class DiversionProposal:
    version: str
    correlation_id: str
    run_id: str
    vehicle_id: str
    station_id: str
    policy_version: str
    release_sha256: str
    decision_sha256: str
    departure_observation_id: str
    valid_from_sim_s: float
    expires_sim_s: float
    wall_lease: ClockLease


@dataclass(frozen=True)
class DiversionContext:
    """Values established by the command boundary or simulator observer."""

    run_id: str
    vehicle_id: str
    station_id: str
    policy_version: str
    release_sha256: str
    decision_sha256: str
    departure_observation_id: str
    now_sim_s: float
    observation_sim_s: float
    observation_elapsed_ns: int
    station_observed_sim_s: float
    station_available_sim_s: float
    station_max_age_s: float
    automatic_approved: bool
    operator_fixture_approved: bool
    operator_override: bool
    thermal_block: bool
    local_protection_block: bool
    station_eligible: bool


def check_diversion(proposal, context, clock: HostClock, *, automatic: bool):
    if proposal.version != CHARGING_COMMAND_VERSION:
        raise Rejected("charging_contract_version")
    if (proposal.run_id, proposal.vehicle_id) != (context.run_id, context.vehicle_id):
        raise Rejected("charging_scope_mismatch")
    if context.operator_override:
        raise Rejected("operator_override")
    if automatic and not context.automatic_approved:
        raise Rejected("automatic_dispatch_not_approved")
    if not automatic and not context.operator_fixture_approved:
        raise Rejected("operator_fixture_not_approved")
    if context.thermal_block or context.local_protection_block:
        raise Rejected("local_protection")
    if (
        proposal.station_id != context.station_id
        or proposal.policy_version != context.policy_version
        or proposal.release_sha256 != context.release_sha256
        or proposal.decision_sha256 != context.decision_sha256
        or proposal.departure_observation_id != context.departure_observation_id
    ):
        raise Rejected("charging_lineage_mismatch")
    times = (
        proposal.valid_from_sim_s,
        proposal.expires_sim_s,
        context.now_sim_s,
        context.observation_sim_s,
        context.station_observed_sim_s,
        context.station_available_sim_s,
        context.station_max_age_s,
    )
    if any(not math.isfinite(value) or value < 0 for value in times):
        raise Rejected("charging_invalid_time")
    proposal.wall_lease.verify(clock)
    if not proposal.valid_from_sim_s <= context.now_sim_s < proposal.expires_sim_s:
        raise Rejected("charging_expired_or_early")
    if context.observation_sim_s != context.now_sim_s or not (
        0 <= clock.elapsed_ns - context.observation_elapsed_ns < 5_000_000_000
    ):
        raise Rejected("charging_stale_departure")
    if not (
        context.station_observed_sim_s <= context.station_available_sim_s <= context.now_sim_s
        and context.now_sim_s - context.station_observed_sim_s < context.station_max_age_s
    ):
        raise Rejected("charging_stale_station")
    if not context.station_eligible:
        raise Rejected("charging_station_ineligible")
