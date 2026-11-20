"""Bind approved station bays to an authenticated moving-bus forecast request."""

from dataclasses import dataclass, field, replace

from .moving_charge_candidate import OnRouteStop, VisitPolicy, approach_request
from .moving_charge_detour import Detour, detour_indices, splice_detour
from .moving_charge_scenario import StationScenario
from .moving_charge_station import StationBay
from .moving_charge_worker import JOB_VERSION, PlanningRequest, StationOffer
from .route_aware.admission import PreparedPlan
from .route_aware.context import SourceContext
from .route_aware.geometry import reference_length
from .route_aware.validation import choice, digest, integer, number, sequence, text
from .route_aware.wire import ForecastRequest, fingerprint


@dataclass(frozen=True)
class AssignedBay:
    bay: StationBay
    external_index: int
    detour: Detour | None = field(default=None, metadata={"omit_none_on_wire": True})

    def __post_init__(self) -> None:
        if not isinstance(self.bay, StationBay):
            raise ValueError("Assigned stop requires a reviewed bay")
        integer(self.external_index, "station road occurrence", 0, 9999)
        if self.detour is not None and (
            not isinstance(self.detour, Detour) or self.detour.branch_index != self.external_index
        ):
            raise ValueError("Detour station position must refer to its original branch")

    @property
    def detour_bay_index(self) -> int:
        return (
            self.external_index + self.detour.bay_edge_index if self.detour else self.external_index
        )

    def has_passed(self, context: SourceContext) -> bool:
        if self.detour is not None:
            return context.external_index >= self.external_index
        return context.external_index > self.external_index or (
            context.external_index == self.external_index
            and context.lane_offset_m >= self.bay.end_offset_m
        )


@dataclass(frozen=True)
class StationAssignment:
    version: str
    run_id: str
    asset_id: str
    network_sha256: str
    baseline_edges: tuple[str, ...]
    bays: tuple[AssignedBay, ...]
    scenario: StationScenario
    policy: VisitPolicy
    target_soc_levels: tuple[float, ...]
    max_candidate_jobs: int
    budget_real_life_s: float

    def __post_init__(self) -> None:
        choice(self.version, "station assignment version", ("moving-charge-assignment/1.0.0",))
        text(self.run_id, "assigned run")
        text(self.asset_id, "assigned asset")
        digest(self.network_sha256, "assigned road network")
        sequence(self.baseline_edges, "assigned road order", str, 1, 5000)
        for edge in self.baseline_edges:
            text(edge, "assigned road")
        sequence(self.bays, "assigned bays", AssignedBay, 1, 32)
        if not isinstance(self.scenario, StationScenario) or self.scenario.run_id != self.run_id:
            raise ValueError("Station scenario belongs to a different run")
        if not isinstance(self.policy, VisitPolicy):
            raise ValueError("Station assignment requires a visit policy")
        bay_ids = {entry.bay.bay_id for entry in self.bays}
        if len(bay_ids) != len(self.bays) or bay_ids != {
            row.bay_id for row in self.scenario.schedules
        }:
            raise ValueError("Station availability must cover the assigned bays exactly")
        for entry in self.bays:
            integer(
                entry.external_index, "assigned bay occurrence", 0, len(self.baseline_edges) - 1
            )
            if entry.bay.network_sha256 != self.network_sha256:
                raise ValueError("Assigned bay belongs to a different network")
            if entry.detour is not None:
                detour_indices(self.baseline_edges, entry.detour)
        if (
            not isinstance(self.target_soc_levels, tuple)
            or not 1 <= len(self.target_soc_levels) <= 16
        ):
            raise ValueError("Station assignment needs a bounded target list")
        for target in self.target_soc_levels:
            number(target, "assigned charge target", 0, 1)
        if tuple(sorted(set(self.target_soc_levels))) != self.target_soc_levels:
            raise ValueError("Assigned charge targets must be unique and ordered")
        number(self.budget_real_life_s, "station worker real-life budget", 0.01, 120)
        integer(self.max_candidate_jobs, "station worker job budget", 1, 256)

    def prepare_job(
        self,
        request: ForecastRequest,
        context: SourceContext,
        prepared: PreparedPlan,
        *,
        assignment_sha256: str,
        now_real_life_s: float,
        prepare_alternate=None,
        network=None,
    ) -> PlanningRequest:
        if digest(assignment_sha256, "approved station assignment") != fingerprint(self):
            raise ValueError("Station assignment differs from the reviewed input")
        prepared.validate(context.plan)
        if (
            (request.state.scope.run_id, request.state.scope.asset_id)
            != (self.run_id, self.asset_id)
            or request.state.scope != context.scope
            or request.state.working_s != context.working_s
            or request.route != prepared.route
            or request.route.network_sha256 != self.network_sha256
            or context.plan.edges != self.baseline_edges
        ):
            raise ValueError("Station assignment differs from the observed bus and route")
        if context.external_index < prepared.first_external_index:
            raise ValueError("Forecast preparation excludes the current bus occurrence")
        observed_road = request.route.occurrences[request.state.occurrence_index]
        if (
            context.lane_id not in (observed_road.lane_id, *observed_road.alternative_lane_ids)
            or abs(context.lane_offset_m - request.state.lane_offset_m) > 1e-6
            or context.charging_state != request.state.charging_state
        ):
            raise ValueError("Prepared charge job does not match observed lane progress")
        states = {
            state.bay_id: state
            for state in self.scenario.observe(
                run_id=self.run_id,
                working_s=request.state.working_s,
                observed_real_life_s=request.state.observed_real_life_s,
                received_real_life_s=request.state.received_real_life_s,
                expected_sha256=self.scenario.sha256,
            )
        }
        offers = []
        for entry in self.bays:
            if entry.external_index < context.external_index:
                continue
            if entry.detour is not None:
                if (
                    prepare_alternate is None
                    or network is None
                    or request.scenario is not None
                    or entry.has_passed(context)
                    or context.lane_id.startswith(":")
                    or request.state.current_stop is not None
                ):
                    continue
                try:
                    proposed = splice_detour(
                        network,
                        context.plan,
                        entry.detour,
                        source_external_index=context.external_index,
                        source_lane_id=context.lane_id,
                        bay_lane_id=entry.bay.lane_id,
                    )
                    alternate = prepare_alternate(
                        replace(context, plan=proposed.plan),
                        required_visit_lanes={proposed.bay_external_index: entry.bay.lane_id},
                    )
                    visit = alternate.occurrence_for(proposed.bay_external_index)
                    offer = StationOffer(
                        OnRouteStop(entry.bay, visit, entry.bay.end_offset_m),
                        states[entry.bay.bay_id],
                        alternate.route,
                    )
                    approach_request(offer.driving_request(request), offer.stop)
                except ValueError:
                    continue
                offers.append(offer)
                continue
            index = prepared.occurrence_for(entry.external_index)
            road = request.route.occurrences[index]
            bay = entry.bay
            if (
                index < request.state.occurrence_index
                or (
                    index == request.state.occurrence_index
                    and bay.end_offset_m <= request.state.lane_offset_m
                )
                or road.lane_id != bay.lane_id
                or road.geometry_basis != "lane_centerline"
                or bay.end_offset_m >= reference_length(road)
                or index == len(request.route.occurrences) - 1
            ):
                continue
            offers.append(
                StationOffer(OnRouteStop(bay, index, bay.end_offset_m), states[bay.bay_id])
            )
        return PlanningRequest(
            JOB_VERSION,
            request,
            self.scenario.scenario_id,
            tuple(offers),
            self.policy,
            self.target_soc_levels,
            now_real_life_s,
            self.max_candidate_jobs,
            self.budget_real_life_s,
        )
