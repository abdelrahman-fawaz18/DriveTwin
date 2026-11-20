"""Select attributable terrain tiles without tying the estimator to a region."""

import json
from pathlib import Path


class RasterCatalog:
    def __init__(self, path, root):
        self.root = root.resolve()
        self.path = Path(path).resolve()
        if not self.path.is_relative_to(self.root / "data/downloads/energy-v2"):
            raise ValueError("Elevation manifests must stay in the project terrain cache")
        record = json.loads(self.path.read_bytes())
        self.datum = record["vertical_datum"]
        self.tiles = record.get("tiles")
        if self.tiles is None:
            self.tiles = [
                {
                    "manifest": self.path.relative_to(self.root).as_posix(),
                    "layer": record.get("layer", "dtm"),
                    "bounds_lon_lat": [-180, -90, 180, 90],
                }
            ]
        if not isinstance(self.tiles, list) or not 1 <= len(self.tiles) <= 256:
            raise ValueError("Elevation catalog accepts one to 256 tiles")
        self.records = []
        for tile in self.tiles:
            layer = tile["layer"]
            if layer not in ("dtm", "dsm"):
                raise ValueError("Elevation layer must be dtm or dsm")
            west, south, east, north = tile["bounds_lon_lat"]
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError("Invalid elevation coverage bounds")
            manifest = (self.root / tile["manifest"]).resolve()
            if not manifest.is_relative_to(self.root / "data/downloads/energy-v2"):
                raise ValueError("Elevation tile is outside the project terrain cache")
            metadata = json.loads(manifest.read_bytes())
            if not all(
                metadata.get(k) for k in ("dataset_id", "source_url", "vertical_datum", "license")
            ):
                raise ValueError("Elevation needs dataset identity, source URL, datum, and license")
            if metadata["vertical_datum"] != self.datum or metadata.get("layer", layer) != layer:
                raise ValueError("Elevation layers must share the declared vertical datum")
            self.records.append((layer, (west, south, east, north), manifest, metadata))

    def select(self, longitude, latitude, layer):
        for kind, (w, s, e, n), path, metadata in self.records:
            if kind == layer and w <= longitude <= e and s <= latitude <= n:
                return path, metadata
        raise ValueError(f"No {layer} coverage at {longitude:.6f}, {latitude:.6f}")
