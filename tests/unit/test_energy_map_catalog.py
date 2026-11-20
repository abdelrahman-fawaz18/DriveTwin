"""Coverage, attribution, and isolation checks for region-independent raster inputs."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/geographic"))
from raster_catalog import RasterCatalog  # noqa: E402


class RasterCatalogTests(unittest.TestCase):
    def test_layer_and_geographic_coverage_do_not_fabricate_a_flat_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "data/downloads/energy-v2"
            cache.mkdir(parents=True)
            tiles = []
            for layer in ("dtm", "dsm"):
                path = cache / (layer + ".json")
                path.write_text(
                    json.dumps(
                        dict(
                            dataset_id="survey",
                            source_url="https://example.org/survey",
                            vertical_datum="local-survey",
                            license="CC0",
                            layer=layer,
                        )
                    )
                )
                tiles.append(
                    dict(
                        manifest=path.relative_to(root).as_posix(),
                        layer=layer,
                        bounds_lon_lat=[7, 46, 8, 47],
                    )
                )
            catalog = cache / "catalog.json"
            catalog.write_text(json.dumps(dict(vertical_datum="local-survey", tiles=tiles)))
            reader = RasterCatalog(catalog, root)
            self.assertEqual(reader.select(7.5, 46.5, "dsm")[1]["layer"], "dsm")
            with self.assertRaisesRegex(ValueError, "No dtm coverage"):
                reader.select(9, 46.5, "dtm")
            tiles[0]["manifest"] = "../outside.json"
            catalog.write_text(json.dumps(dict(vertical_datum="local-survey", tiles=tiles)))
            with self.assertRaisesRegex(ValueError, "outside"):
                RasterCatalog(catalog, root)

    def test_mixed_vertical_datums_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "data/downloads/energy-v2"
            cache.mkdir(parents=True)
            tile = cache / "ground.json"
            tile.write_text(
                json.dumps(
                    dict(
                        dataset_id="survey",
                        source_url="https://example.org",
                        vertical_datum="other",
                        license="CC0",
                    )
                )
            )
            catalog = cache / "catalog.json"
            catalog.write_text(
                json.dumps(
                    dict(
                        vertical_datum="one",
                        tiles=[
                            dict(
                                manifest=tile.relative_to(root).as_posix(),
                                layer="dtm",
                                bounds_lon_lat=[7, 46, 8, 47],
                            )
                        ],
                    )
                )
            )
            with self.assertRaisesRegex(ValueError, "datum"):
                RasterCatalog(catalog, root)
