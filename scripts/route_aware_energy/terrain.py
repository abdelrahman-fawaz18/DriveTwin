"""Download a bounded NRCan terrain clip and inspect its local raster metadata.

Run with the separately pinned terrain-preparation environment. This tool is not
imported by an Energy Worker and never starts a vehicle or container.
"""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import rasterio
from rasterio.warp import transform_bounds

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "downloads" / "energy-v2"
ENDPOINT = "https://datacube.services.geo.ca/ows/elevation"
MAX_BYTES = 256 * 1024 * 1024


def fetch(
    bounds: tuple[float, float, float, float], resolution: float, *, layer: str = "dtm"
) -> dict:
    if layer not in ("dtm", "dsm"):
        raise ValueError("Only documented NRCan terrain and surface layers are supported")
    west, south, east, north = bounds
    if not all(math.isfinite(v) for v in (*bounds, resolution)):
        raise ValueError("Finite coordinates and resolution required")
    if not (-141 <= west < east <= -52 and 41 <= south < north <= 84):
        raise ValueError("A Canadian geographic bounding box is required")
    if not 2 <= resolution <= 100:
        raise ValueError("Resolution must be between 2 and 100 meters")
    projected = transform_bounds("EPSG:4326", "EPSG:3979", *bounds, densify_pts=21)
    left, bottom, right, top = projected
    cells = math.ceil((right - left) / resolution) * math.ceil((top - bottom) / resolution)
    if cells > 1_000_000:
        raise ValueError("Clip exceeds the one-million-cell preprocessing budget")
    params = {
        "SERVICE": "WCS",
        "VERSION": "1.1.1",
        "REQUEST": "GetCoverage",
        "FORMAT": "image/geotiff",
        "IDENTIFIER": layer,
        "BOUNDINGBOX": ",".join(str(v) for v in projected) + ",urn:ogc:def:crs:EPSG::3979",
        "GRIDBASECRS": "urn:ogc:def:crs:EPSG::3979",
        "GRIDOFFSETS": f"{resolution},{-resolution}",
        "GRIDORIGIN": f"{left},{top}",
        "GRIDCS": "urn:ogc:def:cs:OGC:0.0:Grid2dSquareCS",
        "GRIDTYPE": "urn:ogc:def:method:WCS:1.1:2dSimpleGrid",
    }
    url = ENDPOINT + "?" + urlencode(params)
    key = hashlib.sha256(url.encode()).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    raster_path = CACHE / f"{key}.tif"
    manifest = CACHE / f"{key}.json"
    if manifest.exists() and raster_path.exists():
        old = json.loads(manifest.read_text(encoding="utf-8"))
        if old["sha256"] != hashlib.sha256(raster_path.read_bytes()).hexdigest():
            raise ValueError("Cached terrain fingerprint changed")
        return old
    partial = CACHE / f"{key}.partial"
    if partial.exists():
        raise ValueError("Prior partial download exists; inspect it before a new attempt")
    started = time.monotonic()
    digest = hashlib.sha256()
    count = 0
    with (
        urlopen(
            Request(url, headers={"User-Agent": "DriveTwin terrain preparation"}), timeout=40
        ) as response,
        partial.open("xb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_BYTES or time.monotonic() - started > 90:
                raise ValueError("Terrain download exceeded its byte/time budget")
            output.write(chunk)
            digest.update(chunk)
        final_url = response.url
    with rasterio.open(partial) as dataset:
        if dataset.count != 1 or dataset.width * dataset.height > 1_000_000:
            raise ValueError("Unexpected terrain raster dimensions")
        if dataset.crs != rasterio.crs.CRS.from_epsg(3979):
            raise ValueError("Terrain response uses an unexpected coordinate system")
        if any(abs(value - resolution) > resolution * 0.01 for value in dataset.res):
            raise ValueError("Terrain response resolution differs from the requested grid")
        values = dataset.read(1, masked=True)
        if values.count() == 0:
            raise ValueError("Terrain clip has no valid coverage")
        low, high = float(values.min()), float(values.max())
        if not (-1000 <= low <= high <= 10000):
            raise ValueError("Terrain heights are outside the supported domain")
        record = {
            "provider": "NRCan HRDEM Mosaic " + layer.upper(),
            "layer": layer,
            "request_url": url,
            "response_url": final_url,
            "sha256": digest.hexdigest(),
            "bytes": count,
            "geographic_bounds": bounds,
            "projected_bounds": list(dataset.bounds),
            "horizontal_crs": dataset.crs.to_string(),
            "resolution_m": list(dataset.res),
            "width": dataset.width,
            "height": dataset.height,
            "valid_cells": int(values.count()),
            "minimum_m": low,
            "maximum_m": high,
            "license": "Open Government Licence - Canada",
            "vertical_datum": "CGVD2013",
            "catalog": "https://datacube.services.geo.ca/stac/api/",
            "file": raster_path.relative_to(ROOT).as_posix(),
            "downloaded_real_life_unix_s": time.time(),
            "acquisition_date": None,
            "use": "terrain; bridge and tunnel roadway heights require separate information",
        }
    partial.rename(raster_path)
    manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bounds", type=float, nargs=4, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH")
    )
    parser.add_argument("--resolution-m", type=float, default=10)
    args = parser.parse_args()
    print(json.dumps(fetch(tuple(args.bounds), args.resolution_m), indent=2))


if __name__ == "__main__":
    main()
