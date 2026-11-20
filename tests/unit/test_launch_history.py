"""Saved recordings remain discoverable without exposing run credentials."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/launcher"))
from history import History  # noqa: E402


class HistoryTests(unittest.TestCase):
    def test_huge_saved_timestamp_is_skipped_instead_of_crashing_history(self):
        identity, _ = self.record()
        path = self.history.directory / (identity + ".json")
        record = json.loads(path.read_bytes())
        path.write_text(json.dumps({**record, "created": 10**400}))
        self.assertEqual(self.history.page()["total"], 0)

    def test_deeply_nested_saved_metadata_is_skipped(self):
        identity, _ = self.record()
        path = self.history.directory / (identity + ".json")
        path.write_text("[" * 1500 + "]" * 1500)
        self.assertEqual(self.history.page()["total"], 0)

    def test_speed_is_saved_and_old_sessions_default_to_one(self):
        self.record()
        entry = self.history.page()["items"][0]
        self.assertEqual(entry["time_scale"], 1)
        entry["time_scale"] = 20
        self.history.remember(entry)
        self.assertEqual(History(self.root).page()["items"][0]["time_scale"], 20)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=ROOT / ".runtime" if (ROOT / ".runtime").is_dir() else None
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opened = []
        self.history = History(self.root, opener=self.opened.append)

    def record(self, number=1, *, state="arrived", cleanup=True, backup=True):
        identity = f"20261113T184819Z-{number:06x}"
        folder = self.root / "evidence/energy-v2" / ("live-journey-" + identity)
        folder.mkdir(parents=True)
        profile = {
            "journey_id": f"trip-{number}",
            "title": f"Road trip {number}",
            "services": ["energy"],
        }
        (folder / "profile.json").write_text(json.dumps(profile))
        result = {
            "status": state,
            "worker_started": True,
            "session": {"cleanup_verified": cleanup, "backup_saved": backup},
            "monitoring_token": "private-example-must-not-appear",
        }
        (folder / "operator-result.json").write_text(json.dumps(result))
        (folder / "database.dump").write_bytes(b"backup fixture")
        self.history.remember(
            {
                "id": identity,
                "trip_id": profile["journey_id"],
                "title": profile["title"],
                "services": ["monitoring", "energy"],
                "created": 1000 + number,
                "token": "never-indexed",
            }
        )
        return identity, folder

    def test_saved_partial_and_failed_outcomes_survive_reopen(self):
        self.record(1)
        self.record(2, state="stopped_incomplete")
        self.record(3, state="failed_or_incomplete", backup=False)
        other = History(self.root, opener=self.opened.append)
        result = other.page()
        self.assertEqual(
            [item["state"] for item in result["items"]],
            ["failed_or_incomplete", "stopped_incomplete", "arrived"],
        )
        self.assertEqual(other.page(outcome="saved")["total"], 2)
        self.assertEqual(other.page(outcome="attention")["total"], 1)
        self.assertNotIn("private-example", json.dumps(result))
        self.assertNotIn(
            "never-indexed", "".join(p.read_text() for p in other.directory.glob("*.json"))
        )

    def test_unverified_or_missing_backup_never_becomes_saved_success(self):
        _, folder = self.record(cleanup=False)
        (folder / "database.dump").unlink()
        item = self.history.page()["items"][0]
        self.assertEqual(item["state"], "cleanup_unverified")
        self.assertFalse(item["recording_saved"])
        self.assertEqual(self.history.page(outcome="attention")["total"], 1)

    def test_arrival_with_missing_backup_needs_review(self):
        _, folder = self.record()
        (folder / "database.dump").unlink()
        item = self.history.page()["items"][0]
        self.assertEqual(item["state"], "failed_or_incomplete")
        self.assertFalse(item["recording_saved"])

    def test_malformed_saved_result_cannot_claim_cleanup_or_recording(self):
        _, folder = self.record()
        for result in (
            {"status": "arrived", "session": None},
            {"status": "arrived", "session": {"cleanup_verified": "true"}},
            {"status": "arrived", "runtime_host_guard": ["invalid"]},
        ):
            with self.subTest(result=result):
                (folder / "operator-result.json").write_text(json.dumps(result))
                item = self.history.page()["items"][0]
                self.assertEqual(item["state"], "cleanup_unverified")
                self.assertFalse(item["cleanup_verified"])
                self.assertFalse(item["recording_saved"])

    def test_discovery_preserves_per_run_starting_charge(self):
        identity, folder = self.record()
        profile_file = folder / "profile.json"
        profile = json.loads(profile_file.read_bytes())
        profile.update(
            starting_charge_percent=12.3,
            initial_charge_percent=12.3,
            services=["energy", "charging"],
            charging_startup_delay_working_s=60,
        )
        profile_file.write_text(json.dumps(profile))
        (self.history.directory / (identity + ".json")).unlink()
        entry = self.history.page(refresh=True)["items"][0]
        self.assertEqual(entry["initial_charge_percent"], 12.3)
        self.assertEqual(entry["starting_charge_percent"], 12.3)
        self.assertEqual(entry["charging_startup_delay_working_s"], 60)

    def test_imports_earlier_runs_and_skips_malformed_data(self):
        identity, folder = self.record()
        (self.history.directory / (identity + ".json")).unlink()
        bad = folder.parent / "live-journey-20269999T999999Z-abcdef"
        bad.mkdir()
        (bad / "profile.json").write_text("[]")
        (self.history.directory / "malformed.json").write_text("[]")
        result = self.history.page(refresh=True)
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["items"][0]["recording_saved"])

    def test_pagination_and_filters_preserve_all_runs(self):
        for number in range(1, 24):
            self.record(number)
        first = self.history.page()
        second = self.history.page(offset=20)
        self.assertEqual(first["total"], 23)
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(second["items"]), 3)
        self.assertFalse({v["id"] for v in first["items"]} & {v["id"] for v in second["items"]})
        self.assertEqual(self.history.page(search="TRIP 23")["total"], 1)
        for options in (
            {"offset": -1},
            {"offset": True},
            {"outcome": "arbitrary"},
            {"search": "x" * 101},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.history.page(**options)

    def test_unfiltered_page_only_reads_visible_outcomes_and_keeps_them_fresh(self):
        for number in range(1, 24):
            self.record(number)
        with patch.object(self.history, "summary", wraps=self.history.summary) as summaries:
            self.history.page()
            self.assertEqual(summaries.call_count, 20)
        _, folder = self.record(24)
        result = json.loads((folder / "operator-result.json").read_bytes())
        result["session"]["cleanup_verified"] = False
        (folder / "operator-result.json").write_text(json.dumps(result))
        self.assertEqual(self.history.page()["items"][0]["state"], "cleanup_unverified")

    def test_open_accepts_only_an_indexed_owned_folder(self):
        identity, folder = self.record()
        self.assertTrue(self.history.open_folder(identity)["opened"])
        self.assertEqual(self.opened, [str(folder.resolve())])
        for value in ("../../other", None, [identity], "20261113T184819Z-ffffff"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.history.open_folder(value)
        self.assertEqual(len(self.opened), 1)

    def test_active_startup_without_results_is_not_reported_as_missing(self):
        identity = "20261113T184819Z-000001"
        self.history.remember(
            {
                "id": identity,
                "trip_id": "trip",
                "title": "Road trip",
                "services": ["monitoring"],
                "created": 1234,
            }
        )
        page = self.history.page(current={"id": identity, "active": True, "state": "checking"})
        self.assertTrue(page["items"][0]["active"])
        self.assertEqual(page["items"][0]["state"], "checking")
        self.assertFalse(page["items"][0]["folder_available"])

    @unittest.skipIf(
        sys.platform == "win32",
        "Linux check covers redirect rejection without Windows symlink privileges",
    )
    def test_redirected_result_folder_is_rejected(self):
        identity, folder = self.record()
        moved = self.root / "unrelated-folder"
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.history.open_folder(identity)
        self.assertEqual(self.opened, [])
        self.assertEqual(self.history.page()["items"][0]["state"], "results_unavailable")


if __name__ == "__main__":
    unittest.main()
