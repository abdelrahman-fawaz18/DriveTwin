"""Current-demo selection preserves the exact successful local candidate."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/demo"))
from current_demo import resolve_current, select  # noqa: E402
from package_demo import inventory, sha  # noqa: E402
from serve import resolve_site  # noqa: E402


class CurrentDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / ".private/browser-demo"
        self.site = self.base / "reviewed/site"
        (self.site / "cases/montreal").mkdir(parents=True)
        (self.site / "cases/montreal/manifest.json").write_text("{}")
        (self.site / "demo.html").write_text("reviewed application")
        self.report = self.base / "reviewed/browser.json"
        self.report.write_text(
            json.dumps(
                {
                    "version": "browser-demo-browser-check/1.0.0",
                    "passed": True,
                    "fixture": False,
                    "manifest_sha256": sha(self.site / "cases/montreal/manifest.json"),
                }
            )
        )
        self.publication = self.site.parent / "site-publication.json"
        self.publication.write_text(
            json.dumps(
                {
                    "version": "browser-demo-publication-candidate/1.0.0",
                    "files_sha256": inventory(self.site),
                    "verification_sha256": sha(self.report),
                    "review": {"privacy": "passed", "secret_scan": "passed"},
                }
            )
        )

    def test_explicit_selection_wins_over_newer_ready_preparation(self):
        ready = self.base / "preparation-20990101/READY.json"
        ready.parent.mkdir()
        ready.write_text(json.dumps({"site": "missing-old-site"}))
        select(self.site, self.report, root=self.root)
        self.assertEqual(resolve_current(root=self.root), self.site)

    def test_changed_site_report_or_publication_fails_closed(self):
        for path in (self.site / "demo.html", self.report, self.publication):
            with self.subTest(path=path):
                original = path.read_bytes()
                select(self.site, self.report, root=self.root)
                path.write_bytes(original + b" ")
                with self.assertRaises(ValueError):
                    resolve_current(root=self.root)
                path.write_bytes(original)

    def test_fixture_and_outside_private_path_are_rejected(self):
        report = json.loads(self.report.read_bytes())
        report["fixture"] = True
        self.report.write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            select(self.site, self.report, root=self.root)
        with self.assertRaises(ValueError):
            select(self.root, self.report, root=self.root)

    def test_malformed_current_receipt_is_refused(self):
        (self.base / "current.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            resolve_current(root=self.root)

    def test_legacy_ready_discovery_remains_bounded_and_readable(self):
        ready = self.base / "preparation-20990101/READY.json"
        ready.parent.mkdir()
        ready.write_text(json.dumps({"site": self.site.relative_to(self.root).as_posix()}))
        self.assertEqual(resolve_current(root=self.root), self.site)
        ready.write_text("[]")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            resolve_current(root=self.root)

    def test_malformed_review_metadata_is_refused(self):
        receipt = json.loads(self.publication.read_bytes())
        receipt["review"] = []
        self.publication.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "privacy reviews"):
            select(self.site, self.report, root=self.root)

    @unittest.skipIf(sys.platform == "win32", "Linux covers links without Windows privileges")
    def test_existing_temporary_link_does_not_overwrite_another_file(self):
        sentinel = self.root / "unrelated.json"
        sentinel.write_bytes(b"preserve unrelated bytes")
        os.link(sentinel, self.base / "current.tmp")
        select(self.site, self.report, root=self.root)
        self.assertEqual(sentinel.read_bytes(), b"preserve unrelated bytes")
        self.assertEqual(resolve_current(root=self.root), self.site)

    @unittest.skipIf(sys.platform == "win32", "Linux covers links without Windows privileges")
    def test_redirected_pointer_and_explicit_site_are_refused(self):
        sentinel = self.root / "unrelated.json"
        sentinel.write_text("{}")
        (self.base / "current.json").symlink_to(sentinel)
        with self.assertRaisesRegex(ValueError, "redirected"):
            resolve_current(root=self.root)
        (self.site / "unrelated.json").symlink_to(sentinel)
        with self.assertRaisesRegex(ValueError, "redirected"):
            resolve_site(self.site, root=self.root)

    def test_explicit_valid_site_remains_supported(self):
        self.assertEqual(resolve_site(self.site, root=self.root), self.site)


if __name__ == "__main__":
    unittest.main()
