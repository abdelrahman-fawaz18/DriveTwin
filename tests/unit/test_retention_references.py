"""A source checkout can contain original evidence; its directory name is not disposable."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "retention_references", ROOT / "scripts/release/check_retention_references.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class RetentionReferenceTests(unittest.TestCase):
    def test_report_inside_a_checkout_is_protected_but_unused_files_are_not(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            name = ".runtime/clean-checkout-example/evidence/run/qualification.json"
            report = root / name
            report.parent.mkdir(parents=True)
            report.write_text("original result")
            source = root / "verification.json"
            source.write_text(json.dumps({"private_report": name}))
            unused = root / ".runtime/unused.json"
            unused.write_text("regenerable")
            plan = {"remove": [{"path": name}, {"path": ".runtime/unused.json"}]}
            self.assertEqual(
                guard.conflicts(root, plan, ["verification.json"]),
                [{"path": name, "source": "verification.json", "line": 1}],
            )

    def test_generic_directory_mentions_do_not_pin_all_historical_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "evidence/old/result.json"
            path.parent.mkdir(parents=True)
            path.write_text("old result")
            (root / "guide.md").write_text("Results are in evidence/old/.")
            self.assertEqual(
                guard.conflicts(
                    root, {"remove": [{"path": "evidence/old/result.json"}]}, ["guide.md"]
                ),
                [],
            )

    def test_outside_cleanup_target_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "escapes"):
                guard.conflicts(Path(folder), {"remove": [{"path": "../outside"}]}, [])


if __name__ == "__main__":
    unittest.main()
