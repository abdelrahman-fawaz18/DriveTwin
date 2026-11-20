"""The expanded model remains byte-bound inside the unchanged manifest limit."""

import hashlib
import json
import unittest
from unittest.mock import patch

from evdt.config import ROOT
from evdt.monitoring.recipes import inputs


class RouteRecipeTests(unittest.TestCase):
    def test_every_model_file_is_bound_without_growing_manifest(self):
        result = inputs(
            ROOT / "simulation/montreal", ROOT / "data/public/montreal-core-v1/roads.osm.xml"
        )
        package = {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "src/evdt/energy/route_aware").rglob("*.py"))
        }
        self.assertGreater(len(package), 0)
        self.assertLessEqual(len(result), 128)
        self.assertEqual(
            result["route-aware-package-inventory/2.0.0"],
            hashlib.sha256(
                json.dumps(package, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        target = ROOT / "src/evdt/energy/route_aware/forecast.py"
        original = type(target).read_bytes
        with patch.object(
            type(target),
            "read_bytes",
            lambda path: original(path) + b"\n" if path == target else original(path),
        ):
            changed = inputs(
                ROOT / "simulation/montreal", ROOT / "data/public/montreal-core-v1/roads.osm.xml"
            )
        self.assertNotEqual(
            changed["route-aware-package-inventory/2.0.0"],
            result["route-aware-package-inventory/2.0.0"],
        )

    def test_source_additions_keep_prepared_manifest_within_limit(self):
        scenario = ROOT / "simulation/montreal"
        roads = ROOT / "data/public/montreal-core-v1/roads.osm.xml"
        added = ROOT / "src/evdt/monitoring/example.py"
        baseline = inputs(scenario, roads)
        original_glob = type(added).rglob
        original_read = type(added).read_bytes
        original_exists = type(added).is_file
        prepared = {"prepared-journey.json", "network.net.xml", "routes.rou.xml"}
        with (
            patch.object(
                type(added),
                "rglob",
                lambda path, pattern: (
                    [*original_glob(path, pattern), added]
                    if path == ROOT / "src"
                    else original_glob(path, pattern)
                ),
            ),
            patch.object(
                type(added),
                "read_bytes",
                lambda path: (
                    b"example" if path == added or path.name in prepared else original_read(path)
                ),
            ),
            patch.object(
                type(added),
                "is_file",
                lambda path: path == scenario / "prepared-journey.json" or original_exists(path),
            ),
        ):
            changed = inputs(scenario, roads)
        self.assertLessEqual(len(changed), 128)
        self.assertTrue(prepared.issubset(changed))
        self.assertNotEqual(changed["source-inventory/1.0.0"], baseline["source-inventory/1.0.0"])
        self.assertEqual(
            changed["route-aware-package-inventory/2.0.0"],
            baseline["route-aware-package-inventory/2.0.0"],
        )
