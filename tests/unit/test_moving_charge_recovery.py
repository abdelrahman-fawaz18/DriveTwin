"""Bind a hypothetical replacement job to verified stopped-bus inputs."""

from dataclasses import replace

import pytest
from test_energy_route_wire import request
from test_moving_charge import bay

from evdt.energy.moving_charge_assignment import AssignedBay, StationAssignment
from evdt.energy.moving_charge_candidate import VisitPolicy
from evdt.energy.moving_charge_recovery import HeldChargeStop, HeldDepartureInputs
from evdt.energy.moving_charge_recovery_input import prepare_replacement
from evdt.energy.moving_charge_scenario import BayInterval, BaySchedule, StationScenario
from evdt.energy.route_aware.admission import InputUnavailable, PreparedPlan, TrustedClock
from evdt.energy.route_aware.assignment import Assignment
from evdt.energy.route_aware.context import SourcePlan
from evdt.energy.route_aware.contracts import Curve, CurvePoint, RoadPoint
from evdt.energy.route_aware.wire import fingerprint


@pytest.fixture
def held_case():
    base = request()
    road = base.route.occurrences[0]
    scope = replace(base.state.scope, run_id="00000000-0000-0000-0000-000000000001")
    edges = ("approach", "held", "replacement", "destination")
    plan = SourcePlan(base.route.network_sha256, edges, ())
    suffix = tuple(
        replace(
            road,
            index=index,
            edge_id=edge,
            lane_id=edge + "_0",
            shape=(RoadPoint(100 * index, 0, 10), RoadPoint(100 * (index + 1), 0, 10)),
            stops=(),
        )
        for index, edge in enumerate(edges[1:])
    )
    prepared = PreparedPlan(
        plan.revision,
        replace(base.route, revision=plan.revision, occurrences=suffix),
        (0, 1, 2),
        1,
    )
    original = replace(bay(), lane_id="held_0", network_sha256=plan.network_sha256)
    downstream = replace(original, bay_id="new-bay", lane_id="replacement_0")
    scenario = StationScenario(
        "scenario",
        scope.run_id,
        (
            BaySchedule(original.bay_id, (BayInterval(0, 500, "out_of_order", None),)),
            BaySchedule(downstream.bay_id, (BayInterval(0, 500, "available", downstream.power_w),)),
        ),
    )
    policy = VisitPolicy(
        0,
        8000,
        Curve("1", "W", (CurvePoint(0, 100_000), CurvePoint(1, 100_000)), base.vehicle.origin),
        5,
        "simulated-dc",
        12,
    )
    stations = StationAssignment(
        "moving-charge-assignment/1.0.0",
        scope.run_id,
        scope.asset_id,
        plan.network_sha256,
        edges,
        (AssignedBay(original, 1), AssignedBay(downstream, 2)),
        scenario,
        policy,
        (0.2,),
        8,
        10,
    )
    held = HeldChargeStop(
        scope,
        "00000000-0000-0000-0000-000000000002",
        "d" * 64,
        fingerprint(stations),
        plan.revision,
        "e" * 64,
        original.bay_id,
        1,
        original.lane_id,
        30,
        150,
        0.108,
        99,
        HeldDepartureInputs(7500, 294, ()),
    )
    clock = TrustedClock(scope, "paused", 150, 99.8, 99.9, held.manifest_sha256)
    assignment = Assignment(
        "route-energy-assignment/2.0.0",
        scope.run_id,
        scope.asset_id,
        scope.source_id,
        1,
        True,
        plan.network_sha256,
        "f" * 64,
        base.vehicle,
        base.conditions,
        base.policy,
    )
    return dict(
        held=held,
        clock=clock,
        assignment=assignment,
        stations=stations,
        provider=lambda *_: prepared,
        now=100,
    )


def test_hypothetical_departure_uses_measured_hold_and_only_a_downstream_bay(held_case):
    held = held_case["held"]
    job = prepare_replacement(**held_case)
    state = job.baseline.state
    assert held_case["clock"].state == "paused"
    assert state.wire_sha256 == fingerprint(held)
    assert (state.working_s, state.speed_m_s, state.soc, state.lane_offset_m) == (150, 0, 0.108, 30)
    assert (state.auxiliary_power_w, state.battery_temperature_k) == (7500, 294)
    assert [offer.stop.bay.bay_id for offer in job.offers] == ["new-bay"]


@pytest.mark.parametrize(
    ("input_name", "changes", "reason"),
    (
        ("held", {"departure": None}, "held_charge_source_unavailable"),
        ("clock", {"state": "running"}, "held_charge_source_unavailable"),
        ("clock", {"working_s": 151}, "held_charge_source_unavailable"),
        ("held", {"observed_real_life_s": 24}, "held_charge_source_unavailable"),
        ("held", {"assignment_sha256": "0" * 64}, "held_charge_assignment_changed"),
        ("held", {"route_revision": "0" * 64}, "held_charge_trip_plan_changed"),
        ("held", {"lane_offset_m": 39}, "held_charge_bay_unverified"),
    ),
)
def test_replacement_refuses_inconsistent_or_stale_inputs(held_case, input_name, changes, reason):
    held_case[input_name] = replace(held_case[input_name], **changes)
    with pytest.raises(InputUnavailable, match=reason):
        prepare_replacement(**held_case)
