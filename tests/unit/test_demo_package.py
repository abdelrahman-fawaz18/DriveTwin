"""A functional browser fixture cannot qualify a publication candidate."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "demo_package", Path(__file__).resolve().parents[2] / "scripts/demo/package_demo.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DemoPackageTest(unittest.TestCase):
    def test_packaging_preserves_relative_assets_and_adds_notices_and_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "apps/dashboard/dist-demo"
            build.mkdir(parents=True)
            (build / "demo.html").write_text(
                '<script src="./assets/demo.js"></script>', encoding="utf-8"
            )
            (build / "assets").mkdir()
            (build / "assets/demo.js").write_text("// unit fixture", encoding="utf-8")
            case = root / "case"
            case.mkdir()
            (case / "common.json").write_text("{}", encoding="utf-8")
            (case / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "unit-fixture",
                        "files": {
                            "common": {
                                "path": "common.json",
                                "bytes": 2,
                                "sha256": MODULE.sha(case / "common.json"),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            for name in (
                "LICENSE",
                "apps/dashboard/node_modules/react/LICENSE",
                "apps/dashboard/node_modules/leaflet/LICENSE",
                "apps/dashboard/src/ui/assets/Inter-LICENSE.txt",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Unit-test notice", encoding="utf-8")
            report = root / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "version": "browser-demo-browser-check/1.0.0",
                        "fixture": False,
                        "passed": True,
                        "manifest_sha256": MODULE.sha(case / "manifest.json"),
                        "build_sha256": MODULE.inventory(build),
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(MODULE, "ROOT", root),
                patch.object(MODULE, "review_site", return_value={"unit_test": True}),
            ):
                receipt = MODULE.package(case, report, root / "site")
            self.assertEqual(
                (root / "site/index.html").read_bytes(), (build / "demo.html").read_bytes()
            )
            self.assertEqual((root / "site/cases/montreal/common.json").read_bytes(), b"{}")
            self.assertIn("notices/Inter.txt", receipt["files_sha256"])
            self.assertTrue((root / "site-publication.json").is_file())
            self.assertFalse((root / "READY.json").exists())

    def test_fixture_receipt_cannot_be_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report.json"
            report.write_text(
                json.dumps(
                    {"version": "browser-demo-browser-check/1.0.0", "fixture": True, "passed": True}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "fixtures cannot be released"):
                MODULE.package(root, report, root / "site")
            self.assertFalse((root / "site").exists())

    def test_changed_case_is_rejected_before_copying(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            report = root / "report.json"
            report.write_text(
                json.dumps(
                    {
                        "version": "browser-demo-browser-check/1.0.0",
                        "fixture": False,
                        "passed": True,
                        "manifest_sha256": "0" * 64,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "changed after browser verification"):
                MODULE.package(root, report, root / "site")
            self.assertFalse((root / "site").exists())


if __name__ == "__main__":
    unittest.main()
