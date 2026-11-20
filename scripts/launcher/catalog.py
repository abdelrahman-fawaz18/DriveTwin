"""Local, fingerprinted trip catalog for the launch application."""

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/geographic"))
from live_inputs import (  # noqa: E402
    charging_assignment,
    check,
    digest,
    local_path,
    read,
    starting_charge_percent,
    time_scale,
    write,
)

CATALOG = ROOT / ".private/operator/live-trips.json"
OPERATOR_MODE = "operator-development"
# Full road, stop, elevation and station input inventory from the connected trial.
OPERATOR_INPUTS = {
    "montreal-central-lafontaine": "589590c05173b2aa14c2ab5313599d5807c00b31300bb344a5f895452ace8141",
    "montreal-mount-royal": "339486b163543989cd473fdf3b0fdf11ccbe5bcf8504897f75bf5d7d5499b326",
}
# Prepared inventories with per-bay forecast, route change, transfer, and arrival evidence.
DETOUR_OPERATOR_INPUTS = {
    "montreal-central-lafontaine": "0a0115799e3cc0687cae9686c5b2f5a77ea7a32cb6e81f494b8231f10f901d85",
    "ottawa-parliament-carleton": "09b679a7d903361e36ed9a325a1d79441fbb590b7f1ba7c777565c82fbbc9c37",
    "vancouver-burnaby-climb": "1e57d3f41b0d4fa45186b30e46fa0170ff2b9dadcfb062f18ab1723ec899cebb",
    "vancouver-stanley-park": "2e7cfc0af154d8a87785ef435a664db20e3f2a52f8db0eb1765ced835fa77f6b",
}
# The same complete, reviewed input bytes after portable installation. Identity
# uses each unique filename and its digest so moving immutable inputs does not
# revoke a reviewed profile or grant authority to different data.
OPERATOR_CONTENT_INPUTS = {
    "montreal-central-lafontaine": "707d04fc5f7c39ad3dbdb6f01f05ea601f5f8069fca86c97f5e048e1d2090ef3",
    "montreal-mount-royal": "94e55ce1b92ad50bdeadbc5c6fa5270252feb5fcae41a80291691febd47afcb5",
}
DETOUR_OPERATOR_CONTENT_INPUTS = {
    "montreal-central-lafontaine": "62b409088322d3144a98b20af80e8e03c635373c9441fdb173440ec273219cee",
    "ottawa-parliament-carleton": "24c50b937ef15408fe90bb7a01901f9b23573e74b1bb6d52b076c1bc7ece8b45",
    "vancouver-stanley-park": "6d80f2e5f732e0ba06c3daeb1749d2e9c670912cff2bd9dd41abfea2b67d66b7",
    "vancouver-burnaby-climb": "79facadd47ac549fa621bbd9e095feff528576b2b03f722d9ac2d5a6ed9f13b4",
}


def operator_profile_matches(trip, *, detour=False):
    variant = trip.get("charging_variant") if detour else trip["profile"]
    expected = (DETOUR_OPERATOR_INPUTS if detour else OPERATOR_INPUTS).get(trip["id"])
    if variant is None or expected is None or "charging" not in trip["available_services"]:
        return False
    inventory = json.dumps(variant["inputs"], sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(inventory.encode()).hexdigest() == expected:
        return True
    content = {Path(name).name: sha for name, sha in variant["inputs"].items()}
    content_expected = (DETOUR_OPERATOR_CONTENT_INPUTS if detour else OPERATOR_CONTENT_INPUTS).get(
        trip["id"]
    )
    if len(content) != len(variant["inputs"]) or content_expected is None:
        return False
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest() == content_expected


def operator_available(trip):
    return operator_profile_matches(trip, detour=True) or operator_profile_matches(trip)


def save_catalog(destination, trips):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        write(temporary, {"version": "launch-trip-catalog/1.0.0", "trips": trips})
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def preview_map(trip):
    profile = trip["profile"]
    reference = profile["reference"]
    inputs = [
        reference + "/reference-network.net.xml",
        reference + "/routes.xml",
        profile["prepared"] + "/roads.osm.xml",
        reference + "/run-input.json",
    ]
    fingerprints = [digest(local_path(name)) for name in inputs]
    if fingerprints != [profile["inputs"][name] for name in inputs]:
        raise ValueError("The prepared map inputs have changed")
    fingerprints.append(digest(ROOT / "scripts/geographic/journey_map.py"))
    key = hashlib.sha256("".join(fingerprints).encode()).hexdigest()
    cache = ROOT / ".runtime/launch-app/maps" / (key + ".json")
    if cache.is_file():
        return read(cache)
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    result = subprocess.run(
        [
            str(executable),
            "scripts/linux.py",
            "--timeout",
            "25",
            "--",
            ".runtime/energy-v2/venv/bin/python",
            "scripts/geographic/journey_map.py",
            *inputs,
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode or len(result.stdout) > 2_000_000:
        raise ValueError("The local street map could not be prepared")
    value = json.loads(result.stdout)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".tmp")
    write(temporary, value)
    temporary.replace(cache)
    return value


def load(path=CATALOG):
    if not path.is_file():
        return []
    value = read(path)
    if value.get("version") != "launch-trip-catalog/1.0.0":
        raise ValueError("Unsupported local trip catalog")
    trips = value["trips"]
    if not 1 <= len(trips) <= 100 or len({x["id"] for x in trips}) != len(trips):
        raise ValueError("Invalid trip catalog")
    for trip in trips:
        profile = trip["profile"]
        if trip["id"] != profile["journey_id"]:
            raise ValueError("Catalog identity differs from the prepared profile")
        variant = trip.get("charging_variant")
        if variant is not None and (
            variant["journey_id"] != trip["id"] or "charging" not in trip["available_services"]
        ):
            raise ValueError("Charging variant does not belong to the selected trip")
    return trips


def public(trips):
    return [
        {
            **{
                key: value
                for key, value in trip.items()
                if key not in ("profile", "outline", "charging_variant")
            },
            "charging_operator_available": operator_available(trip),
            "charging_detour_operator_available": operator_profile_matches(trip, detour=True),
            "charging_layout": trip.get("charging_layout", "on-route")
            if "charging" in trip["available_services"]
            else None,
        }
        for trip in trips
    ]


def select(
    trips,
    trip_id,
    services,
    speed=1,
    *,
    charge_override=None,
    charging_operator=False,
    charging_startup_delay_working_s=0,
):
    from evdt.monitoring.charging_startup import charging_startup_delay

    delay = charging_startup_delay(charging_startup_delay_working_s)
    if type(charging_operator) is not bool:
        raise ValueError("Select a supported charging mode")
    speed = time_scale(speed)
    if not isinstance(services, list) or any(type(x) is not str for x in services):
        raise ValueError("Select services from this preset's available options")
    found = [trip for trip in trips if trip["id"] == trip_id]
    if len(found) != 1:
        raise ValueError("Unknown trip preset")
    trip = found[0]
    if len(services) != len(set(services)) or any(
        x not in trip["available_services"] for x in services
    ):
        raise ValueError("This preset does not support the selected service combination")
    prepared = (
        trip.get("charging_variant")
        if "charging" in services
        and (not charging_operator or operator_profile_matches(trip, detour=True))
        else None
    )
    profile = {**(prepared or trip["profile"]), "services": sorted(services), "time_scale": speed}
    profile.pop("moving_charge_mode", None)
    profile.pop("forecast_candidate", None)
    profile.pop("charging_startup_delay_working_s", None)
    if delay:
        if "charging" not in services:
            raise ValueError("Enable Charging Advisor before setting its startup delay")
        profile["charging_startup_delay_working_s"] = delay
    if "charging" in services:
        if "energy" not in services or speed != 1 or "charging_assignment" not in profile:
            raise ValueError(
                "Charging Advisor requires Energy Forecast, 1×, and a station assignment"
            )
    else:
        assignment = profile.pop("charging_assignment", None)
        if assignment is not None:
            profile["inputs"] = {
                name: digest for name, digest in profile["inputs"].items() if name != assignment
            }
    if charging_operator:
        if "charging" not in services or not operator_available(trip):
            raise ValueError("Operator confirmation requires a verified 1× charging preset")
        profile["moving_charge_mode"] = OPERATOR_MODE
    if charge_override is not None:
        profile["starting_charge_percent"] = starting_charge_percent(charge_override)
    if "initial_charge_percent" in trip:
        profile["initial_charge_percent"] = profile.get(
            "starting_charge_percent", trip["initial_charge_percent"]
        )
    if "expected_working_minutes" in profile:
        profile["maximum_real_life_s"] = min(
            14400, max(900, math.ceil(profile["expected_working_minutes"] * 60 / speed * 1.3 + 600))
        )
    check(profile)
    return profile


def import_evaluation(folder, destination=CATALOG, *, append=False):
    """Prepare operator presets from a completed retained geographic evaluation."""
    folder = folder.resolve(strict=True)
    if not folder.is_relative_to(ROOT / "evidence/energy-v2"):
        raise ValueError("Use retained geographic evidence inside this project")
    study = read(folder / "study-result.json")
    if not study["passed"] or not study["cleanup_verified"]:
        raise ValueError("A complete geographic study is required for preset preparation")
    qualification_path = folder / "qualification.json"
    qualification = read(qualification_path) if qualification_path.is_file() else {}
    if qualification.get("case") != "map-journey" or qualification.get("passed") is not True:
        raise ValueError("Launcher presets require a passing standard map-journey qualification")
    journeys = read(folder / "journey.json")
    trips = []
    regions = {"montreal": "Montréal", "ottawa": "Ottawa", "vancouver": "Metro Vancouver"}
    for index, prepared in enumerate(journeys, 1):
        reference = folder / f"journey-{index:02d}"
        data = read(reference / "run-input.json")
        recipe = data["recipe"]
        trip_result = read(reference / "study-result.json")["trips"][0]
        inputs = {prepared["folder"] + "/" + name: sha for name, sha in prepared["inputs"].items()}
        for name in ("run-input.json", "reference-network.net.xml", "routes.xml"):
            inputs[(reference / name).relative_to(ROOT).as_posix()] = digest(reference / name)
        area = recipe.get("extract") or recipe["id"].split("-")[0]
        profile = {
            "version": "live-geographic-profile/1.0.0",
            "journey_id": recipe["id"],
            "title": recipe["title"],
            "region": regions.get(area, area),
            "prepared": prepared["folder"],
            "reference": reference.relative_to(ROOT).as_posix(),
            "inputs": inputs,
            "expected_working_minutes": trip_result["duration_working_s"] / 60,
            "maximum_real_life_s": min(
                14400, max(900, math.ceil(trip_result["duration_working_s"] * 1.3 + 600))
            ),
            "services": ["energy", "replay"],
        }
        check(profile)
        trips.append(
            {
                "id": recipe["id"],
                "title": recipe["title"],
                "region": profile["region"],
                "distance_km": round(trip_result["distance_m"] / 1000, 2),
                "minutes": round(profile["expected_working_minutes"], 1),
                "stops": len(data["stops"]["stops"]),
                "initial_charge_percent": recipe["initial_soc"] * 100,
                "equipment_kw": recipe["auxiliary_w"] / 1000,
                "mass_kg": recipe["mass_kg"],
                "conditions": recipe.get("conditions", "Declared trip operating conditions"),
                "available_services": profile["services"],
                "energy_unavailable_reason": None,
                "profile": profile,
            }
        )
    existing = load(destination) if append else []
    if not trips:
        raise ValueError("No prepared trips found in the geographic study")
    combined = [*existing, *trips]
    if len({row["id"] for row in combined}) != len(combined):
        raise ValueError(
            "An imported trip ID already exists; review it before replacing the catalog"
        )
    if len(combined) > 100:
        raise ValueError("The local launcher catalog accepts at most 100 trips")
    save_catalog(destination, combined)
    return trips


def attach_charging(trip_id, assignment, destination=CATALOG):
    """Install a reviewed local station template for one prepared trip."""
    from evdt.energy.route_aware.sumo_routes import read_network

    trips = load(destination)
    trip = next((row for row in trips if row["id"] == trip_id), None)
    if trip is None or "energy" not in trip["available_services"]:
        raise ValueError("Select a prepared trip that supports Energy Forecast")
    name = local_path(assignment).relative_to(ROOT).as_posix()
    profile = select(trips, trip_id, ["energy"])
    profile["charging_assignment"] = name
    profile["inputs"] = {**profile["inputs"], name: digest(ROOT / name)}
    prepared, reference, data = check(profile)
    networks = []
    for network_path in (
        prepared / "elevated-route.net.xml",
        reference / "reference-network.net.xml",
    ):
        if network_path.stat().st_size > 32 * 1024**2:
            raise ValueError("The prepared bus network exceeds the station review budget")
        networks.append(read_network(network_path.read_bytes(), max_bytes=32 * 1024**2))
    network, runtime_network = networks
    network_sha256 = read(prepared / "geometry.json")["network"]["sha256"]
    if network.sha256 != network_sha256:
        raise ValueError("Station review needs the same bus network used by the forecast")
    assigned = charging_assignment(
        profile, str(UUID(int=0)), network.sha256, data["mission"]["route_edges"]
    )
    stops = data["stops"]["stops"]
    for entry in assigned.bays:
        bay = entry.bay
        if bay.access_problem(
            network,
            connector=assigned.policy.connector,
            vehicle_length_m=assigned.policy.vehicle_length_m,
        ):
            raise ValueError(f"Charging bay {bay.bay_id} is incompatible with the bus or its lane")
        lane = network.lanes[bay.lane_id]
        runtime_lane = runtime_network.lanes.get(bay.lane_id)
        if (
            runtime_lane is None
            or runtime_lane.internal
            or runtime_lane.edge_id != lane.edge_id
            or bay.end_offset_m >= min(lane.length_m, runtime_lane.length_m)
        ):
            raise ValueError(f"Charging bay {bay.bay_id} must fit the forecast and runtime lane")
        if entry.detour is None:
            if (
                entry.external_index >= len(assigned.baseline_edges) - 1
                or lane.edge_id != assigned.baseline_edges[entry.external_index]
                or any(
                    stop["external_index"] == entry.external_index
                    and bay.start_offset_m <= stop["offset_m"] <= bay.end_offset_m
                    for stop in stops
                )
            ):
                raise ValueError(f"Charging bay {bay.bay_id} conflicts with the prepared itinerary")
        elif lane.edge_id != entry.detour.edges[entry.detour.bay_edge_index] or any(
            entry.detour.branch_index < stop["external_index"] < entry.detour.rejoin_index
            for stop in stops
        ):
            raise ValueError(
                f"Charging bay {bay.bay_id} detour conflicts with the prepared itinerary"
            )
    trip["profile"] = profile
    trip.pop("charging_variant", None)
    trip.pop("charging_layout", None)
    trip["available_services"] = sorted(set(trip["available_services"]) | {"charging"})
    save_catalog(destination, trips)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluation", type=Path, nargs="?")
    importing = parser.add_mutually_exclusive_group()
    importing.add_argument("--append", action="store_true", help="Keep the existing presets")
    importing.add_argument("--replace", action="store_true", help="Replace the existing presets")
    parser.add_argument("--attach-charging", nargs=2, metavar=("TRIP_ID", "ASSIGNMENT"))
    args = parser.parse_args()
    if args.attach_charging and args.evaluation is None and not (args.append or args.replace):
        attach_charging(*args.attach_charging)
        print("Reviewed station assignment attached. No services started.")
        raise SystemExit(0)
    if args.evaluation is None or args.attach_charging:
        parser.error("Select an evaluation or one charging assignment")
    if CATALOG.exists() and not (args.append or args.replace):
        parser.error("An existing catalog requires --append or --replace")
    result = import_evaluation(args.evaluation, append=args.append)
    print(json.dumps({"presets": len(result), "services_started": False}))
