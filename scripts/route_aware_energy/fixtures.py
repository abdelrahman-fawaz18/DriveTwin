"""Declared small inputs for operational checks, separate from accuracy studies."""

import json
import time
from dataclasses import replace
from pathlib import Path

from evdt.energy.route_aware import CONTRACT_VERSION
from evdt.energy.route_aware.assignment import Assignment
from evdt.energy.route_aware.contracts import (
    Conditions,
    LiveState,
    Origin,
    RoadPoint,
    RouteContext,
    RouteOccurrence,
    SourceScope,
    VehicleProfile,
)
from evdt.energy.route_aware.policy import ForecastPolicy
from evdt.energy.route_aware.wire import ForecastRequest, decode_record, fingerprint

ROOT = Path(__file__).resolve().parents[2]


def development_request(run, *, asset="bus-001", working_s=0, now=None):
    now = time.time() if now is None else now
    origin = Origin(
        "synthetic", "DriveTwin functional fixture", "flat 1500-meter road; declared 8 kW equipment"
    )
    vehicle = decode_record(
        VehicleProfile,
        json.loads((ROOT / "qualification/energy/route-aware/vehicle-profile.json").read_bytes()),
    )
    route = RouteContext(
        "a" * 64,
        "functional-road-1",
        "local-east-north-meters",
        "synthetic-height",
        origin,
        (
            RouteOccurrence(
                0,
                "road",
                "road_0",
                (RoadPoint(0, 0, 0), RoadPoint(1500, 0, 0)),
                10,
                "urban",
                "surface",
                "synthetic",
            ),
        ),
    )
    state = LiveState(
        SourceScope(run, asset, "sumo-adapter", 1, "00000000-0000-0000-0000-000000000001"),
        "00000000-0000-0000-0000-000000000002",
        "b" * 64,
        working_s,
        now,
        now,
        "running",
        route.revision,
        0,
        0,
        0.8,
        0,
        8000,
        "not_charging",
    )
    request = ForecastRequest(
        CONTRACT_VERSION,
        1,
        now,
        "qualification",
        state,
        route,
        vehicle,
        (Conditions(origin, now, 0, 14_400, auxiliary_power_w=8000),),
        ForecastPolicy(),
    )
    assignment = Assignment(
        "route-energy-assignment/2.0.0",
        run,
        asset,
        state.scope.source_id,
        1,
        True,
        route.network_sha256,
        "c" * 64,
        vehicle,
        request.conditions,
        request.policy,
    )
    return assignment, replace(request, assignment_sha256=fingerprint(assignment))
