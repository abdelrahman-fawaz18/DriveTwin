"""Prepare a live geographic journey from retained, fingerprinted road inputs."""

import hashlib
import json
import math
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.monitoring.charging_startup import charging_startup_delay  # noqa: E402
from evdt.monitoring.pacing import time_scale  # noqa: E402


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_path(name):
    path = (ROOT / name).resolve(strict=True)
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("Journey inputs must be files inside this project")
    return path


def check(profile):
    pace = time_scale(profile.get("time_scale", 1))
    services = selected_services(profile)
    mode = profile.get("moving_charge_mode", "advisory")
    delay = charging_startup_delay(profile.get("charging_startup_delay_working_s", 0))
    if delay and ("charging" not in services or mode == "automatic-qualification"):
        raise ValueError("An advisor startup delay requires a regular charging session")
    if mode not in ("advisory", "operator-development", "automatic-qualification"):
        raise ValueError("Unrecognized moving charging mode")
    if mode != "advisory" and (
        "charging" not in services
        or "energy" not in services
        or "charging_assignment" not in profile
        or pace != 1
    ):
        raise ValueError("Charging actuation requires an explicit 1× charging and forecast profile")
    if "qualification_auto_override" in profile and (
        profile["qualification_auto_override"] is not True or mode != "automatic-qualification"
    ):
        raise ValueError(
            "A seeded operator override belongs to the automatic qualification profile"
        )
    if "moving_charge_recovery" in profile and (
        profile["moving_charge_recovery"] is not True or mode != "operator-development" or pace != 1
    ):
        raise ValueError("Charging recovery requires an explicit 1× operator profile")
    if "qualification_charge_queue" in profile and (
        profile["qualification_charge_queue"] is not True
        or mode != "operator-development"
        or "moving_charge_recovery" in profile
    ):
        raise ValueError("A queued charging trial requires the dedicated operator profile")
    if "qualification_charger_outage" in profile:
        from evdt.energy.route_aware.validation import number, text

        outage = profile["qualification_charger_outage"]
        if (
            profile.get("moving_charge_recovery") is not True
            or type(outage) is not dict
            or set(outage) != {"bay_id", "working_s"}
        ):
            raise ValueError("Qualification outage requires the recovery profile and a fixed input")
        text(outage["bay_id"], "qualification outage bay")
        number(outage["working_s"], "qualification outage cutoff", 1, 14_400)
    if "forecast_candidate" in profile and (
        profile["forecast_candidate"] != "sumo-source-distance" or "energy" not in services
    ):
        raise ValueError("Unsupported development forecast candidate")
    override = None
    if "starting_charge_percent" in profile:
        override = starting_charge_percent(profile["starting_charge_percent"])
    if profile["version"] != "live-geographic-profile/1.0.0":
        raise ValueError("Unsupported live journey profile")
    if not 180 <= profile["maximum_real_life_s"] <= 14_400:
        raise ValueError("Live journey requires a bounded duration")
    for name, expected in profile["inputs"].items():
        if digest(local_path(name)) != expected:
            raise ValueError("Journey input changed: " + name)
    prepared = ROOT / profile["prepared"]
    reference = ROOT / profile["reference"]
    required = [
        prepared / name
        for name in (
            "mission.json",
            "stops.json",
            "geometry.json",
            "heights.json",
            "roads.osm.xml",
            "elevated-route.net.xml",
            "elevated-plant.net.xml",
        )
    ] + [reference / name for name in ("run-input.json", "reference-network.net.xml", "routes.xml")]
    if any(path.relative_to(ROOT).as_posix() not in profile["inputs"] for path in required):
        raise ValueError("Incomplete live input inventory")
    if "charging_assignment" in profile:
        if (
            type(profile["charging_assignment"]) is not str
            or profile["charging_assignment"] not in profile["inputs"]
            or "energy" not in services
        ):
            raise ValueError(
                "Charging advisory requires a bound local assignment and Energy Forecast"
            )
        if pace != 1:
            raise ValueError("The moving-bus Charging Advisor currently requires 1×")
    elif "charging" in services:
        raise ValueError("This trip has no reviewed charging station assignment")
    data = read(reference / "run-input.json")
    if data["recipe"]["id"] != profile["journey_id"]:
        raise ValueError("Journey identity differs from its prepared recipe")
    if "initial_charge_percent" in profile:
        effective = override if override is not None else data["recipe"]["initial_soc"] * 100
        value = profile["initial_charge_percent"]
        if type(value) not in (int, float) or not math.isclose(
            value, effective, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError("Recorded starting charge differs from the prepared recipe")
    from live_assignments import indexed_restrictions

    indexed_restrictions(data["recipe"], len(data["mission"]["route_edges"]))
    if data["mission"] != read(prepared / "mission.json") or data["stops"] != read(
        prepared / "stops.json"
    ):
        raise ValueError("Reference route and prepared geographic inputs disagree")
    return prepared, reference, data


def charging_assignment(profile, run, network_sha256, edges):
    from evdt.energy.moving_charge_assignment import StationAssignment
    from evdt.energy.route_aware.wire import decode_record

    path = local_path(profile["charging_assignment"])
    if path.stat().st_size > 512 * 1024:
        raise ValueError("Local station template exceeds its byte limit")
    raw = read(path)
    if (
        digest(path) != profile["inputs"][profile["charging_assignment"]]
        or not isinstance(raw, dict)
        or not isinstance(raw.get("scenario"), dict)
        or "run_id" not in raw
        or "run_id" not in raw["scenario"]
        or raw["run_id"] is not None
        or raw["scenario"]["run_id"] is not None
    ):
        raise ValueError("Invalid local station assignment template")
    raw["run_id"] = raw["scenario"]["run_id"] = run
    assigned = decode_record(StationAssignment, raw)
    if (
        assigned.asset_id != "bus-001"
        or assigned.network_sha256 != network_sha256
        or assigned.baseline_edges != tuple(edges)
    ):
        raise ValueError("Charging assignment does not match the prepared route")
    if profile.get("moving_charge_mode") == "automatic-qualification" and (
        assigned.policy.margin_soc < 0.01
        or any(entry.detour is not None for entry in assigned.bays)
    ):
        raise ValueError("Automatic qualification requires on-route bays and a one-point margin")
    return assigned


def starting_charge_percent(value):
    if type(value) not in (int, float) or not 0.1 <= value <= 100:
        raise ValueError("Starting charge must be a number from 0.1% to 100%")
    steps = round(value * 10)
    if not math.isclose(value * 10, steps, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Starting charge must use increments of 0.1 percentage point")
    return steps / 10


def selected_services(profile):
    services = profile.get("services", ["energy", "replay"])
    if (
        not isinstance(services, list)
        or any(item not in ("energy", "replay", "charging") for item in services)
        or len(services) != len(set(services))
    ):
        raise ValueError("Select supported optional services: energy, replay, charging")
    return services


def prepare_geometry(profile, runtime):
    """Bind exact installed geometry bytes to this run's runtime paths.

    Historical bundle paths describe the preparation location. The installed
    profile owns current paths; only references change, never the retained data.
    Keep the trusted provider's existing runtime allowlist and byte budgets.
    """
    if not runtime.resolve().is_relative_to(ROOT / ".runtime"):
        raise ValueError("Forecast geometry must be prepared in project runtime storage")
    for entry in (runtime, *runtime.parents):
        if entry == ROOT:
            break
        if entry.is_symlink() or entry.is_junction():
            raise ValueError("Forecast runtime storage cannot use redirected paths")
    prepared = ROOT / profile["prepared"]
    geometry = read(prepared / "geometry.json")
    destination = runtime / "forecast-inputs"
    destination.mkdir()
    for role, name in (
        ("network", "elevated-route.net.xml"),
        ("terrain", "heights.json"),
        ("osm", "roads.osm.xml"),
    ):
        reference = geometry[role]
        if reference is None:
            continue
        source = local_path(profile["prepared"] + "/" + name)
        expected = profile["inputs"].get(source.relative_to(ROOT).as_posix())
        if (
            expected is None
            or reference["sha256"] != expected
            or source.stat().st_size > 32 * 1024**2
            or digest(source) != expected
        ):
            raise ValueError("Installed geometry input differs from its bound inventory")
        target = destination / name
        try:
            os.link(source, target)
        except FileExistsError:
            raise
        except OSError:
            with source.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
        geometry[role] = {"path": target.relative_to(ROOT).as_posix(), "sha256": expected}
    write(runtime / "geometry.json", geometry)
    return runtime / "geometry.json"


def prepare(profile, runtime, run):
    from live_assignments import indexed_restrictions

    prepared, reference, data = check(profile)
    recipe, mission = data["recipe"], data["mission"]
    if "starting_charge_percent" in profile:
        recipe = {
            **recipe,
            "initial_soc": starting_charge_percent(profile["starting_charge_percent"]) / 100,
        }
        data = {**data, "recipe": recipe}
    stops, endpoint = data["stops"]["stops"], data["stops"]["endpoint"]
    scenario = runtime / "scenario"
    scenario.mkdir()
    for name in ("bus-parameters.json", "plant.json", "import-review.json"):
        shutil.copyfile(ROOT / "simulation/montreal" / name, scenario / name)
    shutil.copyfile(reference / "reference-network.net.xml", scenario / "network.net.xml")
    prepared_routes = scenario / "routes.rou.xml"
    if "starting_charge_percent" in profile:
        routes = ET.parse(reference / "routes.xml")
        vehicles = routes.findall("vehicle")
        if len(vehicles) != 1:
            raise ValueError("A geographic starting charge requires exactly one prepared bus")
        charge = vehicles[0].find("param[@key='device.battery.chargeLevel']")
        if charge is None:
            raise ValueError("Prepared bus is missing its initial battery charge")
        charge.set("value", str(recipe["capacity_kwh"] * 1000 * recipe["initial_soc"]))
        routes.write(prepared_routes, encoding="utf-8", xml_declaration=True)
    else:
        shutil.copyfile(reference / "routes.xml", prepared_routes)
    write(
        scenario / "mission.json",
        {
            **mission,
            "step_s": 0.2,
            "seed": 32017,
            "stop": {
                "lane": endpoint["lane_id"],
                "end_pos_m": endpoint["offset_m"],
                "duration_s": 14400,
            },
        },
    )
    parameters = read(scenario / "bus-parameters.json")
    for key, value in {
        "usable_battery_energy": recipe["capacity_kwh"],
        "initial_soc": recipe["initial_soc"],
        "curb_mass": recipe["mass_kg"],
        "passenger_load": 0,
        "maximum_speed": 27.78,
        "comfortable_deceleration": 1.5,
        "baseline_auxiliary_power": recipe["auxiliary_w"],
    }.items():
        parameters["parameters"][key]["value"] = value
        parameters["parameters"][key]["rationale"] = "Retained geographic trip recipe"
    write(scenario / "bus-parameters.json", parameters)
    plant = read(scenario / "plant.json")
    plant["energy"].update(
        air_drag_coefficient=0.7,
        rotating_mass_kg=200,
        radial_drag_coefficient=0,
        rolling_drag_coefficient=0.01 * recipe["surface_multiplier"],
        recuperation_efficiency=0.55,
        non_hvac_power_w=3000,
        hvac_power_w=recipe["auxiliary_w"] - 3000,
    )
    write(scenario / "plant.json", plant)
    geometry = read(prepared / "geometry.json")
    write(
        scenario / "prepared-journey.json",
        {
            "version": "prepared-live-journey/1.0.0",
            "sha256": {
                name: digest(scenario / name) for name in ("network.net.xml", "routes.rou.xml")
            },
            "forecast_network_sha256": geometry["network"]["sha256"],
            "route_edges": mission["route_edges"],
            "speed_factor": recipe["speed_factor"],
            "speed_zones": indexed_restrictions(recipe, len(mission["route_edges"])),
            "stops": [*stops, {**endpoint, "dwell_working_s": 14400}],
            "endpoint": endpoint,
            "provenance": {"inputs": profile["inputs"], "operating_recipe": recipe},
        },
    )
    prepare_geometry(profile, runtime)
    station_model = (
        charging_assignment(
            profile,
            run,
            geometry["network"]["sha256"],
            mission["route_edges"],
        )
        if "charging_assignment" in profile
        else None
    )
    stations = {"stations": []}
    if station_model:
        from evdt.energy.route_aware.wire import fingerprint

        stations["moving_charge_assignment_sha256"] = fingerprint(station_model)
    write(scenario / "stations.json", stations)
    context = map_context(scenario, prepared, profile, run, station_model)
    raw = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > 2_000_000:
        raise ValueError("Prepared map exceeds the existing dashboard context budget")
    (runtime / "secrets/map_context.json").write_bytes(raw)
    return scenario, prepared, data, station_model


def map_context(scenario, prepared, profile, run, station_model=None):
    from journey_map import journey_features

    mission = read(prepared / "mission.json")
    features, bounds = journey_features(
        scenario / "network.net.xml",
        scenario / "routes.rou.xml",
        prepared / "roads.osm.xml",
        mission,
        read(prepared / "stops.json"),
        profile["title"],
        station_bays=station_model.bays if station_model else (),
    )
    return {
        "type": "FeatureCollection",
        "context_version": "1.0.0",
        "run_id": run,
        "asset_id": "bus-001",
        "mission_id": mission["mission_id"],
        "features": features,
        "region": profile["region"],
        "bbox": bounds,
        "attribution": "OpenStreetMap contributors · ODbL-1.0",
        "input_sha256": {
            "roads.osm.xml": digest(prepared / "roads.osm.xml"),
            **{
                name: digest(scenario / name)
                for name in ("mission.json", "stations.json", "import-review.json")
            },
        },
    }


def assignment(runtime, run, recipe, *, source_distance=False, pace=1):
    from evdt.energy.route_aware.assignment import Assignment
    from evdt.energy.route_aware.contracts import Conditions, Origin, VehicleProfile
    from evdt.energy.route_aware.policy import ForecastPolicy
    from evdt.energy.route_aware.wire import decode_record

    evaluated_policy = decode_record(
        ForecastPolicy, read(ROOT / "qualification/energy/route-aware/geographic-policy.json")
    )
    live_policy = replace(
        evaluated_policy,
        period_working_s=120,
        forecast_max_age_working_s=120 + evaluated_policy.worker_deadline_real_life_s,
        forecast_max_age_real_life_s=120 / min(1, time_scale(pace))
        + evaluated_policy.worker_deadline_real_life_s,
        max_observation_lag_working_s=evaluated_policy.observation_lag_limit_working_s,
    )
    if source_distance:
        live_policy = replace(live_policy, sumo_braking_source_distance=True)
    else:
        live_policy = replace(live_policy, sumo_yellow_clearance=True)
    vehicle = replace(
        decode_record(
            VehicleProfile, read(ROOT / "qualification/energy/route-aware/vehicle-profile.json")
        ),
        capacity_kwh=recipe["capacity_kwh"],
        loaded_mass_kg=recipe["mass_kg"],
        max_speed_m_s=27.78,
    )
    origin = Origin(
        "declared",
        "Geographic journey operating recipe",
        "Declared equipment, mass, speed, and surface; no future bus readings",
    )
    conditions = Conditions(
        origin,
        time.time(),
        0,
        14400,
        auxiliary_power_w=recipe["auxiliary_w"],
        rolling_multiplier=recipe["surface_multiplier"],
        speed_factor=recipe["speed_factor"],
    )
    geometry = read(runtime / "geometry.json")
    return Assignment(
        "route-energy-assignment/2.0.0",
        run,
        "bus-001",
        "sumo-adapter",
        1,
        True,
        geometry["network"]["sha256"],
        digest(runtime / "geometry.json"),
        vehicle,
        (conditions,),
        live_policy,
    )
