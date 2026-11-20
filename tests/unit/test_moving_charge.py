"""Check reserve crossings, station offer freshness and stationary battery accounting."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_energy_route_wire import request

from evdt.energy.moving_charge_candidate import (
    OnRouteStop,
    VisitPolicy,
    approach_request,
    departure_request,
    evaluate_candidate,
)
from evdt.energy.moving_charge_leg import calculate_leg
from evdt.energy.moving_charge_search import plan_charge
from evdt.energy.moving_charge_station import (
    STATION_VERSION,
    STATUS_VERSION,
    ChargeWindow,
    StationBay,
    StationStatus,
)
from evdt.energy.moving_charge_transfer import charge_to
from evdt.energy.route_aware.contracts import Curve, CurvePoint, PlannedStop, RoadPoint
from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.wire import canonical_bytes


def bay():
    return StationBay(
        STATION_VERSION,
        "site",
        "bay",
        "simulated-dc",
        15,
        "test-lane",
        20,
        35,
        120_000,
        0.9,
        30,
        "0" * 64,
        "qualified approach and exit",
        "declared location",
    )


def status():
    return StationStatus(
        STATUS_VERSION,
        "bay",
        "scenario",
        2,
        100,
        101,
        50,
        "occupied",
        900,
        200,
        110_000,
    )


def window(value):
    return value.window(
        bay(),
        scenario_id="scenario",
        now_real_life_s=102,
        current_working_s=51,
        arrival_working_s=180,
        status_max_age_real_life_s=5,
    )


def transfer(offer, **changes):
    params = {
        "arrival_working_s": 0.0,
        "arrival_soc": 0.20,
        "reserve_soc": 0.10,
        "target_soc": 0.25,
        "capacity_kwh": 100,
        "equipment_w": 10_000,
        "acceptance_by_soc": Curve(
            "1",
            "W",
            (CurvePoint(0, 120_000), CurvePoint(1, 120_000)),
            request().vehicle.origin,
        ),
        **changes,
    }
    return charge_to(offer, **params)


def test_moving_leg_keeps_forecast_wire_and_finds_the_first_reserve_crossing():
    original = request()
    normal = forecast(original)
    assert normal.summary is not None
    delta = original.state.soc - normal.summary.minimum_soc
    assert delta > 0
    subject = replace(
        original, state=replace(original.state, soc=original.vehicle.reserve_soc + delta / 2)
    )
    result = calculate_leg(subject, checkpoint_occurrences=(0,))
    assert canonical_bytes(result.forecast) == canonical_bytes(forecast(subject))
    assert result.first_below_reserve is not None
    assert result.first_below_reserve.working_s > subject.state.working_s
    assert result.first_below_reserve.soc < subject.vehicle.reserve_soc
    assert result.checkpoints[0].working_s == pytest.approx(
        result.forecast.summary.arrival_working_s
    )
    assert result.checkpoints[0].soc == pytest.approx(result.forecast.summary.arrival_soc)
    already_low = calculate_leg(replace(subject, state=replace(subject.state, soc=0.01)))
    assert already_low.first_below_reserve == already_low.initial


def test_reserve_marker_is_inside_sampled_road_and_legacy_result_still_decodes():
    import json

    from evdt.energy.moving_charge_leg import DrivingLeg
    from evdt.energy.route_aware.wire import decode_record

    original = request()
    occurrence = original.route.occurrences[0]
    start, *_, end = occurrence.shape
    shape = tuple(
        replace(p, latitude_deg=45.0, longitude_deg=-73.0 + (p.x_m - start.x_m) / 100_000)
        for p in occurrence.shape
    )
    route = replace(original.route, occurrences=(replace(occurrence, shape=shape),))
    original = replace(original, route=route)
    delta = original.state.soc - forecast(original).summary.minimum_soc
    subject = replace(
        original, state=replace(original.state, soc=original.vehicle.reserve_soc + delta / 2)
    )
    result = calculate_leg(subject)
    crossing = result.first_below_reserve
    assert crossing is not None and crossing.coordinates is not None
    lon, lat = crossing.coordinates
    assert (
        -73.0 + subject.state.lane_offset_m / 100_000
        < lon
        < -73.0 + (end.x_m - start.x_m) / 100_000
    )
    assert lat == 45.0
    assert canonical_bytes(result.forecast) == canonical_bytes(forecast(subject))
    encoded = json.loads(canonical_bytes(result))
    del encoded["first_below_reserve"]["coordinates"]
    legacy = decode_record(DrivingLeg, encoded)
    assert legacy.first_below_reserve.coordinates is None
    assert json.loads(canonical_bytes(legacy)) == encoded


def test_station_rejects_inconsistent_bay_and_returns_offer_capped_by_status():
    station = bay()
    network = SimpleNamespace(
        sha256=station.network_sha256,
        lanes={"test-lane": SimpleNamespace(internal=False, length_m=50)},
    )
    assert station.access_problem(network, connector="simulated-dc", vehicle_length_m=12) is None
    assert (
        station.access_problem(network, connector="unknown", vehicle_length_m=12)
        == "connector_incompatible"
    )
    assert (
        station.access_problem(network, connector="simulated-dc", vehicle_length_m=18)
        == "insufficient_bay_length"
    )
    assert window(status()) == ChargeWindow(230, 900, 110_000, 0.9)
    with pytest.raises(ValueError, match="stale"):
        window(replace(status(), observed_real_life_s=90))
    with pytest.raises(ValueError, match="no usable offer"):
        window(
            replace(status(), state="unknown", available_from_working_s=None, offered_power_w=None)
        )
    with pytest.raises(ValueError, match="different bay"):
        window(replace(status(), bay_id="other"))


def test_transfer_keeps_energy_balance_when_waiting_and_obeys_access_window():
    offer = ChargeWindow(60, 600, 100_000, 0.9)
    result = transfer(offer)
    net_stored_j = (result.departure_soc - result.arrival_soc) * 360_000_000
    assert net_stored_j == pytest.approx(result.grid_energy_j * 0.9 - result.equipment_energy_j)
    assert result.wait_working_s == 60
    assert result.end_working_s == pytest.approx(292.5)
    with pytest.raises(ValueError, match="below_reserve_while_waiting"):
        transfer(offer, arrival_soc=0.101)
    with pytest.raises(ValueError, match="access_window_ends_before_target"):
        transfer(replace(offer, charge_until_working_s=280))
    with pytest.raises(ValueError, match="insufficient_net_charging_power"):
        transfer(replace(offer, input_power_w=1_000))


def test_taper_crosses_station_limit_and_increases_time_for_the_same_energy():
    offer = ChargeWindow(60, 1000, 100_000, 0.9)
    acceptance = Curve(
        "1",
        "W",
        (CurvePoint(0, 140_000), CurvePoint(0.3, 60_000), CurvePoint(1, 20_000)),
        request().vehicle.origin,
    )
    uncapped = transfer(offer)
    tapered = transfer(offer, acceptance_by_soc=acceptance)
    assert tapered.end_working_s > uncapped.end_working_s
    assert tapered.grid_energy_j > uncapped.grid_energy_j


def test_on_route_candidate_splits_at_the_bay_and_retains_passenger_stops():
    original = request()
    before = replace(original.route.occurrences[0], stops=(PlannedStop("before", 25, 10),))
    after = replace(
        before,
        index=1,
        lane_id="other-lane",
        shape=(RoadPoint(100, 0, 15), RoadPoint(200, 0, 15)),
        stops=(PlannedStop("after", 50, 20),),
    )
    base = replace(original, route=replace(original.route, occurrences=(before, after)))
    station = replace(
        bay(),
        lane_id=before.lane_id,
        network_sha256=base.route.network_sha256,
        start_offset_m=60,
        end_offset_m=75,
    )
    stop = OnRouteStop(station, 0, 70)
    offer = replace(
        status(),
        state="available",
        observed_working_s=base.state.working_s,
        available_from_working_s=base.state.working_s,
    )
    result = evaluate_candidate(
        base,
        stop,
        offer,
        target_soc=0.805,
        policy=VisitPolicy(
            0,
            8000,
            Curve("1", "W", (CurvePoint(0, 100_000), CurvePoint(1, 100_000)), base.vehicle.origin),
            5,
            "simulated-dc",
            12,
        ),
        station_scenario_id="scenario",
        now_real_life_s=102,
    )
    assert result.reason is None
    assert result.transfer is not None
    assert result.onward is not None
    assert result.arrival_working_s > result.transfer.end_working_s
    assert [s.stop_id for s in approach_request(base, stop).route.occurrences[0].stops] == [
        "before"
    ]
    assert result.approach.forecast.summary.ledger.auxiliary_required_j > 0
    assert result.onward.forecast.summary.duration_working_s > 20
    assert result.approach.forecast.route_sha256 != forecast(base).route_sha256
    with pytest.raises(ValueError, match="overlaps"):
        approach_request(
            base,
            replace(stop, offset_m=25, bay=replace(station, start_offset_m=20, end_offset_m=35)),
        )


def test_search_checks_a_later_bay_and_expires_an_incomplete_search():
    original = request()
    start = replace(original.route.occurrences[0], stops=())
    middle = replace(
        start,
        index=1,
        lane_id="middle",
        shape=(RoadPoint(100, 0, 15), RoadPoint(200, 0, 15)),
    )
    end = replace(
        middle,
        index=2,
        lane_id="end",
        shape=(RoadPoint(200, 0, 15), RoadPoint(1200, 0, 65)),
    )
    base = replace(
        original,
        route=replace(original.route, occurrences=(start, middle, end)),
        state=replace(original.state, soc=0.104),
        conditions=(replace(original.conditions[0], auxiliary_power_w=8_000),),
    )
    early = replace(
        bay(),
        lane_id=start.lane_id,
        network_sha256=base.route.network_sha256,
        start_offset_m=55,
        end_offset_m=70,
        bay_id="early",
    )
    later = replace(early, bay_id="later", lane_id=middle.lane_id, power_w=160_000)
    early_offer = replace(status(), bay_id=early.bay_id, observed_working_s=30)
    later_offer = replace(
        early_offer,
        bay_id=later.bay_id,
        state="available",
        available_from_working_s=30,
        offered_power_w=160_000,
    )
    offers = ((OnRouteStop(early, 0, 66), early_offer), (OnRouteStop(later, 1, 66), later_offer))
    policy = VisitPolicy(
        0,
        8_000,
        Curve("1", "W", (CurvePoint(0, 200_000), CurvePoint(1, 200_000)), base.vehicle.origin),
        5,
        "simulated-dc",
        12,
    )

    def search(jobs):
        return plan_charge(
            base,
            offers,
            policy=policy,
            target_soc_levels=(0.115, 0.125),
            station_scenario_id="scenario",
            now_real_life_s=102,
            max_candidate_jobs=jobs,
            budget_real_life_s=10,
        )

    result = search(6)
    assert result.status == "provisional_plan", result.exclusions
    assert result.baseline.first_below_reserve is not None
    assert result.preferred.bay_id == "later"
    assert result.preferred.transfer.departure_soc == 0.115
    assert result.fallback.bay_id == "early"
    partial = search(5)
    assert partial.status == "search_budget_exhausted"
    assert partial.preferred is None

    from evdt.energy.moving_charge_worker import (
        JOB_VERSION,
        PlanningRequest,
        StationOffer,
        calculate,
        parse_result,
        validate_planning_result,
    )

    job = PlanningRequest(
        JOB_VERSION,
        base,
        "scenario",
        tuple(StationOffer(*pair) for pair in offers),
        policy,
        (0.115, 0.125),
        102,
        6,
        10,
    )
    worker_result = parse_result(canonical_bytes(calculate(job)))
    validate_planning_result(job, worker_result)
    selected = worker_result.decision.preferred
    assert selected is not None and selected.transfer is not None
    changed = replace(selected, transfer=replace(selected.transfer, grid_energy_j=0))
    with pytest.raises(ValueError, match="transfer differs"):
        validate_planning_result(
            job, replace(worker_result, decision=replace(worker_result.decision, preferred=changed))
        )


def test_detour_keeps_required_stop_occurrences_and_rejects_missing_lane_link():
    from evdt.energy.moving_charge_detour import Detour, find_detour, splice_detour
    from evdt.energy.route_aware.context import SourcePlan, SourceStop
    from evdt.energy.route_aware.sumo_routes import read_network

    raw = b"""<net>
    <location netOffset="0,0" projParameter="!"/>
    <edge id="a"><lane id="a_0" index="0" speed="10" length="100" shape="0,0 100,0"/></edge>
    <edge id="b"><lane id="b_0" index="0" speed="10" length="100" shape="100,0 200,0"/></edge>
    <edge id="bay"><lane id="bay_0" index="0" speed="5" length="100" shape="100,0 150,30 200,0"/></edge>
    <edge id="c"><lane id="c_0" index="0" speed="10" length="100" shape="200,0 300,0"/></edge>
    <edge id="d"><lane id="d_0" index="0" speed="10" length="100" shape="300,0 400,0"/></edge>
    <connection from="a" to="bay" fromLane="0" toLane="0"/>
    <connection from="bay" to="c" fromLane="0" toLane="0"/>
    <connection from="c" to="d" fromLane="0" toLane="0"/>
    </net>"""
    network = read_network(raw)
    original = SourcePlan(
        network.sha256,
        ("a", "b", "c", "d"),
        (SourceStop("stay", 2, "c_0", 45, 20), SourceStop("last", 3, "d_0", 50, 20)),
    )
    detour = Detour(0, 2, ("a", "bay", "c"), 1)
    search = dict(
        branch_index=0,
        rejoin_index=2,
        branch_lane_id="a_0",
        rejoin_lane_id="c_0",
        bay_lane_id="bay_0",
        max_path_working_s=60,
    )
    assert find_detour(network, original, max_visited_lanes=6, **search) == detour
    with pytest.raises(ValueError, match="detour_lane_budget_exhausted"):
        find_detour(network, original, max_visited_lanes=1, **search)
    params = dict(source_external_index=0, source_lane_id="a_0", bay_lane_id="bay_0")
    proposal = splice_detour(network, original, detour, **params)
    assert proposal.plan.edges == ("a", "bay", "c", "d")
    assert proposal.plan.remaining_stops == original.remaining_stops
    assert proposal.retained_occurrences == ((0, 0), (2, 2), (3, 3))
    bypassed = replace(
        original,
        remaining_stops=(SourceStop("required", 1, "b_0", 50, 20), *original.remaining_stops),
    )
    with pytest.raises(ValueError, match="bypass"):
        splice_detour(network, bypassed, detour, **params)
    with pytest.raises(ValueError, match="binding route restriction"):
        splice_detour(network, original, detour, immutable_occurrences=(1,), **params)
    disconnected = read_network(raw.replace(b'from="bay" to="c"', b'from="b" to="c"'))
    with pytest.raises(ValueError, match="lane_continuous"):
        splice_detour(
            disconnected, replace(original, network_sha256=disconnected.sha256), detour, **params
        )


def test_declared_station_scenario_reports_outage_and_recovery_without_inventing_release():
    from evdt.energy.moving_charge_scenario import BayInterval, BaySchedule, StationScenario

    scenario = StationScenario(
        "development",
        "run-1",
        (
            BaySchedule(
                "bay",
                (
                    BayInterval(0, 40, "occupied", None),
                    BayInterval(40, 65, "available", 150_000),
                    BayInterval(65, 95, "out_of_order", None),
                    BayInterval(100, 110, "occupied", None),
                    BayInterval(110, 180, "available", 90_000),
                ),
            ),
        ),
    )

    def at(working_s, **change):
        return scenario.observe(
            **{
                "run_id": "run-1",
                "working_s": working_s,
                "observed_real_life_s": 100,
                "received_real_life_s": 101,
                "expected_sha256": scenario.sha256,
                **change,
            }
        )[0]

    assert at(30).available_from_working_s == 40
    assert at(40).offered_power_w == 150_000
    assert at(70).state == "out_of_order"
    assert at(97).state == "unknown"
    assert at(110).offered_power_w == 90_000
    assert [at(t).revision for t in (30, 40, 70, 97, 100, 110)] == [2, 4, 6, 7, 8, 10]
    with pytest.raises(ValueError, match="different run"):
        at(30, run_id="run-2")
    with pytest.raises(ValueError, match="reviewed assignment"):
        at(30, expected_sha256="0" * 64)
    closed = StationScenario(
        "development", "run-1", (BaySchedule("bay", (BayInterval(0, 40, "occupied", None),)),)
    )
    value = closed.observe(
        run_id="run-1",
        working_s=30,
        observed_real_life_s=100,
        received_real_life_s=101,
        expected_sha256=closed.sha256,
    )[0]
    assert value.state == "occupied" and value.available_from_working_s is None


@pytest.mark.parametrize(
    ("next_time", "plant_power", "expected_reason"),
    [
        (59, 150_000, None),
        (59, 0, "plant_power_changed"),
        (60, 150_000, "approved_window_ended"),
        (80, 0, "station_unavailable"),
    ],
)
def test_committed_bay_checks_actual_power_and_approved_window(
    next_time, plant_power, expected_reason, tmp_path
):
    from unittest.mock import patch

    from evdt.energy.moving_charge_assignment import StationAssignment
    from evdt.energy.moving_charge_scenario import BayInterval, BaySchedule, StationScenario
    from evdt.monitoring.moving_charge_executor import MovingChargeExecutor

    station = replace(bay(), power_w=150_000)
    scenario = StationScenario(
        "development",
        "run-1",
        (BaySchedule(station.bay_id, (BayInterval(0, 80, "available", station.power_w),)),),
    )
    assignment = SimpleNamespace(run_id="run-1", asset_id="bus-001", scenario=scenario)
    manifest, credential = tmp_path / "assignment.json", tmp_path / "credential"
    manifest.write_text("{}")
    credential.write_text("x" * 32)
    with (
        patch(
            "evdt.monitoring.moving_charge_executor.decode_record", return_value=assignment
        ) as decode,
        patch("evdt.monitoring.moving_charge_executor.fingerprint", return_value="0" * 64),
    ):
        executor = MovingChargeExecutor("run-1", "bus-001", manifest, credential)
        decode.assert_called_once_with(StationAssignment, {})
    executor.active = (station, 0.12)
    executor.station_deadline_s = 60
    executor.charged_wh = 200
    connection = SimpleNamespace(
        chargingstation=SimpleNamespace(getChargingPower=lambda bay_id: plant_power)
    )

    assert executor.before_step(connection, next_time - 0.2, next_time) is (expected_reason is None)
    assert executor.report()["delivered_j"] == 720_000
    if expected_reason is None:
        assert executor.events == []
    else:
        assert executor.report()["interrupted"]
        assert executor.events[0]["reason"] == expected_reason
        assert executor.events[0]["working_s"] == next_time - 0.2
        assert not executor.before_step(connection, next_time, next_time + 0.2)
        assert len(executor.events) == 1


def test_two_stop_search_closes_both_visits_and_expires_partial_results():
    from evdt.energy.moving_charge_multiple import plan_two_stops

    original = request()
    start = replace(original.route.occurrences[0], stops=())
    middle = replace(
        start,
        index=1,
        lane_id="middle",
        shape=(RoadPoint(100, 0, 15), RoadPoint(700, 0, 40)),
    )
    end = replace(
        middle,
        index=2,
        lane_id="end",
        shape=(RoadPoint(700, 0, 40), RoadPoint(1400, 0, 65)),
    )
    base = replace(
        original,
        route=replace(original.route, occurrences=(start, middle, end)),
        state=replace(original.state, soc=0.102),
        conditions=(replace(original.conditions[0], auxiliary_power_w=8_000),),
    )
    first = replace(
        bay(),
        lane_id=start.lane_id,
        network_sha256=base.route.network_sha256,
        start_offset_m=45,
        end_offset_m=60,
        bay_id="first",
        site_id="first-site",
        setup_working_s=0,
        power_w=150_000,
    )
    second = replace(
        first,
        lane_id=middle.lane_id,
        start_offset_m=490,
        end_offset_m=505,
        bay_id="second",
        site_id="second-site",
    )

    def available(station, until):
        return replace(
            status(),
            bay_id=station.bay_id,
            state="available",
            offered_power_w=150_000,
            observed_working_s=30,
            available_from_working_s=30,
            open_until_working_s=until,
        )

    offers = (
        (OnRouteStop(first, 0, 59), available(first, 130)),
        (OnRouteStop(second, 1, 504), available(second, 1000)),
    )
    policy = VisitPolicy(
        0,
        8000,
        Curve("1", "W", (CurvePoint(0, 160_000), CurvePoint(1, 160_000)), base.vehicle.origin),
        5,
        "simulated-dc",
        12,
    )
    common = dict(
        policy=policy,
        station_scenario_id="scenario",
        now_real_life_s=102,
        budget_real_life_s=10,
    )
    targets = (0.107, 0.112)
    one = plan_charge(base, offers, target_soc_levels=targets, max_candidate_jobs=12, **common)
    assert one.status == "no_plan_in_search_domain", one.exclusions
    two = plan_two_stops(base, offers, targets=targets, max_evaluations=12, **common)
    assert two.status == "provisional_two_stop_plan"
    approach, terminal = two.visits
    assert approach.next_bay_id == terminal.bay_id == "second"
    assert approach.arrival_working_s is None
    assert approach.transfer.departure_soc == 0.107
    assert terminal.onward.forecast.summary.minimum_soc >= base.vehicle.reserve_soc

    # Evaluate every target pair independently of the cached approach used by the search.
    first_option, second_option = offers
    exhaustive = []
    for first_target in targets:
        first_visit = evaluate_candidate(
            base,
            *first_option,
            target_soc=first_target,
            policy=policy,
            station_scenario_id="scenario",
            now_real_life_s=102,
            next_stop=second_option[0],
        )
        if first_visit.reason is not None or first_visit.transfer is None:
            continue
        second_departure = departure_request(base, first_option[0], first_visit.transfer)
        for second_target in targets:
            second_visit = evaluate_candidate(
                second_departure,
                *second_option,
                target_soc=second_target,
                policy=policy,
                station_scenario_id="scenario",
                now_real_life_s=102,
            )
            if second_visit.arrival_working_s is not None:
                exhaustive.append((first_visit, second_visit))
    assert exhaustive
    assert two.visits == min(exhaustive, key=lambda pair: pair[1].arrival_working_s)
    # With reversed input order the trip itinerary, not the list order, controls the result.
    assert plan_two_stops(base, offers[::-1], targets=targets, max_evaluations=12, **common) == two
    capped = plan_two_stops(base, offers, targets=targets, max_evaluations=2, **common)
    assert capped.status == "search_budget_exhausted" and capped.visits == ()


def test_worker_round_trip_binds_the_decision_to_the_exact_request():
    from evdt.energy.moving_charge_worker import (
        JOB_VERSION,
        PlanningRequest,
        calculate,
        parse_request,
        parse_result,
        validate_planning_result,
    )

    baseline = request()
    policy = VisitPolicy(
        0,
        8000,
        Curve("1", "W", (CurvePoint(0, 100_000), CurvePoint(1, 100_000)), baseline.vehicle.origin),
        5,
        "simulated-dc",
        12,
    )
    job = PlanningRequest(JOB_VERSION, baseline, "scenario", (), policy, (0.2,), 101, 2, 10)
    parsed = parse_request(canonical_bytes(job))
    result = parse_result(canonical_bytes(calculate(parsed)))
    validate_planning_result(job, result)
    assert result.decision.status == "continue"
    with pytest.raises(ValueError, match="different request"):
        validate_planning_result(replace(job, evaluated_real_life_s=102), result)
    with pytest.raises(ValueError, match="recorded charging jobs"):
        validate_planning_result(
            job, replace(result, decision=replace(result.decision, candidate_jobs=3))
        )


def test_alternate_driving_request_keeps_live_source_and_required_stops():
    from evdt.energy.moving_charge_worker import StationOffer

    baseline = request()
    road = baseline.route.occurrences[0]
    route = replace(baseline.route, revision="alternate-road-revision")
    station = replace(bay(), lane_id=road.lane_id, network_sha256=route.network_sha256)
    offer = StationOffer(OnRouteStop(station, 0, 35), status(), route)
    alternate = offer.driving_request(baseline)
    assert alternate.state == replace(baseline.state, route_revision=route.revision)
    assert alternate.route == route
    last = route.occurrences[-1]
    changed = replace(
        route,
        occurrences=(
            *route.occurrences[:-1],
            replace(last, stops=(PlannedStop("different", 20, 1),)),
        ),
    )
    with pytest.raises(ValueError, match="required stops"):
        replace(offer, alternate=changed).driving_request(baseline)


def test_live_station_assignment_binds_repeated_lane_to_ordered_road_visit():
    from evdt.energy.moving_charge_assignment import AssignedBay, StationAssignment
    from evdt.energy.moving_charge_scenario import BayInterval, BaySchedule, StationScenario
    from evdt.energy.route_aware.admission import PreparedPlan
    from evdt.energy.route_aware.context import SourceContext, SourcePlan
    from evdt.energy.route_aware.wire import fingerprint

    original = request()
    road = replace(original.route.occurrences[0], stops=())
    following = replace(
        road,
        index=1,
        edge_id="other",
        shape=(RoadPoint(100, 0, 15), RoadPoint(0, 0, 10)),
    )
    route = (road, following, replace(road, index=2), replace(following, index=3))
    plan = SourcePlan(original.route.network_sha256, tuple(r.edge_id for r in route), ())
    context = SourceContext(
        "source-route-context/2.0.0",
        original.state.scope,
        "a" * 64,
        original.state.working_s,
        original.state.observed_real_life_s,
        plan,
        0,
        road.lane_id,
        original.state.lane_offset_m,
        "not_charging",
        None,
        None,
    )
    base = replace(
        original,
        route=replace(original.route, revision=plan.revision, occurrences=route),
        state=replace(original.state, route_revision=plan.revision),
    )
    prepared = PreparedPlan(plan.revision, base.route, (0, 1, 2, 3), 0)
    station = replace(bay(), lane_id=road.lane_id, network_sha256=plan.network_sha256)
    scenario = StationScenario(
        "scenario",
        original.state.scope.run_id,
        (BaySchedule(station.bay_id, (BayInterval(0, 500, "available", 100_000),)),),
    )
    policy = VisitPolicy(
        0,
        8000,
        Curve("1", "W", (CurvePoint(0, 100_000), CurvePoint(1, 100_000)), base.vehicle.origin),
        5,
        "simulated-dc",
        12,
    )
    assignment = StationAssignment(
        "moving-charge-assignment/1.0.0",
        context.scope.run_id,
        context.scope.asset_id,
        plan.network_sha256,
        plan.edges,
        (AssignedBay(station, 2),),
        scenario,
        policy,
        (0.2,),
        8,
        10,
    )
    job = assignment.prepare_job(
        base,
        context,
        prepared,
        assignment_sha256=fingerprint(assignment),
        now_real_life_s=101,
    )
    assert [offer.stop.occurrence_index for offer in job.offers] == [2]
    with pytest.raises(ValueError, match="reviewed input"):
        assignment.prepare_job(
            base,
            context,
            prepared,
            assignment_sha256="0" * 64,
            now_real_life_s=101,
        )
