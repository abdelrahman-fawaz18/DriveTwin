"""Launch-page changes participate in the shared check's source-stability guard."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dev_inventory", ROOT / "scripts/dev.py")
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


class SourceInventoryTests(unittest.TestCase):
    def test_launch_assets_and_browser_checks_are_bound_to_the_report(self):
        names = (
            "apps/launcher/app.js",
            "apps/launcher/trip-map.js",
            "apps/launcher/index.html",
            "apps/launcher/style.css",
            "qualification/browser/check_launch_app.mjs",
            "qualification/browser/dashboard/monitoring.spec.mjs",
            "DriveTwin.cmd",
        )
        directory = ROOT / ".runtime" if (ROOT / ".runtime").is_dir() else None
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            root = Path(temp)
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("original")
            vendor = root / "qualification/browser/node_modules/tool/index.mjs"
            vendor.parent.mkdir(parents=True)
            vendor.write_text("installed dependency")
            with patch.object(dev, "ROOT", root):
                before = dev.source_hashes()
                self.assertTrue(set(names) <= before.keys())
                self.assertNotIn(vendor.relative_to(root).as_posix(), before)
                for name in names:
                    with self.subTest(name=name):
                        (root / name).write_text("changed")
                        self.assertNotEqual(before[name], dev.source_hashes()[name])
