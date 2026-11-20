"""Bind declared road restrictions to each authenticated remaining-stop plan."""

import math
from dataclasses import replace
from types import SimpleNamespace


def indexed_restrictions(recipe, edge_count):
    zones = recipe.get("speed_zones", [])
    if not isinstance(zones, list) or len(zones) > 100:
        raise ValueError("Invalid geographic speed restrictions")
    result = []
    for zone in zones:
        start, end, factor = (zone[k] for k in ("start_fraction", "end_fraction", "speed_factor"))
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end, factor)):
            raise ValueError("Speed restrictions require finite numeric values")
        if not 0 <= start < end <= 1 or not 0 < factor <= 1:
            raise ValueError("Speed restriction outside its route or speed domain")
        first, last = int(start * edge_count), int(end * edge_count)
        if first == last:
            raise ValueError("Speed restriction covers no road occurrences")
        result.append({"start_index": first, "end_index": last, "speed_factor": factor})
    return result


class RestrictionUpdater:
    """Used only by the trusted runner, which already owns assignment authority.

    The controller retains its restricted role. Each update is append-only and
    exactly bound to the observed plan. The initial sentinel rejects all inputs
    until an authenticated plan has been admitted and its restrictions assigned.
    """

    def __init__(self, base, recipe, edges, provider):
        from evdt.energy.route_aware.scenarios import RouteScenario

        self.edges = tuple(edges)
        self.zones = indexed_restrictions(recipe, len(self.edges))
        if not self.zones:
            raise ValueError("A restriction updater requires localized road conditions")
        self.provider = provider
        scenario = RouteScenario(
            "declared-geographic-speed-restrictions",
            base.conditions[0].origin,
            base.conditions[0].available_real_life_s,
            notes=("Speed restrictions follow assigned road occurrences",),
        )
        self.current = replace(base, scenario=scenario, scenario_route_revision="0" * 64)

    def candidate(self, snapshot):
        """Read only a scoped authenticated energy-input response; do not commit here."""
        from evdt.energy.route_aware.context import SourceContext
        from evdt.energy.route_aware.scenarios import RoadConditions
        from evdt.energy.route_aware.wire import decode_record, fingerprint

        if snapshot["reason"] is not None:
            return None
        current = self.current
        if (snapshot["run_id"], snapshot["asset_id"]) != (
            current.run_id,
            current.asset_id,
        ) or snapshot["assignment_sha256"] != fingerprint(current):
            raise ValueError("Live restriction assignment identity changed unexpectedly")
        context = decode_record(SourceContext, snapshot["context"])
        if (
            (context.scope.run_id, context.scope.asset_id, context.scope.source_id)
            != (current.run_id, current.asset_id, current.source_id)
            or context.plan.network_sha256 != current.network_sha256
            or context.plan.edges != self.edges
        ):
            raise ValueError("Localized restrictions require the original assigned road sequence")
        if context.plan.revision == current.scenario_route_revision:
            return None
        # The assignment indexes the complete geometry. The existing trusted
        # input builder projects those indices to the bus's remaining suffix.
        plan = self.provider(current, SimpleNamespace(plan=context.plan))
        plan.validate(context.plan)
        if plan.first_external_index:
            raise ValueError("Restriction assignment requires the complete ordered route")
        roads = []
        for external, start in enumerate(plan.external_occurrences):
            factors = [
                zone["speed_factor"]
                for zone in self.zones
                if zone["start_index"] <= external < zone["end_index"]
            ]
            if not factors:
                continue
            intervals = tuple(
                replace(c, speed_factor=min(c.speed_factor, *factors)) for c in current.conditions
            )
            end = (
                plan.external_occurrences[external + 1]
                if external + 1 < len(plan.external_occurrences)
                else len(plan.route.occurrences)
            )
            roads.extend(RoadConditions(i, intervals) for i in range(start, end))
        return replace(
            current,
            revision=current.revision + 1,
            scenario=replace(current.scenario, roads=tuple(roads)),
            scenario_route_revision=context.plan.revision,
        )

    def committed(self, candidate):
        """Advance local state only after the database transaction commits."""
        if candidate.revision != self.current.revision + 1:
            raise ValueError("Restriction assignment revision must advance once")
        self.current = candidate
