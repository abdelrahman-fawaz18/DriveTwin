"""Validate the exact advisory offer again at an operator handoff."""

from dataclasses import dataclass, field

from evdt.command_timing import ClockLease, HostClock
from evdt.contract_profile import Rejected
from evdt.energy.moving_charge_recovery import ReplacementOf
from evdt.energy.route_aware.context import SourceContext
from evdt.energy.route_aware.contracts import SourceScope
from evdt.energy.route_aware.validation import digest, integer, number, text


@dataclass(frozen=True)
class ReviewedStop:
    job_fence: int
    assignment_sha256: str
    candidate_sha256: str
    scope: SourceScope
    manifest_sha256: str
    route_revision: str
    bay_id: str
    bay_external_index: int
    station_scenario_id: str
    station_revision: int
    target_soc: float
    working_not_before: float
    working_expires: float
    lease: ClockLease
    detour_sha256: str | None = field(default=None, metadata={"omit_none_on_wire": True})
    minimum_handoff_soc: float | None = field(default=None, metadata={"omit_none_on_wire": True})
    replacement_of: ReplacementOf | None = field(default=None, metadata={"omit_none_on_wire": True})
    queue_opens_working_s: float | None = field(default=None, metadata={"omit_none_on_wire": True})

    def __post_init__(self):
        integer(self.job_fence, "charging job fence", 1, 9_007_199_254_740_991)
        integer(self.bay_external_index, "charging road occurrence", 1, 9_999)
        integer(self.station_revision, "station status revision", 1, 9_007_199_254_740_991)
        text(self.bay_id, "charging bay identity")
        text(self.station_scenario_id, "charging station scenario")
        for value in (
            self.assignment_sha256,
            self.candidate_sha256,
            self.manifest_sha256,
            self.route_revision,
        ):
            digest(value, "reviewed input digest")
        number(self.target_soc, "target charge", 0.1, 1)
        number(self.working_not_before, "earliest handoff", 0, 1e12)
        number(self.working_expires, "latest handoff", self.working_not_before, 1e12)
        if self.detour_sha256 is not None:
            digest(self.detour_sha256, "reviewed detour identity")
        if self.minimum_handoff_soc is not None:
            number(self.minimum_handoff_soc, "minimum handoff charge", 0.1, 1)
        if self.replacement_of is not None and (
            not isinstance(self.replacement_of, ReplacementOf)
            or self.minimum_handoff_soc is None
            or self.detour_sha256 is not None
        ):
            raise ValueError("Recovery requires a reviewed on-route replacement and charge floor")
        if self.working_expires == self.working_not_before:
            raise ValueError("Charging handoff requires a nonempty working-time lease")
        if self.queue_opens_working_s is not None:
            number(self.queue_opens_working_s, "reviewed queue opening", self.working_expires, 1e12)
            if self.detour_sha256 is not None or self.replacement_of is not None:
                raise ValueError("A queued handoff requires an original on-route stop")
        if not isinstance(self.scope, SourceScope) or not isinstance(self.lease, ClockLease):
            raise ValueError(
                "Charging handoff requires a typed source scope and elapsed clock lease"
            )


def revalidate(
    reviewed,
    current: SourceContext,
    status,
    *,
    clock: HostClock,
    working_s,
    assignment_sha256,
    job_fence,
    candidate_sha256,
    source_state,
    reserve_soc,
    soc,
):
    """Confirm authorized intent against independently observed current inputs.

    Geometry, braking lead, passenger stops, and live station position are also
    checked locally by the adapter before it sends a simulator update.
    """
    reviewed.lease.verify(clock)
    if (
        current.scope != reviewed.scope
        or current.manifest_sha256 != reviewed.manifest_sha256
        or current.plan.revision != reviewed.route_revision
        or assignment_sha256 != reviewed.assignment_sha256
        or job_fence != reviewed.job_fence
        or candidate_sha256 != reviewed.candidate_sha256
    ):
        raise Rejected("moving_charge_obsolete_plan")
    if reviewed.queue_opens_working_s is None:
        station_available = status.state == "available"
    else:
        station_available = (
            status.state == "occupied"
            and status.available_from_working_s == reviewed.queue_opens_working_s
            and working_s < reviewed.queue_opens_working_s < status.open_until_working_s
        )
    if not (
        reviewed.working_not_before <= current.working_s <= working_s < reviewed.working_expires
        and working_s - current.working_s <= 1.2
        and 0 <= clock.utc.timestamp() - current.observed_real_life_s <= 3
    ):
        raise Rejected("moving_charge_stale_progress")
    if source_state != "running" or current.charging_state != "not_charging":
        raise Rejected("moving_charge_source_unavailable")
    threshold = max(reserve_soc, reviewed.minimum_handoff_soc or reserve_soc)
    if not 0.1 <= reserve_soc <= soc < reviewed.target_soc or soc < threshold:
        raise Rejected("moving_charge_battery_outside_domain")
    if (
        current.lane_id.startswith(":")
        or current.external_index >= reviewed.bay_external_index
        or (
            reviewed.detour_sha256 is None
            and reviewed.bay_external_index >= len(current.plan.edges) - 1
        )
    ):
        raise Rejected("moving_charge_commit_point_passed")
    if not (
        status.bay_id == reviewed.bay_id
        and status.scenario_id == reviewed.station_scenario_id
        and status.revision == reviewed.station_revision
        and station_available
        and status.offered_power_w is not None
        and status.observed_working_s == current.working_s
        and 0
        <= clock.utc.timestamp() - status.received_real_life_s
        <= clock.utc.timestamp() - status.observed_real_life_s
        <= 3
        and working_s < status.open_until_working_s
    ):
        raise Rejected("moving_charge_bay_unavailable")
