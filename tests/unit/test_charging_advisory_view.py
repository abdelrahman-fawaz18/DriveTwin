"""Current charging estimates expire with their source, forecast, and bay."""

from copy import deepcopy
from unittest.mock import Mock

from evdt.energy.route_aware.context import SourcePlan
from evdt.monitoring.charging_advisory_view import observed_effect, present


def test_effect_is_a_recorded_observation_separate_from_acknowledgment():
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = None
    assert observed_effect(conn, "run", "asset") is None
    row = {
        "sim_time_s": 315,
        "annotations": {
            "charging_effect": "motion_resumed",
            "charging_bay": "bay-2",
            "charging_effect_time": "264.2",
            "charging_delivered_j": "14400000",
        },
    }
    conn.execute.return_value.fetchone.return_value = row
    assert observed_effect(conn, "run", "asset") == {
        "state": "motion_resumed",
        "bay_id": "bay-2",
        "delivered_j": 14_400_000,
        "observed_working_s": 264.2,
    }
    assert conn.execute.call_args.args[1] == ("run", "asset")
    row["annotations"]["charging_effect_time"] = "316"
    assert observed_effect(conn, "run", "asset") is None
    row["annotations"].update(
        charging_effect="approaching", charging_effect_time="250", charging_delivered_j="0"
    )
    assert observed_effect(conn, "run", "asset")["state"] == "approaching"
    row["annotations"]["charging_delivered_j"] = "2"
    assert observed_effect(conn, "run", "asset") is None
    row["annotations"].update(charging_effect="waiting_for_charger", charging_delivered_j="0")
    assert observed_effect(conn, "run", "asset")["state"] == "waiting_for_charger"


def test_advisory_is_current_only_for_matching_live_inputs_and_reachable_bay():
    plan = SourcePlan("b" * 64, ("a", "b", "c"), ())
    scope = {"run_id": "run", "asset_id": "bus"}
    context = {
        "plan": {
            "network_sha256": plan.network_sha256,
            "edges": list(plan.edges),
            "remaining_stops": [],
        },
        "external_index": 0,
        "lane_id": "a_0",
        "lane_offset_m": 10,
    }
    current = {
        "reason": None,
        "clock": {"scope": scope, "working_s": 15},
        "context": context,
        "current_context": context,
        "assignment_sha256": "c" * 64,
        "as_of_real_life_s": 307,
    }
    record = {
        "status": "current",
        "forecast_fence": 1,
        "latest_forecast_fence": 1,
        "validity": {
            "forecast_assignment_sha256": "c" * 64,
            "route_revision": plan.revision,
            "working_expires": 35,
            "real_life_expires": 310,
            "bay_positions": {"bay": {"external_index": 1, "lane_id": "b_0", "end_offset_m": 30}},
        },
        "payload": {
            "decision": {
                "status": "provisional_plan",
                "baseline": {
                    "initial": {"working_s": 12},
                    "forecast": {"scope": scope},
                    "first_below_reserve": {"working_s": 65, "coordinates": [-73.5, 45.5]},
                },
                "preferred": {
                    "bay_id": "bay",
                    "approach": {
                        "forecast": {"summary": {"arrival_working_s": 60, "minimum_soc": 0.103}}
                    },
                    "transfer": {
                        "departure_soc": 0.12,
                        "arrival_soc": 0.103,
                        "plug_in_soc": 0.102,
                        "grid_energy_j": 5_000_000,
                        "wait_working_s": 25,
                        "start_working_s": 85,
                        "end_working_s": 195,
                    },
                    "onward": {
                        "forecast": {
                            "summary": {
                                "arrival_working_s": 400,
                                "arrival_soc": 0.105,
                                "minimum_soc": 0.101,
                            }
                        }
                    },
                },
                "fallback": None,
            }
        },
    }
    current_forecast = {"fence": 1}
    assert present(record, current, current_forecast, 0, charging_delay=30) == {
        "configured": True,
        "status": "startup_delay",
        "startup_delay_working_s": 30,
        "estimate": None,
        "recorded": None,
    }
    at_release = {**current, "clock": {**current["clock"], "working_s": 30}}
    assert present(record, at_release, current_forecast, 0, charging_delay=30)["estimate"]
    visible = present(record, current, current_forecast, 0)
    assert visible["status"] == "provisional_plan"
    assert visible["estimate"]["minimum_soc"] == 0.101
    assert visible["estimate"]["expires_in_real_life_s"] == 3
    assert visible["estimate"]["working_expires"] == 35
    assert visible["estimate"]["expires_at_real_life_s"] == 310
    assert visible["estimate"]["charging_working_s"] == 110
    assert visible["estimate"]["charger_arrival_working_s"] == 60
    assert visible["estimate"]["charger_arrival_soc"] == 0.103
    assert visible["estimate"]["estimated_grid_energy_j"] == 5_000_000
    assert visible["estimate"]["destination_soc"] == 0.105
    assert visible["estimate"]["approval_waiting_for_road"] is False
    assert visible["estimate"]["approval_waiting_for_motion"] is True
    sample = {
        "signal_id": "motion.speed",
        "unit": "m/s",
        "provenance": "simulated-observation",
        "quality": "good",
        "value": 4.0,
    }
    moving = {**current, "packet": {"payload": {"samples": [sample]}}}
    assert (
        present(record, moving, current_forecast, 0)["estimate"]["approval_waiting_for_motion"]
        is False
    )
    sample["value"] = 0.0
    assert (
        present(record, moving, current_forecast, 0)["estimate"]["approval_waiting_for_motion"]
        is True
    )
    at_junction = {**current, "current_context": {**context, "lane_id": ":junction_0"}}
    assert (
        present(record, at_junction, current_forecast, 0)["estimate"]["approval_waiting_for_road"]
        is True
    )
    assert visible["reserve_crossing"] == {
        "working_s": 65,
        "coordinates": [-73.5, 45.5],
        "expires_in_real_life_s": 3,
    }
    no_option = deepcopy(record)
    no_option["payload"]["decision"].update(
        status="no_plan_in_search_domain",
        preferred=None,
        exclusions=[
            {"bay_id": "bay", "reason": "below_reserve_while_waiting"},
            {"bay_id": "bay", "reason": "below_reserve_while_waiting"},
            {"bay_id": "bay", "reason": "access_window_ends_before_target"},
            {"bay_id": "bay", "reason": "charging_target_must_add_energy"},
            {"bay_id": "another", "reason": "opaque internal model error"},
        ],
    )
    no_option_visible = present(no_option, current, current_forecast, 0)
    assert no_option_visible["reserve_crossing"] == visible["reserve_crossing"]
    assert no_option_visible["excluded_bays"] == [
        {"bay_id": "bay", "reasons": ["below_floor_while_waiting", "station_window_too_short"]},
        {"bay_id": "another", "reasons": ["qualification_input_unavailable"]},
    ]
    assert no_option_visible["comparison_expires_in_real_life_s"] == 3
    assert "excluded_bays" not in present(
        no_option, {**current, "as_of_real_life_s": 310}, current_forecast, 0
    )
    detour = deepcopy(record)
    detour["validity"]["bay_positions"]["bay"]["branch_only"] = True
    assert present(detour, current, current_forecast, 0)["estimate"]["off_route"] is True

    past = deepcopy(current)
    past["current_context"].update(external_index=1, lane_id="b_0", lane_offset_m=31)
    at_branch = deepcopy(past)
    at_branch["current_context"]["lane_offset_m"] = 1
    assert present(record, at_branch, current_forecast, 0)["estimate"]["past_approval_point"]
    assert present(detour, at_branch, current_forecast, 0)["estimate"] is None
    detour["validity"]["bay_positions"]["second"] = {
        "external_index": 2,
        "lane_id": "c_0",
        "end_offset_m": 45,
    }
    decision = detour["payload"]["decision"]
    decision["fallback"] = {**decision["preferred"], "bay_id": "second"}
    decision["exclusions"] = [{"bay_id": "second", "reason": "below_reserve_while_waiting"}]
    decision["fallback"]["approach"] = {
        "forecast": {"summary": {"arrival_working_s": 30, "minimum_soc": 0.103}}
    }
    two_options = present(detour, current, current_forecast, 0)["estimate"]
    assert two_options["fallback_bay_id"] == "second"
    assert two_options["fallback_details"]["target_soc"] == 0.12
    assert two_options["fallback_details"]["wait_working_s"] == 25
    assert two_options["fallback_details"]["working_expires"] == 30
    assert "excluded_bays" not in present(detour, current, current_forecast, 0)
    detour["validity"]["bay_positions"]["bay"]["station_state"] = "occupied"
    detour["validity"]["bay_positions"]["second"]["station_state"] = "available"
    active = present(detour, current, current_forecast, 0)["estimate"]
    assert active["station_state"] == "occupied"
    assert active["fallback_station_state"] == "available"
    alternate = present(detour, at_branch, current_forecast, 0)["estimate"]
    assert alternate["bay_id"] == "second"
    assert alternate["fallback_only"] is True
    assert alternate["fallback_bay_id"] is None
    expired = {**at_branch, "as_of_real_life_s": 310}
    assert present(detour, expired, current_forecast, 0)["estimate"] is None
    for job, state in (
        (record, past),
        (record, {**current, "as_of_real_life_s": 310}),
        (record, {**current, "assignment_sha256": "d" * 64}),
        ({**record, "validity": None}, current),
    ):
        hidden = present(job, state, current_forecast, 0)
        assert hidden["status"] == "historical"
        assert hidden["estimate"] is None
        assert hidden["recorded"]["cutoff_working_s"] == 12

    paused = present(record, {**current, "reason": "source_paused"}, current_forecast, 0)
    assert paused["status"] == "source_unavailable"
    assert paused["estimate"] is None

    current["assignment"] = {
        "policy": {"forecast_max_age_working_s": 60, "forecast_max_age_real_life_s": 90},
        "vehicle": {"reserve_soc": 0.1},
    }
    forecast = {
        "fence": 2,
        "classification": "current",
        "assignment_sha256": "c" * 64,
        "route_revision": plan.revision,
        "payload": {
            "scope": scope,
            "status": "available",
            "cutoff_working_s": 12,
            "requested_real_life_s": 300,
            "summary": {"arrival_soc": 0.16, "minimum_soc": 0.12},
        },
    }
    assert present(record, current, forecast, 0.01)["status"] == "continue"
    assert present(None, current, forecast, 0.01)["status"] == "continue"
    assert present(record, current, forecast, 0.03)["status"] == "historical"
    assert (
        present(record, current, {**forecast, "classification": "historical"}, 0)["status"]
        == "historical"
    )
    assert present(None, {**current, "as_of_real_life_s": 391}, forecast, 0)["status"] == "waiting"
