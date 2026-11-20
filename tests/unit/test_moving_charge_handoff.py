"""A selected visit expires independently of advisory calculation history."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evdt.command_timing import ClockLease, HostClock
from evdt.contract_profile import Rejected
from evdt.energy.moving_charge_handoff import ReviewedStop, revalidate
from evdt.energy.moving_charge_station import STATUS_VERSION, StationStatus
from evdt.energy.route_aware.context import SourceContext, SourcePlan, SourceStop
from evdt.energy.route_aware.contracts import SourceScope
from evdt.energy.route_aware.wire import fingerprint
from evdt.moving_charge_api import approach_handoff_floor, context_receipt
from evdt.moving_charge_journal import decode, encode


def test_station_projection_uses_receipt_for_the_exact_registered_context():
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    scope = SourceScope(
        "346d6ee1-ab78-4129-9139-08e032c70546",
        "bus-001",
        "sumo-adapter",
        1,
        "5a576443-bc09-48dc-9435-89c6d93e14d6",
    )
    context = SourceContext(
        "source-route-context/2.0.0",
        scope,
        "b" * 64,
        40,
        now.timestamp() - 0.1,
        SourcePlan("a" * 64, ("a", "b", "c"), ()),
        1,
        "b_0",
        20,
        "not_charging",
        None,
        None,
    )
    conn = Mock()
    receipt = now - timedelta(seconds=0.05)
    conn.execute.return_value = SimpleNamespace(fetchone=lambda: {"received_at": receipt})
    assert context_receipt(conn, context, now.timestamp()) == receipt.timestamp()
    assert conn.execute.call_args.args[1] == (scope.run_id, fingerprint(context))
    for rejection in (
        None,
        {"received_at": now - timedelta(seconds=0.2)},
        {"received_at": now + timedelta(seconds=0.1)},
    ):
        conn.execute.return_value = SimpleNamespace(fetchone=lambda: rejection)
        with pytest.raises(Rejected, match="stale_progress"):
            context_receipt(conn, context, now.timestamp())


def test_handoff_revalidates_scope_plan_battery_station_and_both_clocks():
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    host = HostClock("7e35100b-3008-440d-8ca5-51a4acf0c95d", 1_000_000_000, now)
    scope = SourceScope(
        "346d6ee1-ab78-4129-9139-08e032c70546",
        "bus-001",
        "sumo-adapter",
        1,
        "5a576443-bc09-48dc-9435-89c6d93e14d6",
    )
    plan = SourcePlan("a" * 64, ("a", "b", "c", "d"), (SourceStop("s1", 3, "d_0", 40, 20),))
    progress = SourceContext(
        "source-route-context/2.0.0",
        scope,
        "b" * 64,
        40,
        now.timestamp() - 0.1,
        plan,
        1,
        "b_0",
        20,
        "not_charging",
        None,
        None,
    )
    status = StationStatus(
        STATUS_VERSION,
        "bay",
        "scenario",
        8,
        now.timestamp() - 0.1,
        now.timestamp(),
        40,
        "available",
        170,
        40,
        150_000,
    )
    approval = ReviewedStop(
        5,
        "c" * 64,
        "d" * 64,
        scope,
        progress.manifest_sha256,
        plan.revision,
        "bay",
        2,
        status.scenario_id,
        8,
        0.14,
        40,
        44,
        ClockLease.until(host, now + timedelta(seconds=3)),
    )
    arguments = {
        "clock": host,
        "working_s": 40.2,
        "assignment_sha256": approval.assignment_sha256,
        "job_fence": 5,
        "candidate_sha256": approval.candidate_sha256,
        "source_state": "running",
        "reserve_soc": 0.1,
        "soc": 0.112,
    }
    revalidate(approval, progress, status, **arguments)
    original = encode(approval)
    assert "detour_sha256" not in original
    assert "minimum_handoff_soc" not in original
    assert decode(original) == approval
    assert "queue_opens_working_s" not in original
    queued = replace(approval, queue_opens_working_s=90)
    occupied = replace(status, state="occupied", available_from_working_s=90)
    revalidate(queued, progress, occupied, **arguments)
    assert decode(encode(queued)) == queued
    for changed in (status, replace(occupied, available_from_working_s=91)):
        with pytest.raises(Rejected, match="bay_unavailable"):
            revalidate(queued, progress, changed, **arguments)
    with pytest.raises(ValueError, match="original on-route"):
        replace(queued, detour_sha256="a" * 64)
    detour = replace(approval, detour_sha256="e" * 64)
    assert decode(encode(detour)) == detour
    guarded = replace(approval, minimum_handoff_soc=0.111)
    assert decode(encode(guarded)) == guarded
    revalidate(guarded, progress, status, **arguments)
    with pytest.raises(Rejected, match="battery_outside_domain"):
        revalidate(guarded, progress, status, **{**arguments, "soc": 0.109})

    for reason, current, station, changes in (
        ("obsolete_plan", progress, status, {"job_fence": 6}),
        ("obsolete_plan", replace(progress, plan=replace(plan, remaining_stops=())), status, {}),
        ("source_unavailable", progress, status, {"source_state": "paused"}),
        ("commit_point_passed", replace(progress, external_index=2, lane_id="c_0"), status, {}),
        ("battery_outside_domain", progress, status, {"soc": 0.099}),
        ("bay_unavailable", progress, replace(status, revision=9), {}),
        ("bay_unavailable", progress, replace(status, scenario_id="other"), {}),
        ("stale_progress", progress, status, {"working_s": 44}),
        ("elapsed_deadline", progress, status, {"clock": replace(host, elapsed_ns=4_000_000_000)}),
    ):
        with pytest.raises(Rejected, match=reason):
            revalidate(approval, current, station, **{**arguments, **changes})


def test_approach_handoff_floor_accounts_for_wait_and_planning_margin():
    candidate = {
        "approach": {
            "initial": {"soc": 0.13},
            "forecast": {"summary": {"minimum_soc": 0.115}},
        },
        "transfer": {"plug_in_soc": 0.11},
    }
    assert approach_handoff_floor(candidate, 0.1025) == pytest.approx(0.1225)
    candidate["transfer"]["plug_in_soc"] = 0.12
    assert approach_handoff_floor(candidate, 0.1025) == pytest.approx(0.1175)


def test_executor_reports_approach_only_after_reading_back_the_scheduled_stop():
    from evdt.monitoring.moving_charge_executor import MovingChargeExecutor

    adapter = MovingChargeExecutor.__new__(MovingChargeExecutor)
    adapter.asset = "bus-001"
    bay = SimpleNamespace(bay_id="bay", lane_id="bay_0")
    adapter.active = (bay, 0.12)
    adapter.bay_id = bay.bay_id
    adapter.command_id = None
    adapter.target_reached = False
    adapter.effect_state = None
    adapter.charged_wh = 0
    adapter.queue_opens_working_s = None
    vehicle = Mock()
    vehicle.getParameter.return_value = "0"
    vehicle.getStops.return_value = ()
    connection = SimpleNamespace(vehicle=vehicle)
    with pytest.raises(RuntimeError, match="Scheduled charging stop changed"):
        adapter.observe(connection, 5.2, 0.113)
    assert adapter.observation() == {}
    vehicle.getStops.return_value = (SimpleNamespace(stoppingPlaceID="bay", lane="bay_0"),)
    assert adapter.observe(connection, 5.2, 0.113) == 0
    assert adapter.observation() == {
        "charging_effect": "approaching",
        "charging_bay": "bay",
        "charging_effect_time": "5.2",
        "charging_delivered_j": "0",
    }


def test_detour_updates_only_the_future_route_and_checks_stops():
    from evdt.energy.moving_charge_detour import Detour
    from evdt.monitoring.moving_charge_executor import MovingChargeExecutor
    from evdt.monitoring.prepared_journey import PreparedJourney

    journey = PreparedJourney.__new__(PreparedJourney)
    journey.spec = {
        "route_edges": ["a", "b", "c", "d", "e"],
        "endpoint": {"external_index": 4, "lane_id": "e_0", "offset_m": 30},
        "stops": [{"external_index": 4, "lane_id": "e_0", "offset_m": 30}],
    }
    journey.charge_stop = None
    adapter = MovingChargeExecutor.__new__(MovingChargeExecutor)
    adapter.asset = "bus-001"
    detour = Detour(2, 3, ("c", "bay", "d"), 1)
    from test_moving_charge import bay

    from evdt.energy.moving_charge_assignment import AssignedBay

    entry = AssignedBay(replace(bay(), lane_id="bay_0"), 2, detour)
    review = SimpleNamespace(detour_sha256=fingerprint(detour))
    context = SimpleNamespace(external_index=1, lane_id="b_0")
    connection = Mock()
    connection.lane.getEdgeID.side_effect = lambda lane: lane.removesuffix("_0")
    stop = SimpleNamespace(
        lane="e_0",
        startPos=25,
        endPos=30,
        stoppingPlaceID="",
        stopFlags=0,
        duration=14400,
        until=-1,
        split="",
        join="",
    )
    connection.vehicle.getStops.return_value = (stop,)
    connection.vehicle.getRoute.return_value = ("a", "b", "c", "bay", "d", "e")
    connection.vehicle.getRouteIndex.return_value = 1

    with pytest.raises(Rejected, match="differs"):
        adapter.apply_detour(
            connection, journey, context, entry, SimpleNamespace(detour_sha256="0" * 64)
        )
    connection.vehicle.setRoute.assert_not_called()
    position, replacement = adapter.apply_detour(connection, journey, context, entry, review)
    connection.vehicle.setRoute.assert_called_once_with("bus-001", ("b", "c", "bay", "d", "e"))
    assert position.external_index == 3
    assert replacement.spec["endpoint"]["external_index"] == 5
    assert journey.spec["endpoint"]["external_index"] == 4
    connection.vehicle.getRouteIndex.return_value = 0
    with pytest.raises(RuntimeError, match="before the next SUMO step"):
        adapter.apply_detour(connection, journey, context, entry, review)


def test_interruption_reads_back_held_bay_and_passenger_suffix_without_advancing():
    from test_moving_charge import bay

    from evdt.energy.moving_charge_assignment import AssignedBay
    from evdt.monitoring.charging_stop import CHARGING_STATION
    from evdt.monitoring.moving_charge_executor import MovingChargeExecutor
    from evdt.monitoring.prepared_journey import PreparedJourney

    station = replace(bay(), lane_id="b_0")
    journey = PreparedJourney.__new__(PreparedJourney)
    journey.charge_stop = AssignedBay(station, 1)
    served = {"external_index": 0, "lane_id": "a_0", "offset_m": 20}
    passenger = {"external_index": 2, "lane_id": "c_0", "offset_m": 30}
    endpoint = {"external_index": 3, "lane_id": "d_0", "offset_m": 40}
    route = ("a", "b", "c", "d")
    journey.spec = {
        "route_edges": route,
        "stops": [served, passenger, endpoint],
        "endpoint": endpoint,
    }

    def stop(lane, end, identifier="", flags=0):
        return SimpleNamespace(
            lane=lane,
            endPos=end,
            startPos=end - 15,
            stoppingPlaceID=identifier,
            duration=300,
            stopFlags=flags,
            until=-1,
            split="",
            join="",
        )

    active = stop("b_0", station.end_offset_m, station.bay_id, CHARGING_STATION)
    planned = (active, stop("c_0", 30), stop("d_0", 40))
    executor = MovingChargeExecutor.__new__(MovingChargeExecutor)
    executor.asset = "bus-001"
    executor.active = (station, 0.14)
    executor.interrupted = True
    executor.events = [{"event": "charge_interrupted", "working_s": 45.2}]
    executor.command_id = "test-authorization"
    executor.charged_wh = 100
    connection = Mock()
    connection.simulation.getTime.return_value = 45.2
    connection.chargingstation.getChargingPower.return_value = 0
    connection.vehicle.getRoute.return_value = route
    connection.vehicle.getStops.return_value = planned
    connection.vehicle.getRouteIndex.return_value = 1
    connection.vehicle.getLaneID.return_value = "b_0"
    connection.vehicle.getLanePosition.return_value = station.end_offset_m
    connection.vehicle.getSpeed.return_value = 0
    connection.vehicle.isStopped.return_value = True
    connection.vehicle.getParameter.return_value = "42_000"

    executor.capture_interruption(connection, journey, 1_260_000_000)
    readback = executor.interruption_snapshot
    assert readback["status"] == "held_at_committed_bay"
    assert readback["route_sha256"] == fingerprint(route)
    assert readback["remaining_passenger_stops"] == (passenger,)
    assert readback["energy_j"] == 151_200_000
    assert readback["delivered_j"] == 360_000
    assert readback["power_w"] == 0

    connection.vehicle.getStops.return_value = (active, planned[1], planned[1])
    executor.capture_interruption(connection, journey, 1_260_000_000)
    assert executor.interruption_snapshot["status"] == "unreconciled"
    connection.vehicle.getStops.return_value = planned
    connection.vehicle.getSpeed.return_value = 2
    executor.capture_interruption(connection, journey, 1_260_000_000)
    assert executor.interruption_snapshot == {
        "status": "unreconciled",
        "reason": "vehicle_not_held_at_committed_bay",
    }
    connection.simulationStep.assert_not_called()
    connection.vehicle.resume.assert_not_called()
