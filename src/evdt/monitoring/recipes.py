"""Explicit reproducibility inputs and safe public sampling metadata."""

import hashlib
import json
from pathlib import Path

from evdt.config import ROOT
from evdt.monitoring.formats import operational_format, profile


def inventory_digest(paths) -> str:
    hashes = {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    return hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def inputs(scenario: Path, osm: Path, catalog_version: str = "1.2.0") -> dict[str, str]:
    registry = profile()
    schema_version = operational_format(catalog_version)["schema_version"]
    paths = {
        "roads.osm.xml": osm,
        **{
            name: scenario / name
            for name in (
                "mission.json",
                "bus-parameters.json",
                "plant.json",
                "import-review.json",
                "stations.json",
            )
        },
        **(
            {"sensor-extension.json": scenario / "sensor-extension.json"}
            if (scenario / "sensor-extension.json").is_file()
            else {}
        ),
        "catalog": ROOT / registry["catalogs"][catalog_version],
        "schema": ROOT / "contracts" / registry["schemas"][schema_version],
    }
    result = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    if (scenario / "prepared-journey.json").is_file():
        for name in ("prepared-journey.json", "network.net.xml", "routes.rou.xml"):
            result[name] = hashlib.sha256((scenario / name).read_bytes()).hexdigest()
    # Keep input metadata bounded as the number of source files grows.
    route_aware = ROOT / "src/evdt/energy/route_aware"
    result["source-inventory/1.0.0"] = inventory_digest(
        path for path in (ROOT / "src").rglob("*.py") if not path.is_relative_to(route_aware)
    )
    package = list(route_aware.rglob("*.py"))
    if package:
        result["route-aware-package-inventory/2.0.0"] = inventory_digest(package)
    return result
