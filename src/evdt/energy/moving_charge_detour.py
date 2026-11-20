"""Compose a proposed detour with a verified return to the registered route."""

from dataclasses import dataclass, replace
from heapq import heappop, heappush

from .route_aware.context import SourcePlan
from .route_aware.sumo_routes import Network, expand_route
from .route_aware.validation import integer, number, sequence, text


@dataclass(frozen=True)
class Detour:
    branch_index: int
    rejoin_index: int
    edges: tuple[str, ...]
    bay_edge_index: int

    def __post_init__(self) -> None:
        integer(self.branch_index, "branch road occurrence", 0, 9999)
        integer(self.rejoin_index, "rejoin road occurrence", self.branch_index + 1, 9999)
        sequence(self.edges, "detour roads", str, 3, 128)
        for edge in self.edges:
            text(edge, "detour road")
        integer(self.bay_edge_index, "bay road on detour", 1, len(self.edges) - 2)


@dataclass(frozen=True)
class ProposedRoute:
    plan: SourcePlan
    bay_external_index: int
    retained_occurrences: tuple[tuple[int, int], ...]


def detour_indices(
    edges: tuple[str, ...], detour: Detour
) -> tuple[tuple[str, ...], dict[int, int]]:
    before, after = detour.branch_index, detour.rejoin_index
    if not before < after < len(edges) or (detour.edges[0], detour.edges[-1]) != (
        edges[before],
        edges[after],
    ):
        raise ValueError("Detour boundary roads differ from the registered occurrences")
    shift = len(detour.edges) - (after - before + 1)
    retained = {
        **{index: index for index in range(before + 1)},
        **{index: index + shift for index in range(after, len(edges))},
    }
    proposed = (*edges[:before], *detour.edges, *edges[after + 1 :])
    return proposed, retained


def find_detour(
    network: Network,
    source: SourcePlan,
    *,
    branch_index: int,
    rejoin_index: int,
    branch_lane_id: str,
    rejoin_lane_id: str,
    bay_lane_id: str,
    max_visited_lanes: int,
    max_path_working_s: float,
) -> Detour:
    """Find a connected, directed lane path through a specified bay.

    Cost is travel at lane limits. Full energy, stop, and timing validation is
    still required. This search does not assume an unmodeled lane change.
    """
    integer(branch_index, "branch occurrence", 0, len(source.edges) - 1)
    integer(rejoin_index, "rejoin occurrence", branch_index + 1, len(source.edges) - 1)
    integer(max_visited_lanes, "detour search budget", 1, 50_000)
    number(max_path_working_s, "detour search time horizon", 1, 3_600)
    if source.network_sha256 != network.sha256:
        raise ValueError("Detour inputs refer to a different road network")
    for identity, edge in (
        (branch_lane_id, source.edges[branch_index]),
        (rejoin_lane_id, source.edges[rejoin_index]),
        (bay_lane_id, None),
    ):
        lane = network.lanes.get(identity)
        if lane is None or lane.internal or (edge is not None and lane.edge_id != edge):
            raise ValueError("Detour endpoints and bay require directed bus lanes")
    if bay_lane_id in (branch_lane_id, rejoin_lane_id):
        raise ValueError("A detour needs a bay between the branch and rejoin")
    if any(branch_index < stop.external_index < rejoin_index for stop in source.remaining_stops):
        raise ValueError("Detour would bypass a required stop")

    start = (branch_lane_id, False)
    finish = (rejoin_lane_id, True)
    queue = [(0.0, start)]
    best = {start: 0.0}
    previous = {start: None}
    settled = 0
    while queue:
        cost, state = heappop(queue)
        if cost != best[state]:
            continue
        if settled == max_visited_lanes:
            raise ValueError("detour_lane_budget_exhausted")
        settled += 1
        if state == finish:
            path = []
            current = state
            while current is not None:
                lane = network.lanes[current[0]]
                if not lane.internal:
                    path.append((lane.edge_id, lane.lane_id))
                current = previous[current]
            path.reverse()
            if len(path) > 128:
                raise ValueError("detour_road_budget_exhausted")
            identities = [lane for _, lane in path]
            return Detour(
                branch_index,
                rejoin_index,
                tuple(edge for edge, _ in path),
                identities.index(bay_lane_id),
            )
        lane_id, visited = state
        for link in network.connections.get(lane_id, ()):
            # Returning via the rejoin lane is valid only after reaching the bay.
            if link == rejoin_lane_id and not visited:
                continue
            lane = network.lanes[link]
            target = (link, visited or link == bay_lane_id)
            next_cost = cost + lane.length_m / lane.speed_limit_m_s
            if next_cost <= max_path_working_s and next_cost < best.get(target, float("inf")):
                best[target] = next_cost
                previous[target] = state
                heappush(queue, (next_cost, target))
    raise ValueError("no_detour_within_lane_and_time_domain")


def splice_detour(
    network: Network,
    source: SourcePlan,
    detour: Detour,
    *,
    source_external_index: int,
    source_lane_id: str,
    bay_lane_id: str,
    immutable_occurrences: tuple[int, ...] = (),
) -> ProposedRoute:
    """Bind the detour to ordered visits, then check actual SUMO lane links.

    Both endpoints refer to occurrences of roads on the source plan. Required
    stops remain on the retained visits. A path that would remove a stop is
    excluded even if a road with the same edge ID exists somewhere in its detour.
    """
    if source.network_sha256 != network.sha256:
        raise ValueError("The detour requires the registered network")
    integer(source_external_index, "source road occurrence", 0, len(source.edges) - 1)
    if not source_external_index <= detour.branch_index < detour.rejoin_index < len(source.edges):
        raise ValueError("Detour must branch ahead and rejoin the registered route")
    before, after = detour.branch_index, detour.rejoin_index
    edges, retained = detour_indices(source.edges, detour)
    if any(before < stop.external_index < after for stop in source.remaining_stops):
        raise ValueError("Detour would bypass a required stop")
    for index in immutable_occurrences:
        integer(index, "restricted road occurrence", 0, len(source.edges) - 1)
    if any(before < index < after for index in immutable_occurrences):
        raise ValueError("Detour would replace an occurrence with a binding route restriction")
    bay_lane = network.lanes.get(bay_lane_id)
    if (
        bay_lane is None
        or bay_lane.internal
        or bay_lane.edge_id != detour.edges[detour.bay_edge_index]
    ):
        raise ValueError("Detour does not reach the reviewed bay lane")
    first_visit = network.lanes.get(source_lane_id)
    if (
        first_visit is None
        or first_visit.internal
        or first_visit.edge_id != source.edges[source_external_index]
    ):
        raise ValueError("Confirmed external vehicle lane required before evaluating a detour")
    stops = tuple(
        replace(stop, external_index=retained[stop.external_index])
        for stop in source.remaining_stops
    )
    proposed = SourcePlan(source.network_sha256, edges, stops)
    bay_index = before + detour.bay_edge_index
    required_lanes = {bay_index - source_external_index: bay_lane_id}
    for stop in stops:
        index = stop.external_index - source_external_index
        if index < 0:
            raise ValueError("Remaining stop lies behind the vehicle")
        existing = required_lanes.setdefault(index, stop.lane_id)
        if existing != stop.lane_id:
            raise ValueError("The proposed route needs incompatible lanes on one road visit")
    expand_route(
        network,
        edges[source_external_index:],
        revision=proposed.revision,
        required_lanes=required_lanes,
        entry_lanes={0: source_lane_id},
    )
    return ProposedRoute(proposed, bay_index, tuple(retained.items()))
