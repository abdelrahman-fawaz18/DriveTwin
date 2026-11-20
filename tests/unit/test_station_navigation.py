"""Road/path identity and whole-trajectory reserve behavior, without plant truth."""

import copy
import math

import pytest
from test_energy_product import RELEASE, identify, model, trusted

from evdt.energy.jobs import run_request
from evdt.energy.navigation import finish_stations, location, match
from evdt.energy.observations import fingerprint
from evdt.energy.product import make_record, prepare


def fixture():
    nav = {
        "version": "static-station-navigation/1.0.0",
        "mission_id": "montreal-core-loop-v1",
        "vehicle_class": "bus",
        "maximum_location_match_m": 15,
        "ambiguity_margin_m": 2,
        "heading_tolerance_rad": math.pi / 3,
        "roads": [
            {
                "edge_id": "a",
                "bus_allowed": True,
                "length_m": 111.1950802335329,
                "shape_lon_lat": [[0, 0], [0, 0.001]],
                "paths": {},
            }
        ],
        "stations": [],
    }
    for name, edge, offset in (("ahead", "a", 100), ("behind", "a", 10), ("unmapped", "b", 50)):
        nav["stations"].append(
            {
                "station_id": name,
                "edge_id": edge,
                "lane_id": edge + "_0",
                "offset_m": offset,
                "snap_distance_m": 0,
            }
        )
        nav["roads"][0]["paths"][name] = (
            None
            if name == "unmapped"
            else {
                "edges": ["a"],
                "distance_from_edge_start_m": offset,
                "speed_limit_m_s": 15,
                "internal_connectors_in_distance": True,
            }
        )
    v = trusted()
    for packet in v["packets"]:
        packet["samples"] += [
            {
                "signal_id": name,
                "value": number,
                "unit": unit,
                "quality": "good",
                "provenance": "simulated-observation",
            }
            for name, number, unit in (
                ("location.longitude", 0, "deg"),
                ("location.latitude", 0.0005, "deg"),
                ("motion.heading", 0, "rad"),
            )
        ]
    return bind(v, nav), nav


def bind(value, nav):
    value["assignment"]["navigation_content_sha256"] = fingerprint(nav)
    value["assignment_sha256"] = fingerprint(value["assignment"])
    return identify(value)


def calculate(value, nav):
    import json

    p = prepare(value, model(), RELEASE, nav)
    outputs = {k: run_request(json.dumps(j).encode()) for k, j in p["jobs"].items()}
    return p, outputs, make_record(value, p, outputs, RELEASE, "sha256:worker")


def test_explicit_directed_path_generates_minimized_station_job_and_explanation():
    v, nav = fixture()
    p, out, record = calculate(v, nav)
    items = record["extensions"]["stations"]["items"]
    assert [i["status"] for i in items] == [
        "reachable_under_assumptions",
        "unavailable",
        "unavailable",
    ]
    assert items[1]["reason_codes"] == ["station_approach_behind_vehicle"]
    assert items[2]["reason_codes"] == ["station_no_bus_route"]
    assert items[0]["path"]["remaining_distance_m"] == pytest.approx(44.40245988323355)
    assert items[0]["uncertainty"]["station_endpoint_coverage_measured"] is False
    assert all(
        len(packet["samples"]) == 4 for j in p["jobs"].values() for packet in j["observations"]
    )
    explanation = record["extensions"]["explanation"]
    assert explanation["horizon_duration_s"] > 0 and explanation["horizon_distance_m"] == 800
    assert explanation["declared_capacity_kwh"] == 350 and explanation["reserve_soc"] == 0.1
    assert out["reference"]["forecast"]["values"]["conditional_time_to_reserve_s"] > 0


@pytest.mark.parametrize(
    "latitude,heading,expected",
    [
        (1, 0, "station_location_outside_navigation_domain"),
        (0.0005, math.pi, "station_location_outside_navigation_domain"),
    ],
)
def test_remote_or_opposite_direction_points_do_not_become_routes(latitude, heading, expected):
    v, nav = fixture()
    for s in v["packets"][-1]["samples"]:
        if s["signal_id"] == "location.latitude":
            s["value"] = latitude
        if s["signal_id"] == "motion.heading":
            s["value"] = heading
    identify(v)
    _, _, r = calculate(v, nav)
    assert r["status"] == "advisory"
    assert all(
        s["reason_codes"] == [expected] and s["values"] is None
        for s in r["extensions"]["stations"]["items"]
    )


def test_ambiguous_parallel_roads_are_unavailable():
    v, nav = fixture()
    other = copy.deepcopy(nav["roads"][0])
    other["edge_id"] = "parallel"
    nav["roads"].append(other)
    bind(v, nav)
    _, _, r = calculate(v, nav)
    assert r["extensions"]["stations"]["items"][0]["reason_codes"] == ["station_location_ambiguous"]


def test_missing_gps_does_not_destroy_unrelated_energy_prediction():
    v, nav = fixture()
    v["packets"][-1]["samples"][-1].update(quality="missing", value=None)
    identify(v)
    _, _, r = calculate(v, nav)
    assert r["status"] == "advisory" and r["values"] is not None
    assert r["extensions"]["stations"]["items"][0]["reason_codes"] == [
        "station_location_missing_or_invalid"
    ]


@pytest.mark.parametrize(
    "soc,status", [(0.100001, "reserve_shortfall"), (0.000001, "energy_shortfall")]
)
def test_station_shortfalls_are_actual_values_not_missing_predictions(soc, status):
    v, nav = fixture()
    for p in v["packets"]:
        p["samples"][2]["value"] = soc
    identify(v)
    _, _, r = calculate(v, nav)
    s = r["extensions"]["stations"]["items"][0]
    assert s["status"] == status and s["values"] is not None


def test_already_at_road_approach_is_valid_zero_distance():
    v, nav = fixture()
    _, pos = match(nav, *location(v["packets"][-1]))
    nav["stations"][0]["offset_m"] = pos["offset_m"]
    nav["roads"][0]["paths"]["ahead"]["distance_from_edge_start_m"] = pos["offset_m"]
    bind(v, nav)
    _, _, r = calculate(v, nav)
    s = r["extensions"]["stations"]["items"][0]
    assert s["status"] == "at_station_approach" and s["path"]["remaining_distance_m"] == 0
    assert s["values"]["net_mission_energy_kwh"] == 0


def test_later_recovery_cannot_hide_a_reported_intermediate_reserve_breach():
    v, nav = fixture()
    p, out, _ = calculate(v, nav)
    key = "station:ahead"
    out[key]["forecast"]["values"].update(
        reserve_feasible=False, energy_plan_feasible=True, projected_final_soc=0.12
    )
    s = finish_stations(p["stations"], out, p["jobs"])["items"][0]
    assert s["status"] == "reserve_shortfall"


def test_changed_navigation_identity_and_wrong_mission_are_rejected():
    v, nav = fixture()
    nav["stations"][0]["offset_m"] += 1
    with pytest.raises(ValueError, match="profile mismatch"):
        prepare(v, model(), RELEASE, nav)
    nav["mission_id"] = "other"
    bind(v, nav)
    with pytest.raises(ValueError, match="mission mismatch"):
        prepare(v, model(), RELEASE, nav)


def test_source_unavailable_hides_every_station_numeric_output():
    v, nav = fixture()
    v["reason_codes"] = ["source_paused"]
    identify(v)
    p, _, r = calculate(v, nav)
    assert not p["jobs"] and r["values"] is None
    assert all(s["values"] is None for s in r["extensions"]["stations"]["items"])
