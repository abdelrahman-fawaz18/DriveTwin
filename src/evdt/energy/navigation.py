"""Static bus-road matching and assumption-based station jobs; no simulator access."""

import math
from dataclasses import asdict

from evdt.energy.calibration import sensitivity_envelope
from evdt.energy.forecasts import ForecastClock, ForecastPolicy, MissionRequest
from evdt.energy.jobs import make_job
from evdt.energy.model import DrivingInterval, EnergyParameters
from evdt.energy.observations import fingerprint, number, observation_point


def location(packet):
    result = {}
    for name, unit, low, high in (
        ("location.longitude", "deg", -180, 180),
        ("location.latitude", "deg", -90, 90),
        ("motion.heading", "rad", 0, 2 * math.pi),
    ):
        samples = [s for s in packet["samples"] if s["signal_id"] == name]
        if (
            len(samples) != 1
            or samples[0].get("quality") != "good"
            or samples[0].get("unit") != unit
            or samples[0].get("provenance") != "simulated-observation"
        ):
            raise ValueError("station_location_missing_or_invalid")
        value = number(samples[0].get("value"), name)
        if not low <= value <= high:
            raise ValueError("station_location_missing_or_invalid")
        result[name] = value
    return result["location.longitude"], result["location.latitude"], result["motion.heading"]


def match(profile, lon, lat, heading):
    if (
        profile["version"] != "static-station-navigation/1.0.0"
        or profile["vehicle_class"] != "bus"
        or not 0 < len(profile["roads"]) <= 512
        or len(profile["stations"]) > 20
    ):
        raise ValueError("station_navigation_profile_invalid")
    candidates = []
    for road in profile["roads"]:
        shape = road["shape_lon_lat"]
        if (
            not road["bus_allowed"]
            or not 2 <= len(shape) <= 1000
            or number(road["length_m"], "road_length") <= 0
        ):
            raise ValueError("station_navigation_profile_invalid")
        # Local tangent-plane projection locates a point ON a road. Energy uses
        # the separately validated routed distance, never this air distance.
        xy = [
            (
                (p[0] - lon) * 111195.0802335329 * math.cos(math.radians(lat)),
                (p[1] - lat) * 111195.0802335329,
            )
            for p in shape
        ]
        lengths = [math.dist(a, b) for a, b in zip(xy, xy[1:])]
        total = math.fsum(lengths)
        if total <= 0:
            continue
        walked = 0
        best = None
        for a, b, length in zip(xy, xy[1:], lengths):
            if length == 0:
                continue
            dx, dy = b[0] - a[0], b[1] - a[1]
            bearing = math.atan2(dx, dy) % (2 * math.pi)
            difference = abs((bearing - heading + math.pi) % (2 * math.pi) - math.pi)
            fraction = min(1, max(0, -(a[0] * dx + a[1] * dy) / (length * length)))
            distance = math.hypot(a[0] + fraction * dx, a[1] + fraction * dy)
            if difference <= profile["heading_tolerance_rad"]:
                candidate = (distance, (walked + fraction * length) / total * road["length_m"])
                if best is None or candidate < best:
                    best = candidate
            walked += length
        if best is not None and best[0] <= profile["maximum_location_match_m"]:
            candidates.append((best[0], road["edge_id"], best[1], road))
    candidates.sort(key=lambda c: (c[0], c[1]))
    if not candidates:
        raise ValueError("station_location_outside_navigation_domain")
    if len(candidates) > 1 and candidates[1][0] - candidates[0][0] <= profile["ambiguity_margin_m"]:
        raise ValueError("station_location_ambiguous")
    error, identity, offset, road = candidates[0]
    return road, {"edge_id": identity, "offset_m": offset, "match_error_m": error}


def prepare_stations(value, model, profile):
    if profile is None:
        return {
            "version": "station-assessments/1.0.0",
            "profile_sha256": None,
            "items": [],
            "reason_codes": ["station_navigation_not_configured"],
        }, {}
    if profile["mission_id"] != value["assignment"].get("mission_id", "montreal-core-loop-v1"):
        raise ValueError("Station navigation mission mismatch")
    items = [
        {
            "station_id": s["station_id"],
            "hypothetical": True,
            "availability": "unverified",
            "status": "unavailable",
            "reason_codes": [],
            "values": None,
            "uncertainty": None,
            "path": None,
        }
        for s in profile["stations"]
    ]
    summary = {
        "version": "station-assessments/1.0.0",
        "profile_sha256": fingerprint(profile),
        "items": items,
        "reason_codes": [],
    }
    if value["reason_codes"]:
        for item in items:
            item["reason_codes"] = list(value["reason_codes"])
        return summary, {}
    try:
        road, current = match(profile, *location(value["packets"][-1]))
    except ValueError as exc:
        for item in items:
            item["reason_codes"] = [str(exc)]
        return summary, {}
    point = observation_point(value["packets"][-1])
    parameters = EnergyParameters(**model["parameters"])
    packets = tuple(value["packets"])
    duration = packets[-1]["sim_time_s"] - packets[0]["sim_time_s"]
    first = observation_point(packets[0])
    recent_speed = (point.distance_m - first.distance_m) / duration if duration > 0 else 0
    auxiliary = next(
        s["value"] for s in packets[-1]["samples"] if s["signal_id"] == "auxiliary.power"
    )
    jobs = {}
    for station, item in zip(profile["stations"], items, strict=True):
        path = road["paths"].get(station["station_id"])
        if path is None:
            item["reason_codes"] = ["station_no_bus_route"]
            continue
        distance = path["distance_from_edge_start_m"] - current["offset_m"]
        if station["edge_id"] == road["edge_id"] and station["offset_m"] < current["offset_m"] - 1:
            item["reason_codes"] = ["station_approach_behind_vehicle"]
            continue
        if distance < -1 or not path["internal_connectors_in_distance"]:
            item["reason_codes"] = ["station_path_invalid"]
            continue
        distance = max(0, distance)
        if distance <= 1:
            distance = 0
        speed = min(recent_speed, path["speed_limit_m_s"])
        if distance and speed <= 0:
            item["reason_codes"] = ["station_forward_speed_not_established"]
            continue
        plan = (
            ()
            if not distance
            else (
                DrivingInterval(
                    2 * distance / (point.speed_m_s + speed), point.speed_m_s, speed, 0, auxiliary
                ),
            )
        )
        request = MissionRequest(
            value["input_id"] + ":" + station["station_id"],
            station["station_id"],
            fingerprint(path),
            distance,
            parameters.declared_capacity_kwh,
            "independent-estimator-capacity/1",
            value["assignment"]["reserve_soc"],
            "absent",
        )
        job = make_job(
            packets,
            point.scope,
            ForecastClock(point.sim_time_s, "live_snapshot", "running"),
            request,
            ForecastPolicy("development-prefix/1", 10, 120, 10, 1.5, 5, 1000),
            parameters=parameters,
            plan=plan,
        )
        key = "station:" + station["station_id"]
        jobs[key] = job
        item["job_key"] = key
        item["path"] = {
            **path,
            "remaining_distance_m": distance,
            "current_position": current,
            "approach_edge_id": station["edge_id"],
            "approach_lane_id": station["lane_id"],
            "approach_offset_m": station["offset_m"],
            "road_approach_snap_m": station["snap_distance_m"],
            "horizon_duration_s": math.fsum(p.duration_s for p in plan),
            "assumed_end_speed_m_s": speed,
            "assumed_grade_ratio": 0,
            "auxiliary_power_w": auxiliary,
            "motion_assumption": "Linear transition from observed speed to recent mean speed capped by path road limits; flat grade; constant observed equipment demand",
            "physical_site_access": "unverified",
        }
        u = model["uncertainty"]
        if plan:
            try:
                item["uncertainty"] = {
                    **asdict(
                        sensitivity_envelope(
                            parameters,
                            plan,
                            point.soc,
                            u,
                            parameter_pairs=tuple(tuple(p) for p in u["joint_parameter_pairs"]),
                        )
                    ),
                    "station_endpoint_coverage_measured": False,
                }
            except ValueError:
                item["uncertainty_reason"] = "station_scenarios_outside_unclipped_domain"
    return summary, jobs


def finish_stations(prepared, outputs, jobs):
    import copy

    summary = copy.deepcopy(prepared)
    for item in summary["items"]:
        key = item.pop("job_key", None)
        if key is None:
            continue
        response = outputs[key]
        if response["job_sha256"] != fingerprint(jobs[key]):
            raise ValueError("Station worker job binding mismatch")
        forecast = response["forecast"]
        if forecast["status"] != "advisory":
            item.update(reason_codes=list(forecast["reason_codes"]), uncertainty=None)
            continue
        values = forecast["values"]
        item["values"] = values
        item["status"] = (
            "energy_shortfall"
            if not values["energy_plan_feasible"]
            else "reserve_shortfall"
            if not values["reserve_feasible"]
            else "at_station_approach"
            if item["path"]["remaining_distance_m"] == 0
            else "reachable_under_assumptions"
        )
        item["reason_codes"] = ["station_accuracy_and_site_availability_unqualified"]
    return summary
