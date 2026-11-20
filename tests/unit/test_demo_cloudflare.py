"""Hosting preparation must reject changed or unqualified public candidates."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "demo_cloudflare", Path(__file__).resolve().parents[2] / "scripts/demo/cloudflare_bundle.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CloudflareBundleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / ".private/browser-demo/upload"

    def candidate(self, root):
        base = root / ".private/browser-demo/prepared"
        site = base / "site"
        (site / "cases/montreal").mkdir(parents=True)
        (site / "index.html").write_text("Recorded demo", encoding="utf-8")
        (site / "cases/montreal/manifest.json").write_text("{}", encoding="utf-8")
        manifest = MODULE.sha(site / "cases/montreal/manifest.json")
        (base / "browser").mkdir()
        report = base / "browser/browser-report.json"
        report.write_text(
            json.dumps(
                {
                    "version": "browser-demo-browser-check/1.0.0",
                    "passed": True,
                    "fixture": False,
                    "manifest_sha256": manifest,
                }
            )
        )
        receipt = {
            "version": "browser-demo-publication-candidate/1.0.0",
            "case_id": "unit-fixture",
            "manifest_sha256": manifest,
            "verification_sha256": MODULE.sha(report),
            "files_sha256": MODULE.inventory(site),
            "review": {"privacy": "passed", "secret_scan": "passed"},
        }
        (base / "site-publication.json").write_text(json.dumps(receipt))
        return site

    def test_archive_contains_only_verified_files_and_host_configuration(self):
        site = self.candidate(self.root)
        before = MODULE.inventory(site)
        with patch.object(MODULE, "review_site", return_value={"unit_test": True}):
            receipt = MODULE.prepare(site, self.output, root=self.root)
        self.assertEqual(MODULE.inventory(site), before)
        with zipfile.ZipFile(self.output / "drivetwin-demo.zip") as archive:
            self.assertEqual(set(archive.namelist()), set(before) | {"_headers", "404.html"})
            for name, digest in receipt["files_sha256"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
        self.assertIn("frame-ancestors 'none'", MODULE.HEADERS)

    def test_extra_file_is_rejected_before_output_is_created(self):
        site = self.candidate(self.root)
        (site / "unreviewed.txt").write_text("Not part of the reviewed candidate")
        with self.assertRaisesRegex(ValueError, "candidate changed"):
            MODULE.prepare(site, self.output, root=self.root)
        self.assertFalse(self.output.exists())

    def test_changed_or_fixture_browser_report_is_rejected(self):
        site = self.candidate(self.root)
        report = site.parent / "browser/browser-report.json"
        report.write_text(json.dumps({"passed": True, "fixture": True}))
        with self.assertRaisesRegex(ValueError, "actual-case browser verification"):
            MODULE.prepare(site, self.output, root=self.root)
        self.assertFalse(self.output.exists())

    def test_nested_output_cannot_recursively_copy_the_source(self):
        site = self.candidate(self.root)
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            MODULE.prepare(site, site / "nested-output", root=self.root)
        self.assertFalse((site / "nested-output").exists())

    def current_report(self):
        site = self.candidate(self.root)
        report = self.root / ".private/reviews/current-browser.json"
        report.parent.mkdir(parents=True)
        (site.parent / "browser/browser-report.json").replace(report)
        from current_demo import select

        select(site, report, root=self.root)
        return site, report

    def test_current_candidate_uses_its_checked_external_browser_report(self):
        site, _ = self.current_report()
        with patch.object(MODULE, "review_site", return_value={"unit_test": True}):
            receipt = MODULE.prepare(site, self.output, root=self.root)
        self.assertEqual(receipt["case_id"], "unit-fixture")

    def test_changed_current_report_is_rejected_before_output(self):
        site, report = self.current_report()
        report.write_bytes(report.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            MODULE.prepare(site, self.output, root=self.root)
        self.assertFalse(self.output.exists())

    def test_redirected_site_or_output_ancestor_is_rejected_before_copy(self):
        site = self.candidate(self.root)
        alias = site.parent.parent / "alias"
        target = site.parent.parent / "output-target"
        target.mkdir()
        output_alias = site.parent.parent / "output-alias"
        try:
            alias.symlink_to(site.parent, target_is_directory=True)
            output_alias.symlink_to(target, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory symlink fixture unavailable: {error}")
        with self.assertRaises(ValueError):
            MODULE.prepare(alias / "site", self.output, root=self.root)
        with self.assertRaises(ValueError):
            MODULE.prepare(site, output_alias / "new-upload", root=self.root)
        self.assertFalse(self.output.exists())
        self.assertFalse((target / "new-upload").exists())

    def test_cli_defaults_to_current_candidate_before_newer_ready_preparation(self):
        site, _ = self.current_report()
        ready = self.root / ".private/browser-demo/preparation-20990101/READY.json"
        ready.parent.mkdir()
        ready.write_text(json.dumps({"site": ".private/browser-demo/missing-old-site"}))
        output = self.root / ".private/browser-demo/upload"
        with (
            patch.object(MODULE, "ROOT", self.root),
            patch.object(
                MODULE, "prepare", return_value={"files_sha256": {}, "archive_bytes": 0}
            ) as prepare,
            patch("sys.argv", ["cloudflare_bundle.py", "--output", str(output)]),
        ):
            MODULE.main()
        self.assertEqual(prepare.call_args.args, (site, output))


if __name__ == "__main__":
    unittest.main()
