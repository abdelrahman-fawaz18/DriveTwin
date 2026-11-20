"""Prepared route observations and scoped incremental trajectory pages."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from evdt.monitoring import analysis_views
from evdt.monitoring.dashboard import parse_trip_progress
from evdt.monitoring.prepared_journey import PreparedJourney


def test_prepared_progress_excludes_charger_and_terminal_hold():
    journey = object.__new__(PreparedJourney)
    passenger = {"external_index": 2, "lane_id": "c_0", "offset_m": 25, "stop_id": "osm-123"}
    endpoint = {"external_index": 3, "lane_id": "d_1", "offset_m": 40}
    journey.spec = {
        "route_edges": ["a", "b", "c", "d"],
        "stops": [passenger, endpoint],
        "endpoint": endpoint,
    }
    journey.arrived = False
    journey.charge_stop = SimpleNamespace(
        external_index=1, bay=SimpleNamespace(bay_id="bay", lane_id="b_0", end_offset_m=50)
    )
    stop, terminal = [
        SimpleNamespace(lane=assigned["lane_id"], endPos=assigned["offset_m"], stoppingPlaceID="")
        for assigned in (passenger, endpoint)
    ]
    charger = SimpleNamespace(lane="b_0", endPos=50, stoppingPlaceID="bay")
    vehicle = Mock()
    vehicle.getRoute.return_value = journey.spec["route_edges"]
    vehicle.getRouteIndex.return_value = 1
    vehicle.getStops.return_value = (charger, stop, terminal)
    vehicle.getStopState.return_value = 1
    vehicle.getDrivingDistance.return_value = 185.123
    connection = SimpleNamespace(vehicle=vehicle)

    assert journey.trip_progress(connection, "bus-001") == {
        "remaining_m": 185.1,
        "next_stop_id": "osm-123",
        "at_passenger_stop": False,
    }
    vehicle.getDrivingDistance.assert_called_once_with("bus-001", "d", 40, 1)
    vehicle.getStops.return_value = (stop, terminal)
    assert journey.trip_progress(connection, "bus-001")["at_passenger_stop"] is True
    vehicle.getStops.return_value = (terminal,)
    journey.arrived = True
    assert journey.trip_progress(connection, "bus-001") == {
        "remaining_m": 0,
        "next_stop_id": None,
        "at_passenger_stop": False,
    }

    journey.arrived = False
    vehicle.getRoute.return_value = journey.spec["route_edges"] = ["a", "d", "c", "d"]
    vehicle.getStops.return_value = (stop, terminal)
    vehicle.getDrivingDistance.reset_mock()
    assert journey.trip_progress(connection, "bus-001")["remaining_m"] is None
    vehicle.getDrivingDistance.assert_not_called()


def test_trip_progress_annotation_is_strict_and_optional():
    valid = {"remaining_m": 0, "next_stop_id": None, "at_passenger_stop": False}
    assert parse_trip_progress(json.dumps(valid)) == valid
    assert parse_trip_progress(None) is None
    assert parse_trip_progress("[1,2,3]") is None
    for fields in (
        {"remaining_m": True},
        {"remaining_m": float("nan")},
        {"next_stop_id": "\n"},
        {"at_passenger_stop": True},
    ):
        assert parse_trip_progress(json.dumps({**valid, **fields})) is None


def test_trajectory_delta_binds_both_snapshot_revisions_and_preserves_gap_rows(monkeypatch):
    run, asset = "run", "bus"
    latest, since = "run:bus:18", "run:bus:15"
    conn = Mock()
    calls = []

    def execute(sql, args=None):
        result = Mock()
        if "FROM m_observations" in sql:
            calls.append(args)
            result.fetchall.return_value = [
                {
                    "sim_time_s": 2,
                    "event_id": "00000000-0000-0000-0000-000000000001",
                    "samples": [],
                },
                {
                    "sim_time_s": 3,
                    "event_id": "00000000-0000-0000-0000-000000000002",
                    "samples": [],
                },
            ]
        elif "FROM m_run_definitions" in sql:
            result.fetchone.return_value = {"policy": {"sources": []}}
        return result

    conn.execute.side_effect = execute
    monkeypatch.setattr(analysis_views, "connection", lambda: nullcontext(conn))
    monkeypatch.setattr(analysis_views, "scope", lambda *_: (run, 18))
    page = analysis_views.trajectory(asset, latest, 0, 3600, None, 1, since=since)
    assert calls[0][:6] == (run, asset, 15, 18, 0, 3600)
    assert page["items"][0]["coordinates"] is None
    analysis_views.trajectory(asset, latest, 0, 3600, page["next"], 1, since=since)
    assert calls[1][6] == 2
    with pytest.raises(HTTPException) as wrong_anchor:
        analysis_views.trajectory(asset, latest, 0, 3600, page["next"], 1, since="run:bus:16")
    assert wrong_anchor.value.status_code == 422
    with pytest.raises(HTTPException) as future_anchor:
        analysis_views.trajectory(asset, latest, 0, 3600, None, 1, since="run:bus:19")
    assert future_anchor.value.status_code == 409
    original = analysis_views.trajectory(asset, latest, 0, 3600, None, 1)
    assert len(json.loads(original["next"])) == 7
