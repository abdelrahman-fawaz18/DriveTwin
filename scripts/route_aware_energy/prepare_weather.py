"""Convert an issued ECCC city forecast into a reviewed assignment revision."""

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evdt.energy.route_aware.assignment import Assignment  # noqa: E402
from evdt.energy.route_aware.contracts import Conditions, Origin, RouteContext  # noqa: E402
from evdt.energy.route_aware.environment import TimeMapping, wind_vector  # noqa: E402
from evdt.energy.route_aware.validation import number  # noqa: E402
from evdt.energy.route_aware.wire import canonical_bytes, decode_record, fingerprint  # noqa: E402

API_HOST = "api.weather.gc.ca"
CITY_ENDPOINT = f"https://{API_HOST}/collections/citypageweather-realtime/items/"
DIRECTIONS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()
MAX_BYTES = 512 * 1024


def utc(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Weather times require an explicit UTC offset")
    return parsed.timestamp()


def weather_hours(raw, site, fetched):
    if raw["type"] != "Feature" or raw["id"] != site:
        raise ValueError("Weather response returned a different site")
    properties = raw["properties"]
    group = properties["hourlyForecastGroup"]
    issued = utc(group["timestamp"]["en"])
    updated = utc(properties["lastUpdated"])
    if not 0 <= fetched - issued <= 8 * 3600 or not issued <= updated <= fetched:
        raise ValueError("Weather issue or publication time is outside the allowed age")
    rows = []
    for record in group["hourlyForecasts"]:
        timestamp = utc(record["timestamp"])
        speed = record["wind"]["speed"]
        temperature = record["temperature"]
        if (
            timestamp < issued
            or speed["units"]["en"] != "km/h"
            or temperature["units"]["en"] != "C"
        ):
            raise ValueError("Weather forecast has unsupported units or time")
        metres_per_second = number(speed["value"]["en"], "wind speed km/h", 0, 216) / 3.6
        direction = record["wind"]["direction"]["value"]["en"].upper()
        bearing = DIRECTIONS.index(direction) * 22.5 if metres_per_second else 0
        east, north = wind_vector(metres_per_second, bearing)
        kelvin = (
            number(temperature["value"]["en"], "air temperature Celsius", -73.15, 66.85) + 273.15
        )
        rows.append((timestamp, east, north, kelvin))
    if not 1 <= len(rows) <= 48 or any(
        following[0] - first[0] != 3600 for first, following in zip(rows, rows[1:])
    ):
        raise ValueError("Hourly forecast must have ordered, contiguous coverage")
    return issued, rows


def conditions_for_route(base: tuple[Conditions, ...], hours, mapping, origin, fetched):
    projected = []
    for condition in base:
        cursor = condition.from_working_s
        combined_origin = replace(
            origin,
            reference=f"{origin.reference}; base condition SHA-256 {fingerprint(condition)}",
        )
        for stamp, east, north, temperature in hours:
            start = max(condition.from_working_s, mapping.working(stamp))
            end = min(condition.until_working_s, mapping.working(stamp + 3600))
            if start >= end:
                continue
            if abs(start - cursor) > 1e-7:
                raise ValueError("Issued weather does not cover the assignment interval")
            projected.append(
                replace(
                    condition,
                    origin=combined_origin,
                    available_real_life_s=max(fetched, condition.available_real_life_s),
                    from_working_s=start,
                    until_working_s=end,
                    wind_basis="forecast",
                    wind_east_m_s=east,
                    wind_north_m_s=north,
                    ambient_temperature_k=temperature,
                    pressure_pa=None,
                )
            )
            cursor = end
        if abs(cursor - condition.until_working_s) > 1e-7:
            raise ValueError("Issued weather does not cover the assignment interval")
    return tuple(projected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True, help="Prepared RouteContext JSON")
    parser.add_argument(
        "--coverage", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"), required=True
    )
    parser.add_argument("--weather-time-at-working-zero", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[a-z]{2}-[a-z0-9]{1,12}", args.site) is None:
        parser.error("Supply a City Page Weather feature ID")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".private") or output.exists():
        parser.error("Choose a new output directory under .private")
    assignment = decode_record(Assignment, json.loads(args.assignment.read_bytes()))
    route = decode_record(RouteContext, json.loads(args.route.read_bytes()))
    if route.network_sha256 != assignment.network_sha256 or (
        assignment.scenario_route_revision is not None
        and assignment.scenario_route_revision != route.revision
    ):
        raise ValueError("Prepared route does not match the approved assignment")
    if assignment.scenario is not None and assignment.scenario.roads:
        raise ValueError(
            "Prepare localized weather separately when the assignment has road overrides"
        )
    west, south, east, north = args.coverage
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("A valid reviewed geographic coverage box is required")

    def covered(longitude, latitude):
        return (
            longitude is not None
            and latitude is not None
            and west <= longitude <= east
            and south <= latitude <= north
        )

    if any(
        not covered(point.longitude_deg, point.latitude_deg)
        for occurrence in route.occurrences
        for point in occurrence.shape
    ):
        raise ValueError("An assigned road lies outside the reviewed weather coverage")
    url = CITY_ENDPOINT + args.site + "?f=json&lang=en"
    with urlopen(
        Request(url, headers={"User-Agent": "DriveTwin Energy Forecast"}), timeout=15
    ) as response:
        if urlsplit(response.geturl()).hostname != API_HOST:
            raise ValueError("Weather server redirected to an unapproved host")
        payload = response.read(MAX_BYTES + 1)
    fetched = time.time()
    if len(payload) > MAX_BYTES:
        raise ValueError("Weather response exceeded its byte budget")
    raw = json.loads(payload)
    if not covered(*raw["geometry"]["coordinates"]):
        raise ValueError("Weather site lies outside the reviewed coverage box")
    issued, hours = weather_hours(raw, args.site, fetched)
    digest = hashlib.sha256(payload).hexdigest()
    origin = Origin(
        "forecast",
        "Environment and Climate Change Canada",
        f"City Page Weather {args.site} SHA-256 {digest}",
    )
    mapping = TimeMapping(0, utc(args.weather_time_at_working_zero), 1)
    revision = replace(
        assignment,
        revision=assignment.revision + 1,
        scenario_route_revision=route.revision,
        conditions=conditions_for_route(assignment.conditions, hours, mapping, origin, fetched),
    )
    output.mkdir(parents=True)
    (output / "provider.json").write_bytes(payload)
    (output / "base-assignment.json").write_bytes(canonical_bytes(assignment) + b"\n")
    (output / "assignment.json").write_bytes(canonical_bytes(revision) + b"\n")
    (output / "receipt.json").write_text(
        json.dumps(
            {
                "url": url,
                "provider_sha256": digest,
                "issued_real_life_s": issued,
                "fetched_real_life_s": fetched,
                "coverage": args.coverage,
                "working_zero": args.weather_time_at_working_zero,
                "route_revision": route.revision,
                "base_assignment_sha256": fingerprint(assignment),
                "assignment_sha256": fingerprint(revision),
                "wind_reference_height": "unreported",
                "air_density": "declared vehicle profile",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Prepared revision {revision.revision} with {len(revision.conditions)} issued weather intervals."
    )


if __name__ == "__main__":
    main()
