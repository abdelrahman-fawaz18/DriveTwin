"""Resolve a newly admitted route plan using approved static road inputs."""

from collections.abc import Callable, Mapping
from dataclasses import replace

from .admission import InputUnavailable, PreparedPlan
from .context import SourcePlan
from .contracts import Origin, PlannedStop, RouteOccurrence
from .sumo_routes import Network, expand_route


def prepare_plan(
    network: Network,
    plan: SourcePlan,
    *,
    enrich: Callable[[RouteOccurrence], RouteOccurrence],
    vertical_datum: str,
    elevation_origin: Origin,
    structures: Mapping[str, str] | None = None,
    current_lanes: Mapping[int, str] | None = None,
    current_junction_lane: str | None = None,
    first_external_index: int = 0,
    required_visit_lanes: Mapping[int, str] | None = None,
) -> PreparedPlan:
    """No live simulator queries or future outcomes enter route preparation.

    The trusted host supplies the elevation function and approved static network.
    It may cache by plan/network/elevation processing fingerprints. Missing
    structure heights and unsupported lanes must remain explicit failures.
    """
    if network.sha256 != plan.network_sha256:
        raise InputUnavailable("unassigned_network")
    if type(first_external_index) is not int or not 0 <= first_external_index < len(plan.edges):
        raise InputUnavailable("route_suffix_outside_plan")
    stops: dict[int, tuple[PlannedStop, ...]] = {}
    lanes = {
        index - first_external_index: lane for index, lane in (required_visit_lanes or {}).items()
    }
    for stop in plan.remaining_stops:
        index = stop.external_index - first_external_index
        if index < 0:
            raise InputUnavailable("passed_stop_without_update")
        if index in lanes and lanes[index] != stop.lane_id:
            raise InputUnavailable("multiple_stop_lanes_on_one_occurrence")
        lanes[index] = stop.lane_id
        stops[index] = (
            *stops.get(index, ()),
            PlannedStop(stop.stop_id, stop.offset_m, stop.dwell_working_s),
        )
    expanded = expand_route(
        network,
        plan.edges[first_external_index:],
        revision=plan.revision,
        stops_by_external_index=stops,
        required_lanes=lanes,
        entry_lanes={i - first_external_index: lane for i, lane in (current_lanes or {}).items()},
        current_junction_lane=current_junction_lane,
        structures=structures,
    )
    occurrences = []
    for original in expanded.context.occurrences:
        enriched = enrich(original)
        # Elevation processing cannot alter road identity, limits, or stop assignments.
        for field in (
            "index",
            "edge_id",
            "lane_id",
            "speed_limit_m_s",
            "road_class",
            "structure",
            "stops",
            "reference_length_m",
            "geometry_basis",
            "alternative_lane_ids",
        ):
            if getattr(original, field) != getattr(enriched, field):
                raise InputUnavailable("elevation_changed_route_semantics")
        if enriched.elevation_quality == "missing":
            raise InputUnavailable("missing_route_elevation")
        occurrences.append(enriched)
    route = replace(
        expanded.context,
        occurrences=tuple(occurrences),
        vertical_datum=vertical_datum,
        elevation_origin=elevation_origin,
    )
    prepared = PreparedPlan(
        plan.revision, route, expanded.external_occurrences, first_external_index
    )
    prepared.validate(plan)
    return prepared
