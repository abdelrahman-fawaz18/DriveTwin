"""Propose a bus detour around confirmed closed directed SUMO edges."""

import argparse
import heapq
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evdt.energy.route_aware.context import SourceContext, parse_context  # noqa: E402
from evdt.energy.route_aware.contracts import PlannedStop  # noqa: E402
from evdt.energy.route_aware.sumo_routes import Network, expand_route, read_network  # noqa: E402


def shortest_leg(
    network: Network,
    start: str,
    targets: set[str],
    blocked: set[str],
    *,
    require_travel: bool,
) -> tuple[str, ...]:
    initial = (start, False)
    frontier = [(0.0, initial)]
    costs = {initial: 0.0}
    parents: dict[tuple[str, bool], tuple[str, bool]] = {}
    while frontier:
        cost, state = heapq.heappop(frontier)
        if cost != costs[state]:
            continue
        lane_id, traveled = state
        if lane_id in targets and (traveled or not require_travel):
            path = [lane_id]
            while state in parents:
                state = parents[state]
                path.append(state[0])
            return tuple(reversed(path))
        for next_id in network.connections.get(lane_id, ()):
            lane = network.lanes[next_id]
            if lane.edge_id in blocked:
                continue
            following = (next_id, True)
            new_cost = cost + lane.length_m / lane.speed_limit_m_s
            if new_cost < costs.get(following, float("inf")):
                costs[following] = new_cost
                parents[following] = state
                heapq.heappush(frontier, (new_cost, following))
    raise ValueError("No bus-connected detour preserves the required stop or destination")


def propose(network: Network, context: SourceContext, blocked: set[str]) -> dict:
    plan = context.plan
    if network.sha256 != plan.network_sha256:
        raise ValueError("Source plan and network fingerprints differ")
    roads = {lane.edge_id for lane in network.lanes.values() if not lane.internal}
    if not blocked or not blocked <= roads:
        raise ValueError("Closures must name known directed bus roads")
    remaining = plan.edges[context.external_index :]
    if not blocked.intersection(remaining):
        raise ValueError("Confirmed closures do not intersect the remaining route")
    current_lane = network.lanes.get(context.lane_id)
    if not current_lane or current_lane.internal or current_lane.edge_id != remaining[0]:
        raise ValueError("A valid current external lane is required")
    if current_lane.edge_id in blocked:
        raise ValueError("The bus is already on a closed road")

    lane_id = current_lane.lane_id
    selected = [current_lane.edge_id]
    planned_stops: dict[int, tuple[PlannedStop, ...]] = {}
    lanes = {0: lane_id}
    stop_records = []
    prior_occurrence = context.external_index

    def travel(targets: set[str], original_occurrence: int) -> None:
        nonlocal lane_id, prior_occurrence
        path = shortest_leg(
            network,
            lane_id,
            targets,
            blocked,
            require_travel=lane_id in targets and original_occurrence != prior_occurrence,
        )
        for candidate in path[1:]:
            lane = network.lanes[candidate]
            if not lane.internal:
                selected.append(lane.edge_id)
        lane_id, prior_occurrence = path[-1], original_occurrence

    for stop in plan.remaining_stops:
        target = network.lanes.get(stop.lane_id)
        if not target or target.edge_id != plan.edges[stop.external_index]:
            raise ValueError("A required stop has no matching road lane")
        if target.edge_id in blocked:
            raise ValueError("A required stop is on a closed road")
        if stop.external_index == context.external_index and stop.offset_m < context.lane_offset_m:
            raise ValueError("A required stop is behind the current position")
        travel({stop.lane_id}, stop.external_index)
        index = len(selected) - 1
        lanes[index] = lane_id
        dwell = (
            context.remaining_dwell_working_s
            if context.current_stop_id == stop.stop_id
            else stop.dwell_working_s
        )
        assert dwell is not None
        planned_stops[index] = (
            *planned_stops.get(index, ()),
            PlannedStop(stop.stop_id, stop.offset_m, dwell),
        )
        stop_records.append(
            {
                "stop_id": stop.stop_id,
                "external_occurrence_index": index,
                "lane_id": stop.lane_id,
                "offset_m": stop.offset_m,
                "dwell_working_s": dwell,
            }
        )
    destination = plan.edges[-1]
    if destination in blocked:
        raise ValueError("The assigned destination is on a closed road")
    travel(
        {lane.lane_id for lane in network.lanes.values() if lane.edge_id == destination},
        len(plan.edges) - 1,
    )
    expand_route(
        network,
        tuple(selected),
        revision="proposed-detour",
        stops_by_external_index=planned_stops,
        required_lanes=lanes,
    )
    return {
        "status": "proposed",
        "network_sha256": network.sha256,
        "based_on_route_revision": plan.revision,
        "closed_edges": sorted(blocked),
        "route_edges": selected,
        "stops": stop_records,
        "requires_confirmed_source_assignment": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--source-context", type=Path, required=True)
    parser.add_argument("--closed-edge", action="append", required=True)
    args = parser.parse_args()
    try:
        with (
            args.network.open("rb") as network_file,
            args.source_context.open("rb") as context_file,
        ):
            network = read_network(network_file.read(16 * 1024 * 1024 + 1))
            context = parse_context(context_file.read(65_537))
        print(json.dumps(propose(network, context, set(args.closed_edge)), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
