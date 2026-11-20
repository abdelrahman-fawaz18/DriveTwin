"""Portable scenario installation verifies bytes before changing the catalog."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/launcher"), str(ROOT / "scripts/geographic")]
import live_inputs  # noqa: E402
import scenarios  # noqa: E402

import catalog  # noqa: E402


class ScenarioBundleTests(unittest.TestCase):
    def test_shipped_catalog_matches_manifest_fingerprint(self):
        self.assertEqual(scenarios.specification()["id"], "geographic-v1")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        spec = self.root / "simulation/geographic"
        spec.mkdir(parents=True)
        self.name = "trip/standard/prepared/geometry.json"
        self.bundle = self.root / ".private/operator/input-bundle"
        payload = self.bundle / self.name
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"prepared geometry fixture")
        self.sha = scenarios.digest(payload)
        profile = {
            "journey_id": "trip",
            "inputs": {".private/operator/scenarios/geographic-v1/" + self.name: self.sha},
        }
        trips = [{"id": "trip", "profile": profile}]
        (spec / "catalog.json").write_text(
            json.dumps(
                {
                    "version": "launch-trip-catalog/1.0.0",
                    "trips": trips,
                }
            )
        )
        manifest = {
            "version": scenarios.VERSION,
            "id": "geographic-v1",
            "destination": ".private/operator/scenarios/geographic-v1",
            "catalog_sha256": scenarios.digest(spec / "catalog.json"),
            "files": {self.name: {"sha256": self.sha, "bytes": payload.stat().st_size}},
        }
        (spec / "manifest.json").write_text(json.dumps(manifest))
        (spec / "README.md").write_text("Scenario attribution fixture")

    def test_install_export_and_repeat_preserve_exact_bytes(self):
        with patch.object(scenarios, "check") as check:
            result = scenarios.install(self.bundle, root=self.root)
            self.assertEqual(result["presets"], 1)
            self.assertFalse(result["services_started"])
            self.assertEqual(check.call_count, 1)
            scenarios.install(self.bundle, root=self.root)
        destination = self.root / ".private/operator/live-trips.json"
        result = scenarios.export_bundle(".private/operator/transfer", destination, root=self.root)
        self.assertTrue(result["verified"])
        with self.assertRaises(ValueError):
            scenarios.export_bundle(".private/operator/transfer", destination, root=self.root)

    def test_corruption_does_not_replace_installed_catalog(self):
        destination = self.root / ".private/operator/live-trips.json"
        destination.write_text("original catalog")
        (self.bundle / self.name).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            scenarios.install(self.bundle, root=self.root)
        self.assertEqual(destination.read_text(), "original catalog")

    def test_interrupted_copy_preserves_catalog_and_allows_a_clean_retry(self):
        name = "trip/standard/prepared/second.json"
        second = self.bundle / name
        second.write_bytes(b"second immutable input")
        path = self.root / "simulation/geographic/manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["files"][name] = {
            "sha256": scenarios.digest(second),
            "bytes": second.stat().st_size,
        }
        path.write_text(json.dumps(manifest))
        catalog_path = self.root / ".private/operator/live-trips.json"
        catalog_path.write_text("preserve existing catalog")
        copy = scenarios.link_or_copy

        def interrupt(source, destination):
            if source == second:
                raise OSError("injected copy interruption")
            copy(source, destination)

        with patch.object(scenarios, "link_or_copy", side_effect=interrupt):
            with self.assertRaisesRegex(ValueError, "partial inputs preserved"):
                scenarios.install(self.bundle, root=self.root)
        self.assertEqual(catalog_path.read_text(), "preserve existing catalog")
        destination = self.root / manifest["destination"]
        self.assertFalse(destination.exists())
        partials = list(destination.parent.glob("geographic-v1.partial-*"))
        self.assertEqual(len(partials), 1)
        self.assertEqual(scenarios.digest(partials[0] / self.name), self.sha)
        with patch.object(scenarios, "check"):
            result = scenarios.install(self.bundle, root=self.root)
        self.assertTrue(result["verified"])
        self.assertTrue(partials[0].is_dir())

    def test_manifest_traversal_and_outside_roots_are_rejected(self):
        path = self.root / "simulation/geographic/manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["files"]["../outside"] = {"sha256": self.sha, "bytes": 1}
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            scenarios.verify(self.bundle, root=self.root)
        with self.assertRaises(ValueError):
            scenarios.plain_file(self.root, "../outside")
        with self.assertRaises(ValueError):
            scenarios.inside(self.root, self.root.parent)

    @unittest.skipIf(sys.platform == "win32", "Linux covers redirects without symlink privileges")
    def test_redirected_installation_ancestor_is_rejected_before_writes(self):
        outside = self.root / "unrelated"
        outside.mkdir()
        (self.root / ".private/operator/scenarios").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            scenarios.install(self.bundle, root=self.root)
        self.assertFalse((outside / "geographic-v1").exists())

    def test_relocated_operator_inputs_keep_identity_and_changed_bytes_do_not(self):
        inputs = {"old/map.json": "a" * 64, "old/station.json": "b" * 64}
        encoded = json.dumps(
            {Path(name).name: sha for name, sha in inputs.items()},
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        trip = {
            "id": "reviewed-trip",
            "profile": {"inputs": inputs},
            "available_services": ["charging"],
        }
        with (
            patch.dict(catalog.OPERATOR_INPUTS, {"reviewed-trip": "historical-identity"}),
            patch.dict(catalog.OPERATOR_CONTENT_INPUTS, {"reviewed-trip": fingerprint}),
        ):
            self.assertTrue(catalog.operator_available(trip))
            trip["profile"]["inputs"] = {"new/" + Path(k).name: v for k, v in inputs.items()}
            self.assertTrue(catalog.operator_available(trip))
            trip["profile"]["inputs"]["new/map.json"] = "c" * 64
            self.assertFalse(catalog.operator_available(trip))

    def test_runtime_geometry_rebinds_paths_without_editing_prepared_bytes(self):
        prepared = self.root / ".private/operator/scenarios/fixture"
        prepared.mkdir(parents=True)
        geometry = {}
        inputs = {}
        for role, name in (
            ("network", "elevated-route.net.xml"),
            ("terrain", "heights.json"),
            ("osm", "roads.osm.xml"),
        ):
            source = prepared / name
            source.write_text("exact original " + role)
            sha = scenarios.digest(source)
            inputs[source.relative_to(self.root).as_posix()] = sha
            geometry[role] = {"path": "missing/old-evidence/" + name, "sha256": sha}
        source_manifest = prepared / "geometry.json"
        source_manifest.write_text(json.dumps(geometry))
        before = source_manifest.read_bytes()
        profile = {"prepared": prepared.relative_to(self.root).as_posix(), "inputs": inputs}
        runtime = self.root / ".runtime/run"
        runtime.mkdir(parents=True)
        with patch.object(live_inputs, "ROOT", self.root):
            result = live_inputs.prepare_geometry(profile, runtime)
        rebound = json.loads(result.read_bytes())
        self.assertEqual(source_manifest.read_bytes(), before)
        for role, item in rebound.items():
            self.assertTrue(item["path"].startswith(".runtime/run/forecast-inputs/"))
            self.assertEqual(scenarios.digest(self.root / item["path"]), geometry[role]["sha256"])


if __name__ == "__main__":
    unittest.main()
