"""Launch authority, process ownership, and visible lifecycle without a bus."""

import http.client
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/launcher"))
import recover_run  # noqa: E402
from recover_run import BACKUP, project_name, save_receipt  # noqa: E402
from server import API_VERSION, Server  # noqa: E402
from session import Sessions, write  # noqa: E402

import catalog  # noqa: E402


class LauncherVersionTests(unittest.TestCase):
    def test_page_and_service_expect_the_same_api_version(self):
        script = (ROOT / "apps/launcher/app.js").read_text(encoding="utf-8")
        self.assertRegex(script, rf"(?m)^const launcherApiVersion = {API_VERSION};$")


class Process:
    pid = 900001
    returncode = None

    def poll(self):
        return self.returncode


class RecoveryTests(unittest.TestCase):
    def test_recovery_keeps_the_volumes_and_checks_for_remaining_services(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve() / "live-journey-20261115T085613Z-abcdef"
            folder.mkdir()
            project = project_name(folder)
            volume = project + "_database"
            archive = folder / BACKUP
            answers = iter([["db-id"], [volume], ["net-id"], [], []])

            def docker(*args):
                if args[:2] == ("container", "inspect"):
                    return json.dumps(
                        [project, "db", "exited", [{"Type": "volume", "Name": volume}]]
                    ).encode()
                if args == ("volume", "ls", "-q"):
                    return volume.encode()
                return b""

            def dump(container, destination):
                self.assertEqual(container, "db-id")
                self.assertEqual(destination, folder)
                archive.write_bytes(b"PGDMP")
                return True

            with (
                patch.object(recover_run, "resources", side_effect=lambda *_: next(answers)),
                patch.object(recover_run, "docker", side_effect=docker) as calls,
                patch.object(recover_run, "dump_database", side_effect=dump),
            ):
                recover_run.release_trip(folder)
            mutations = [call.args for call in calls.call_args_list if call.args[1] == "rm"]
            self.assertEqual(mutations, [("container", "rm", "db-id"), ("network", "rm", "net-id")])
            receipt = recover_run.read_receipt(folder)
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(receipt["backup_saved"])
            self.assertEqual(receipt["retained_volumes"], [volume])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=ROOT / ".runtime" if (ROOT / ".runtime").is_dir() else None
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "evidence/energy-v2").mkdir(parents=True)
        self.calls = []
        self.process = Process()

        def spawn(args, **kwargs):
            self.calls.append((args, kwargs))
            return self.process

        self.opened = []
        self.copied = []
        self.manager = Sessions(
            [],
            root=self.root,
            spawn=spawn,
            open_browser=lambda url: self.opened.append(url) is None,
            copy_key=lambda key: self.copied.append(key) is None,
        )
        self.profile = {"title": "A real-road trip", "services": []}
        self.selection = patch("session.select", return_value=self.profile)
        self.selection.start()
        self.addCleanup(self.selection.stop)

    def start(self):
        return self.manager.start("trip-one", ["replay"])

    def test_invalid_saved_bookmark_is_visible_and_cannot_start_another_owner(self):
        pointer = self.manager.directory / "current.json"
        for payload in (
            b"null",
            b"{}",
            b"[]",
            b"invalid json",
            b"\xff",
            b" " * 8193,
            b"[" * 1500 + b"]" * 1500,
        ):
            with self.subTest(payload=payload[:20]):
                pointer.write_bytes(payload)
                reopened = Sessions([], root=self.root, spawn=self.manager.spawn)
                status = reopened.status()
                self.assertIsNone(status["run"])
                self.assertIn("cleanup", status["session_metadata_error"])
                with self.assertRaisesRegex(ValueError, "saved status"):
                    reopened.start("trip-one", ["replay"])
                self.assertEqual(self.calls, [])
                self.assertEqual(pointer.read_bytes(), payload)

    def test_existing_bookmark_read_failure_cannot_be_treated_as_idle(self):
        pointer = self.manager.directory / "current.json"
        pointer.write_text("{}")
        for failure in (FileNotFoundError(), PermissionError()):
            with self.subTest(failure=failure):
                with patch.object(Path, "open", side_effect=failure):
                    reopened = Sessions([], root=self.root, spawn=self.manager.spawn)
                self.assertIsNotNone(reopened.status()["session_metadata_error"])
                with self.assertRaises(ValueError):
                    reopened.start("trip-one", ["replay"])
        self.assertEqual(self.calls, [])

    def test_incomplete_or_misbound_saved_bookmark_never_claims_a_process(self):
        self.start()
        original = dict(self.manager.current)
        path = self.manager.directory / "current.json"
        invalid = [
            {k: v for k, v in original.items() if k != "pid"},
            {**original, "pid": True},
            {**original, "pid": -1},
            {**original, "pid": 2**40},
            {**original, "stop_requested": "false"},
            {**original, "output": "evidence/energy-v2/live-journey-other"},
            {**original, "log": "../other.log"},
            {**original, "created": 10**400},
        ]
        for value in invalid:
            with self.subTest(value=value):
                write(path, value)
                reopened = Sessions([], root=self.root, spawn=self.manager.spawn)
                with patch("session.alive") as probe:
                    self.assertIsNotNone(reopened.status()["session_metadata_error"])
                    with self.assertRaises(ValueError):
                        reopened.start("trip-one", ["replay"])
                probe.assert_not_called()
                self.assertEqual(len(self.calls), 1)

    def test_saved_bookmark_repairs_take_effect_after_reopening(self):
        path = self.manager.directory / "current.json"
        path.write_text("null")
        blocked = Sessions([], root=self.root, spawn=self.manager.spawn)
        self.assertIsNotNone(blocked.status()["session_metadata_error"])
        # Removing a fixture stands for an independently verified repair; the
        # application itself never deletes an invalid ownership bookmark.
        path.unlink()
        reopened = Sessions([], root=self.root, spawn=self.manager.spawn)
        self.assertIsNone(reopened.status()["session_metadata_error"])
        self.assertIsNone(reopened.status()["run"])

    def test_reopened_terminal_result_overrides_reused_pid_but_not_live_owned_process(self):
        self.start()
        folder = self.manager.output()
        folder.mkdir()
        write(
            folder / "operator-result.json",
            {"status": "arrived", "session": {"cleanup_verified": True, "backup_saved": True}},
        )
        (folder / "database.dump").write_bytes(b"PGDMPsaved fixture")
        self.assertTrue(self.manager.active())  # The known Popen owner still has priority.
        reopened = Sessions([], root=self.root, spawn=self.manager.spawn)
        with patch("session.alive", return_value=True):
            old = reopened.status()["run"]
            self.assertFalse(old["active"])
            self.assertEqual(old["state"], "arrived")
            fresh = reopened.start("trip-one", ["replay"])["run"]
        self.assertNotEqual(old["id"], fresh["id"])
        self.assertEqual(len(self.calls), 2)

    def test_reused_pid_without_verified_cleanup_still_blocks_start(self):
        self.start()
        folder = self.manager.output()
        folder.mkdir()
        write(
            folder / "operator-result.json",
            {"status": "failed_or_incomplete", "session": {"cleanup_verified": False}},
        )
        reopened = Sessions([], root=self.root, spawn=self.manager.spawn)
        with patch("session.alive", return_value=True):
            with self.assertRaisesRegex(ValueError, "already active"):
                reopened.start("trip-one", ["replay"])
        self.assertEqual(len(self.calls), 1)

    def test_malformed_saved_result_needs_review_and_keeps_recovery_available(self):
        self.start()
        self.process.returncode = 1
        folder = self.manager.output()
        folder.mkdir()
        for result in (
            {"status": "failed_or_incomplete", "session": None},
            {"status": "arrived", "session": []},
            {"status": "arrived", "session": {"cleanup_verified": "true"}},
            {"status": "failed_or_incomplete", "runtime_host_guard": ["broken"]},
            {"status": "failed_or_incomplete", "runtime_host_guard": {"failure": True}},
        ):
            with self.subTest(result=result):
                write(folder / "operator-result.json", result)
                run = self.manager.status()["run"]
                self.assertEqual(run["state"], "cleanup_unverified")
                self.assertFalse(run["cleanup_verified"])
                self.assertFalse(run["recording_saved"])
                with self.assertRaisesRegex(ValueError, "cleanup review"):
                    self.start()
        self.manager.recover(self.manager.current["id"])
        self.assertIn("scripts/launcher/recover_run.py", self.calls[-1][0])

    def test_malformed_stage_and_ready_records_cannot_expose_credentials(self):
        self.start()
        folder = self.manager.output()
        folder.mkdir()
        write(folder / "stage.json", [])
        write(folder / "ready.json", None)
        run = self.manager.status()["run"]
        self.assertEqual(run["state"], "starting")
        self.assertEqual(run["access"], {})

    def test_invalid_result_encoding_becomes_cleanup_review(self):
        self.start()
        self.process.returncode = 1
        folder = self.manager.output()
        folder.mkdir()
        (folder / "operator-result.json").write_bytes(b"\xff")
        self.assertEqual(self.manager.status()["run"]["state"], "cleanup_unverified")
        with self.assertRaisesRegex(ValueError, "cleanup review"):
            self.start()

    def test_fixed_command_and_concurrent_start_refused(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "already active"):
            self.start()
        self.assertEqual(len(self.calls), 1)
        args, kwargs = self.calls[0]
        self.assertEqual(args[1], "scripts/geographic/live.py")
        self.assertIn("--no-browser", args)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["cwd"], self.root)

    def test_early_stop_waits_for_owned_output_directory(self):
        self.start()
        self.manager.stop()
        self.assertFalse(self.manager.output().exists())
        self.manager.output().mkdir()
        self.manager.tick()
        self.assertTrue((self.manager.output() / "stop").is_file())
        self.assertEqual(self.manager.status()["run"]["state"], "stopping")

    def test_ready_credentials_disappear_after_clean_stop(self):
        self.start()
        output = self.manager.output()
        output.mkdir()
        write(output / "stage.json", {"stage": "running", "message": "Ready"})
        write(
            output / "ready.json",
            {
                "monitoring_url": "http://127.0.0.1:18765",
                "monitoring_token": "test-reader",
                "operator_token": "must-not-be-exposed",
                "charging_operator_token": "separate-authority",
            },
        )
        access = self.manager.status()["run"]["access"]
        self.assertEqual(access["monitoring_token"], "test-reader")
        self.assertNotIn("operator_token", access)
        self.assertNotIn("charging_operator_token", access)
        write(
            output / "operator-result.json",
            {"status": "stopped_incomplete", "session": {"cleanup_verified": True}},
        )
        self.process.returncode = 1
        status = self.manager.status()["run"]
        self.assertFalse(status["active"])
        self.assertEqual(status["access"], {})
        self.assertTrue(status["cleanup_verified"])
        self.assertIsNone(status["error"])

    def test_charging_key_requires_the_active_opted_in_run_and_is_never_polled(self):
        self.profile["moving_charge_mode"] = catalog.OPERATOR_MODE
        identity = self.ready_dashboard()
        output = self.manager.output()
        write(output / "stage.json", {"stage": "starting", "message": "Preparing"})
        write(
            output / "ready.json",
            {
                "run_id": "source-run",
                "monitoring_url": "http://127.0.0.1:18765",
                "monitoring_token": "reader-key",
                "charging_operator_token": "operator-key",
            },
        )
        with self.assertRaisesRegex(ValueError, "active drive"):
            self.manager.charging_operator_key(identity)
        with self.assertRaisesRegex(ValueError, "key with charging approval"):
            self.manager.open_dashboard(identity)
        self.assertEqual(self.manager.status()["run"]["access"], {})
        self.assertEqual(self.copied, [])
        self.assertEqual(self.opened, [])
        (output / "startup-check.json").touch()
        with self.assertRaisesRegex(ValueError, "key with charging approval"):
            self.manager.open_dashboard(identity)
        write(output / "stage.json", {"stage": "running", "message": "Ready"})
        snapshot = self.manager.status()
        self.assertTrue(snapshot["run"]["charging_operator_ready"])
        self.assertNotIn("operator-key", json.dumps(snapshot))
        self.assertNotIn("reader-key", json.dumps(snapshot))
        self.assertIn("monitoring_url", snapshot["run"]["access"])
        index = next(self.manager.history.directory.glob("*.json"))
        self.assertNotIn("operator-key", index.read_text(encoding="utf-8"))
        self.assertEqual(
            self.manager.charging_operator_key(identity),
            {"charging_operator_token": "operator-key"},
        )
        self.assertTrue(self.manager.open_dashboard(identity)["key_copied"])
        self.assertEqual(self.copied, ["operator-key"])
        with self.assertRaises(ValueError):
            self.manager.charging_operator_key("different-run")
        (output / "driving-ended").touch()
        with self.assertRaises(ValueError):
            self.manager.charging_operator_key(identity)
        write(output / "stage.json", {"stage": "history", "message": "History"})
        self.assertEqual(self.manager.status()["run"]["access"], {})

    def test_missing_cleanup_blocks_next_run(self):
        self.start()
        self.manager.output().mkdir()
        self.process.returncode = 1
        self.assertEqual(self.manager.status()["run"]["state"], "cleanup_unverified")
        with self.assertRaisesRegex(ValueError, "cleanup review"):
            self.start()
        self.assertEqual(len(self.calls), 1)

    def test_interrupted_trip_recovery_releases_next_start_and_preserves_outcome(self):
        first = self.start()["run"]["id"]
        folder = self.manager.output()
        folder.mkdir()
        original = {"status": "failed_or_incomplete", "worker_started": True}
        write(folder / "operator-result.json", original)
        with self.assertRaisesRegex(ValueError, "interrupted trip"):
            self.manager.recover(first)
        self.process.returncode = 1
        recovery = Process()
        next_run = Process()
        with patch.object(self.manager, "spawn", side_effect=[recovery, next_run]) as spawn:
            with self.assertRaisesRegex(ValueError, "interrupted trip"):
                self.manager.recover("a-different-trip")
            self.assertEqual(self.manager.recover(first)["run"]["state"], "recovering_cleanup")
            with self.assertRaisesRegex(ValueError, "already active"):
                self.start()
            (folder / BACKUP).write_bytes(b"PGDMP")
            save_receipt(
                folder,
                {
                    "version": 1,
                    "trip": folder.name,
                    "project": project_name(folder),
                    "cleanup_verified": True,
                    "backup_saved": True,
                },
            )
            recovery.returncode = 0
            recovered = self.manager.status()["run"]
            self.assertEqual(recovered["state"], "failed_or_incomplete")
            self.assertTrue(recovered["cleanup_verified"])
            self.assertTrue(recovered["recording_saved"])
            self.assertEqual(json.loads((folder / "operator-result.json").read_bytes()), original)
            self.assertTrue(self.manager.history.page()["items"][0]["recording_saved"])
            self.assertNotEqual(self.start()["run"]["id"], first)
            self.assertEqual(
                spawn.call_args_list[0].args[0][1:],
                ["scripts/launcher/recover_run.py", first],
            )

    def test_reopen_recovers_session_without_a_second_process(self):
        self.start()
        self.manager.output().mkdir()
        with patch("session.alive", return_value=True):
            other = Sessions(
                [], root=self.root, spawn=lambda *_a, **_k: self.fail("No second process")
            )
            self.assertTrue(other.status()["run"]["active"])
            other.stop()
            self.assertTrue((other.output() / "stop").exists())

    def test_blocked_host_is_visible_and_has_no_dashboard(self):
        self.start()
        output = self.manager.output()
        output.mkdir()
        write(
            output / "operator-result.json",
            {
                "status": "blocked_by_host_preflight",
                "error": "Available memory is below the reserve",
            },
        )
        self.process.returncode = 1
        status = self.manager.status()["run"]
        self.assertEqual(status["state"], "blocked_by_host_preflight")
        self.assertEqual(status["access"], {})

    def test_pause_requires_current_revision_and_source_confirmation(self):
        identity = self.ready_dashboard()
        output = self.manager.output()
        requests = output / "control/requests"
        status = output / "control/status"
        requests.mkdir(parents=True)
        status.mkdir()
        with self.assertRaises(ValueError):
            self.manager.set_paused(identity, 0, True)
        (output / "startup-check.json").touch()
        with self.assertRaises(ValueError):
            self.manager.set_paused("other-run", 0, True)
        pause = self.manager.set_paused(identity, 0, True)["run"]["pause"]
        self.assertTrue(pause["pending"])
        self.assertFalse(pause["paused"])
        with self.assertRaises(ValueError):
            self.manager.set_paused(identity, 0, False)
        write(status / "pause.json", {"revision": 1, "paused": True, "sim_time_s": 42})
        state = self.manager.status()["run"]["pause"]
        self.assertTrue(state["paused"])
        self.assertFalse(state["pending"])
        self.assertTrue(self.manager.set_paused(identity, 1, False)["run"]["pause"]["pending"])
        with self.assertRaisesRegex(ValueError, "different trip"):
            self.manager.stop("earlier-run")
        self.assertFalse((output / "stop").exists())
        self.manager.stop()
        with self.assertRaises(ValueError):
            self.manager.set_paused(identity, 2, True)
        self.assertTrue((output / "stop").exists())

    def test_host_guard_failure_is_visible_after_stop_and_save(self):
        self.start()
        self.manager.output().mkdir()
        write(
            self.manager.output() / "operator-result.json",
            {
                "status": "stopped_incomplete",
                "session": {"cleanup_verified": True, "backup_saved": True},
                "runtime_host_guard": {"failure": {"reason": "Windows memory reserve breached"}},
            },
        )
        (self.manager.output() / "database.dump").write_bytes(b"fixture")
        self.process.returncode = 1
        run = self.manager.status()["run"]
        self.assertEqual(run["state"], "failed_or_incomplete")
        self.assertEqual(run["error"], "Windows memory reserve breached")
        self.assertTrue(run["recording_saved"])

    def test_arrival_reports_backup_and_session_problem_separately(self):
        self.start()
        folder = self.manager.output()
        folder.mkdir()
        result = {
            "status": "arrived",
            "session": {"status": "arrived", "cleanup_verified": True, "backup_saved": True},
        }
        write(folder / "operator-result.json", result)
        self.process.returncode = 0
        self.assertEqual(self.manager.status()["run"]["state"], "failed_or_incomplete")
        (folder / "database.dump").write_bytes(b"fixture")
        run = self.manager.status()["run"]
        self.assertEqual(run["state"], "arrived")
        self.assertTrue(run["recording_saved"])
        self.assertEqual(run["message"], "Trip complete. Recording saved and services stopped.")
        result["runtime_host_guard"] = {"failure": {"reason": "Resource clock stepped"}}
        write(folder / "operator-result.json", result)
        run = self.manager.status()["run"]
        self.assertEqual(run["state"], "failed_or_incomplete")
        self.assertEqual(run["error"], "Resource clock stepped")
        self.assertEqual(
            run["message"], "Trip complete. Recording saved. Review the session warning."
        )

    def test_dashboard_capability_is_bound_to_active_run_and_only_exposes_pause(self):
        identity = self.ready_dashboard()
        folder = self.manager.output()
        ready = json.loads((folder / "ready.json").read_bytes())
        write(folder / "ready.json", {**ready, "run_id": "monitoring-run"})
        (folder / "control/requests").mkdir(parents=True)
        (folder / "startup-check.json").touch()
        key = self.manager.current["trip_control_key"]
        self.assertIsNone(self.manager.dashboard_control("test-reader"))
        state = self.manager.dashboard_control(key, {"revision": 0, "paused": True})
        self.assertEqual(set(state), {"run_id", "asset_id", "pause"})
        self.assertEqual(state["run_id"], "monitoring-run")
        self.assertTrue(state["pause"]["pending"])
        self.manager.open_dashboard(identity)
        self.assertEqual(self.opened, ["http://127.0.0.1:18765/#trip-control=" + key])
        self.process.returncode = 0
        self.assertIsNone(self.manager.dashboard_control(key))

    def ready_dashboard(self):
        run = self.start()["run"]
        self.manager.output().mkdir()
        write(self.manager.output() / "stage.json", {"stage": "running", "message": "Ready"})
        write(
            self.manager.output() / "ready.json",
            {
                "monitoring_url": "http://127.0.0.1:18765",
                "monitoring_token": "test-reader",
            },
        )
        return run["id"]

    def test_dashboard_replay_does_not_expose_a_key_in_status(self):
        self.ready_dashboard()
        folder = self.manager.output()
        ready = json.loads((folder / "ready.json").read_bytes())
        write(
            folder / "ready.json",
            {
                **ready,
                "replay_url": "http://127.0.0.1:18766",
                "replay_token": "test-replay-reader",
            },
        )
        self.assertNotIn("replay_token", self.manager.status()["run"]["access"])
        self.assertIsNone(self.manager.dashboard_replay("test-replay-reader"))
        self.assertIsNone(self.manager.dashboard_replay(self.manager.current["trip_control_key"]))
        self.assertEqual(
            self.manager.dashboard_replay("test-reader"),
            {
                "url": "http://127.0.0.1:18766",
                "access": "test-replay-reader",
            },
        )
        self.process.returncode = 0
        self.assertIsNone(self.manager.dashboard_replay("test-reader"))

    def test_open_dashboard_copies_key_and_opens_fixed_url_once(self):
        identity = self.ready_dashboard()
        self.assertTrue(self.manager.open_dashboard(identity)["key_copied"])
        self.assertTrue(self.manager.open_dashboard(identity)["already_opened"])
        self.assertEqual(self.opened, ["http://127.0.0.1:18765"])
        self.assertEqual(self.copied, ["test-reader"])
        saved = (self.manager.directory / "current.json").read_text()
        self.assertNotIn("test-reader", saved)
        with patch("session.alive", return_value=True):
            reopened = Sessions(
                [], root=self.root, open_browser=lambda _: self.fail("Duplicate open")
            )
            self.assertTrue(reopened.open_dashboard(identity)["already_opened"])

    def test_dashboard_open_requires_current_active_ready_run_and_fixed_url(self):
        identity = self.ready_dashboard()
        with self.assertRaises(ValueError):
            self.manager.open_dashboard("different-run")
        for url in ("https://example.com", "http://127.0.0.1:18765/#private", "file:///anything"):
            with self.subTest(url=url):
                write(
                    self.manager.output() / "ready.json",
                    {"monitoring_url": url, "monitoring_token": "test-reader"},
                )
                with self.assertRaises(ValueError):
                    self.manager.open_dashboard(identity)
        write(
            self.manager.output() / "ready.json",
            {"monitoring_url": "http://127.0.0.1:18765", "monitoring_token": "test-reader"},
        )
        self.manager.stop()
        with self.assertRaises(ValueError):
            self.manager.open_dashboard(identity)
        self.process.returncode = 0
        with self.assertRaises(ValueError):
            self.manager.open_dashboard(identity)
        self.assertEqual(self.opened, [])
        self.assertEqual(self.copied, [])

    def test_dashboard_clipboard_failure_retains_browser_and_visible_key(self):
        identity = self.ready_dashboard()
        self.manager.copy_key = lambda _: False
        result = self.manager.open_dashboard(identity)
        self.assertTrue(result["opened"])
        self.assertFalse(result["key_copied"])
        self.assertEqual(self.manager.status()["run"]["access"]["monitoring_token"], "test-reader")

    def test_failed_browser_open_is_reported_and_not_marked_open(self):
        identity = self.ready_dashboard()
        self.manager.open_browser = lambda _: False
        with self.assertRaisesRegex(ValueError, "browser could not open"):
            self.manager.open_dashboard(identity)
        self.assertFalse(self.manager.current.get("dashboard_opened", False))


class CatalogTests(unittest.TestCase):
    def import_fixture(self, root):
        study = root / "evidence/energy-v2/qualified"
        reference = study / "journey-01"
        reference.mkdir(parents=True)
        write(study / "study-result.json", {"passed": True, "cleanup_verified": True})
        write(study / "qualification.json", {"case": "map-journey", "passed": True})
        write(study / "journey.json", [{"folder": "prepared", "inputs": {}}])
        write(
            reference / "study-result.json",
            {"trips": [{"duration_working_s": 480, "distance_m": 4000}]},
        )
        for name in ("reference-network.net.xml", "routes.xml"):
            (reference / name).write_text("<root/>", encoding="utf-8")
        write(
            reference / "run-input.json",
            {
                "recipe": {
                    "id": "other-city-trip",
                    "title": "Test trip",
                    "initial_soc": 0.8,
                    "mass_kg": 19000,
                    "auxiliary_w": 9000,
                },
                "stops": {"stops": []},
            },
        )
        return study

    def test_import_uses_the_geographic_area_without_requiring_it_in_the_trip_id(self):
        cases = (
            ("burnaby-unseen", "vancouver", "Metro Vancouver"),
            ("other-city-trip", "other-city", "other-city"),
            ("ottawa-older", None, "Ottawa"),
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            study = self.import_fixture(root)
            path = study / "journey-01/run-input.json"
            with patch.object(catalog, "ROOT", root), patch.object(catalog, "check"):
                for trip_id, extract, region in cases:
                    with self.subTest(trip=trip_id):
                        data = catalog.read(path)
                        data["recipe"].update(id=trip_id, extract=extract)
                        write(path, data)
                        (result,) = catalog.import_evaluation(study, root / "catalog.json")
                        self.assertEqual(result["region"], region)

    def test_import_requires_passing_standard_qualification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            study = self.import_fixture(root)
            destination = root / "catalog.json"
            destination.write_text("Existing catalog", encoding="utf-8")
            for qualification in (
                {"case": "map-journey-source-distance", "passed": True},
                {"case": "map-journey", "passed": False},
                None,
            ):
                with self.subTest(qualification=qualification), patch.object(catalog, "ROOT", root):
                    path = study / "qualification.json"
                    if qualification is None:
                        path.unlink()
                    else:
                        write(path, qualification)
                    with self.assertRaisesRegex(ValueError, "passing standard map-journey"):
                        catalog.import_evaluation(study, destination)
                    self.assertEqual(destination.read_text(encoding="utf-8"), "Existing catalog")

    def test_import_rejects_duplicate_trip_ids_in_one_study(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            study = self.import_fixture(root)
            destination = root / "catalog.json"
            destination.write_text("Existing catalog", encoding="utf-8")
            write(study / "journey.json", [{"folder": "prepared", "inputs": {}}] * 2)
            shutil.copytree(study / "journey-01", study / "journey-02")
            with patch.object(catalog, "ROOT", root), patch.object(catalog, "check"):
                with self.assertRaisesRegex(ValueError, "trip ID already exists"):
                    catalog.import_evaluation(study, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "Existing catalog")

    def test_charging_operator_requires_explicit_opt_in_and_bound_inputs(self):
        path = "reviewed-stations.json"
        trip = {
            "id": "prepared",
            "available_services": ["energy", "charging"],
            "profile": {"charging_assignment": path, "inputs": {path: "recorded"}},
        }
        key = catalog.hashlib.sha256(b'{"reviewed-stations.json":"recorded"}').hexdigest()
        with patch.dict(catalog.OPERATOR_INPUTS, {"prepared": key}), patch("catalog.check"):
            services = ["energy", "charging"]
            self.assertTrue(catalog.public([trip])[0]["charging_operator_available"])
            self.assertNotIn("moving_charge_mode", catalog.select([trip], "prepared", services))
            enabled = catalog.select([trip], "prepared", services, charging_operator=True)
            self.assertEqual(enabled["moving_charge_mode"], catalog.OPERATOR_MODE)
            for selected_services, speed, mode in (
                (["energy"], 1, True),
                (services, 2, True),
                (services, 1, "true"),
            ):
                with self.assertRaises(ValueError):
                    catalog.select(
                        [trip], "prepared", selected_services, speed, charging_operator=mode
                    )
            trip["profile"]["inputs"][path] = "updated"
            self.assertFalse(catalog.public([trip])[0]["charging_operator_available"])
            with self.assertRaisesRegex(ValueError, "verified 1×"):
                catalog.select([trip], "prepared", services, charging_operator=True)

    def test_detour_variant_requires_a_matching_seal_for_operator_mode(self):
        trip = {
            "id": "checked-trip",
            "available_services": ["energy", "charging"],
            "profile": {"charging_assignment": "original", "inputs": {"original": "one"}},
            "charging_variant": {"charging_assignment": "detour", "inputs": {"detour": "two"}},
            "charging_layout": "mixed",
        }
        ordinary = catalog.hashlib.sha256(b'{"original":"one"}').hexdigest()
        detour = catalog.hashlib.sha256(b'{"detour":"two"}').hexdigest()
        services = ["energy", "charging"]
        with (
            patch.dict(catalog.OPERATOR_INPUTS, {"checked-trip": ordinary}),
            patch("catalog.check"),
        ):
            self.assertEqual(
                catalog.select([trip], "checked-trip", services)["charging_assignment"], "detour"
            )
            self.assertNotIn("charging_variant", catalog.public([trip])[0])
            self.assertEqual(
                catalog.select([trip], "checked-trip", services, charging_operator=True)[
                    "charging_assignment"
                ],
                "original",
            )
            with patch.dict(catalog.DETOUR_OPERATOR_INPUTS, {"checked-trip": detour}):
                self.assertEqual(
                    catalog.select([trip], "checked-trip", services, charging_operator=True)[
                        "charging_assignment"
                    ],
                    "detour",
                )

    def test_imported_charging_assignment_checks_bus_access_and_itinerary_before_saving(self):
        path = "station-profile.json"
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        (root / path).write_text("{}", encoding="utf-8")
        (root / "reference-network.net.xml").write_bytes(b"network")
        (root / "elevated-route.net.xml").write_bytes(b"network")
        (root / "geometry.json").write_text('{"network":{"sha256":"verified"}}', encoding="utf-8")
        trip = {"id": "new-trip", "profile": {"inputs": {}}, "available_services": ["energy"]}
        data = {
            "mission": {"route_edges": ("west", "east")},
            "stops": {"stops": [{"external_index": 0, "offset_m": 12}]},
        }
        lane = SimpleNamespace(edge_id="west", length_m=60, internal=False)
        network = SimpleNamespace(sha256="verified", lanes={"west_0": lane})
        runtime_lane = SimpleNamespace(edge_id="west", length_m=60, internal=False)
        runtime_network = SimpleNamespace(lanes={"west_0": runtime_lane})
        bay = SimpleNamespace(
            bay_id="local-bay",
            lane_id="west_0",
            start_offset_m=16,
            end_offset_m=30,
            access_problem=lambda *_a, **_k: None,
        )
        entry = SimpleNamespace(bay=bay, external_index=0, detour=None)
        assignment = SimpleNamespace(
            bays=(entry,),
            baseline_edges=("west", "east"),
            policy=SimpleNamespace(connector="simulated-dc", vehicle_length_m=12),
        )
        with (
            patch.object(catalog, "load", return_value=[trip]),
            patch.object(catalog, "ROOT", root),
            patch.object(catalog, "local_path", return_value=root / path),
            patch.object(catalog, "check", return_value=(root, root, data)),
            patch.object(catalog, "charging_assignment", return_value=assignment),
            patch.object(catalog, "save_catalog") as save,
            patch(
                "evdt.energy.route_aware.sumo_routes.read_network",
                side_effect=[network, runtime_network] * 5,
            ),
        ):
            trip_stops = data["stops"]["stops"]
            trip_stops[0]["offset_m"] = 20
            with self.assertRaisesRegex(ValueError, "conflicts with the prepared itinerary"):
                catalog.attach_charging("new-trip", path)
            trip_stops[0]["offset_m"] = 12
            lane.edge_id = "east"
            runtime_lane.edge_id = "east"
            with self.assertRaisesRegex(ValueError, "conflicts with the prepared itinerary"):
                catalog.attach_charging("new-trip", path)
            lane.edge_id = "west"
            runtime_lane.edge_id = "west"
            runtime_lane.length_m = 25
            with self.assertRaisesRegex(ValueError, "must fit the forecast and runtime lane"):
                catalog.attach_charging("new-trip", path)
            runtime_lane.length_m = 60
            bay.access_problem = lambda *_a, **_k: "connector_incompatible"
            with self.assertRaisesRegex(ValueError, "incompatible"):
                catalog.attach_charging("new-trip", path)
            save.assert_not_called()
            self.assertEqual(trip["available_services"], ["energy"])
            bay.access_problem = lambda *_a, **_k: None
            catalog.attach_charging("new-trip", path)
            save.assert_called_once()
            self.assertEqual(trip["available_services"], ["charging", "energy"])
            self.assertFalse(catalog.public([trip])[0]["charging_operator_available"])

    def test_charging_advice_is_opt_in_and_requires_energy_and_normal_pace(self):
        path = "reviewed-stations.json"
        trip = {
            "id": "prepared",
            "available_services": ["energy", "charging"],
            "profile": {
                "charging_assignment": path,
                "inputs": {path: "recorded"},
                "moving_charge_mode": "operator-development",
                "forecast_candidate": "sumo-source-distance",
            },
        }
        with patch("catalog.check"):
            off = catalog.select([trip], "prepared", ["energy"])
            self.assertNotIn("charging_assignment", off)
            self.assertNotIn(path, off["inputs"])
            on = catalog.select([trip], "prepared", ["charging", "energy"])
            self.assertEqual(on["charging_assignment"], path)
            self.assertNotIn("forecast_candidate", on)
            self.assertNotIn("moving_charge_mode", on)
            self.assertNotIn("charging_startup_delay_working_s", on)
            for delay in (30, 60, 120):
                profile = catalog.select(
                    [trip],
                    "prepared",
                    ["charging", "energy"],
                    charging_startup_delay_working_s=delay,
                )
                self.assertEqual(profile["charging_startup_delay_working_s"], delay)
            for delay in (True, 30.0, -1, 15, 180, "30"):
                with self.subTest(delay=delay), self.assertRaises(ValueError):
                    catalog.select(
                        [trip],
                        "prepared",
                        ["charging", "energy"],
                        charging_startup_delay_working_s=delay,
                    )
            with self.assertRaisesRegex(ValueError, "Enable Charging Advisor"):
                catalog.select([trip], "prepared", ["energy"], charging_startup_delay_working_s=30)
            for modes, speed in ((["charging"], 1), (["charging", "energy"], 2)):
                with self.assertRaisesRegex(ValueError, "requires Energy Forecast"):
                    catalog.select([trip], "prepared", modes, speed)
        self.assertEqual(trip["profile"]["inputs"], {path: "recorded"})

    def test_starting_charge_defaults_to_the_preset_and_override_is_per_run(self):
        trip = {
            "id": "allowed",
            "available_services": [],
            "initial_charge_percent": 80,
            "profile": {"journey_id": "allowed"},
        }
        with patch("catalog.check"):
            default = catalog.select([trip], "allowed", [])
            self.assertEqual(default["initial_charge_percent"], 80)
            self.assertNotIn("starting_charge_percent", default)
            for value in (0.1, 9.8, 10, 58.3, 100):
                profile = catalog.select([trip], "allowed", [], charge_override=value)
                self.assertEqual(profile["starting_charge_percent"], value)
                self.assertEqual(profile["initial_charge_percent"], value)
            for value in (True, "50", 0, 100.1, 50.01, float("nan"), float("inf")):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    catalog.select([trip], "allowed", [], charge_override=value)
        self.assertEqual(trip["profile"], {"journey_id": "allowed"})

    def test_speed_allowlist_defaults_and_duration_budget(self):
        trips = [
            {
                "id": "allowed",
                "available_services": [],
                "profile": {"expected_working_minutes": 112.1, "maximum_real_life_s": 9344},
            }
        ]
        with patch("catalog.check"):
            self.assertEqual(catalog.select(trips, "allowed", [])["time_scale"], 1)
            for speed in (0.5, 1, 2, 5, 10, 20):
                result = catalog.select(trips, "allowed", [], speed)
                self.assertEqual(result["time_scale"], speed)
                self.assertGreater(result["maximum_real_life_s"], 112.1 * 60 / speed)
                self.assertLessEqual(result["maximum_real_life_s"], 14400)
            for speed in (True, "20", 0, 30, float("nan")):
                with self.subTest(speed=speed), self.assertRaises(ValueError):
                    catalog.select(trips, "allowed", [], speed)

    def test_no_shell_or_service_passthrough(self):
        trips = [
            {
                "id": "allowed",
                "available_services": ["replay"],
                "profile": {"journey_id": "allowed"},
            }
        ]
        with patch("catalog.check") as checked:
            for identity, services in [
                ("../../other", []),
                ("allowed", ["energy"]),
                ("allowed", ["replay", "replay"]),
                ("allowed", "replay"),
                ("allowed", ["replay; cmd.exe"]),
            ]:
                with (
                    self.subTest(identity=identity, services=services),
                    self.assertRaises(ValueError),
                ):
                    catalog.select(trips, identity, services)
            checked.assert_not_called()
            self.assertEqual(catalog.select(trips, "allowed", ["replay"])["services"], ["replay"])
            checked.assert_called_once()


class ApiTests(unittest.TestCase):
    def setUp(self):
        class Manager:
            starts = 0
            opens = 0
            dashboards = 0
            stopped = None
            recovered = None
            lock = threading.RLock()
            closing = False

            def status(self):
                return {"trips": [], "run": None, "closing": False}

            def start(self, trip, services, speed=1):
                catalog.time_scale(speed)
                self.starts += 1
                return self.status()

            def stop(self, identity):
                self.stopped = identity
                return self.status()

            def recover(self, identity):
                self.recovered = identity
                return self.status()

            def active(self):
                return True

            def history_page(self, **options):
                return {"items": [], "total": 0, "offset": options["offset"]}

            def open_history_folder(self, identity):
                self.opens += 1
                return {"opened": True}

            def open_dashboard(self, identity):
                self.dashboards += 1
                return {"opened": True, "key_copied": True}

            def charging_operator_key(self, identity):
                return {"charging_operator_token": "restricted-key"}

            def dashboard_control(self, token, request):
                return {"accepted": request} if token == "x" * 43 else None

        self.manager = Manager()
        self.server = Server(self.manager, "local-test-authority", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def call(self, method="GET", path="/api/status", headers=None, value=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = None if value is None else json.dumps(value)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_recovery_requires_launch_authority_and_one_trip_identity(self):
        path = "/api/recover"
        self.assertEqual(
            self.call(
                "POST",
                path,
                {**self.headers(), "Authorization": "Bearer reader"},
                {"id": "current-trip"},
            )[0],
            401,
        )
        self.assertEqual(
            self.call("POST", path, self.headers(), {"id": "current-trip", "path": "extra"})[0],
            404,
        )
        self.assertIsNone(self.manager.recovered)
        self.assertEqual(self.call("POST", path, self.headers(), {"id": "current-trip"})[0], 202)
        self.assertEqual(self.manager.recovered, "current-trip")

    def headers(self):
        return {
            "Authorization": "Bearer local-test-authority",
            "Origin": self.server.origin,
            "Content-Type": "application/json",
        }

    def test_authentication_origin_and_host_are_all_required(self):
        for changes in [
            {"Authorization": "Bearer monitoring-reader"},
            {"Origin": "https://example.com"},
            {"Origin": "null"},
            {"Host": "rebound.example"},
            {"Sec-Fetch-Site": "cross-site"},
        ]:
            with self.subTest(changes=changes):
                status, _, _ = self.call(
                    "POST",
                    "/api/start",
                    {**self.headers(), **changes},
                    {"trip_id": "allowed", "services": []},
                )
                self.assertIn(status, (401, 403))
        self.assertEqual(self.manager.starts, 0)
        status, _, _ = self.call(
            "POST", "/api/start", self.headers(), {"trip_id": "allowed", "services": []}
        )
        self.assertEqual(status, 202)
        self.assertEqual(self.manager.starts, 1)

    def test_untrusted_requests_never_return_private_state(self):
        self.assertEqual(self.call()[0], 401)
        self.assertEqual(self.call(path="/api/history")[0], 401)
        self.assertEqual(self.call(path="/.runtime/launch-app/instance.json")[0], 404)
        self.assertEqual(self.call(path="/../../README.md")[0], 404)
        status, _, headers = self.call(headers=self.headers())
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(
            json.loads(self.call(headers=self.headers())[1])["launcher_api_version"], API_VERSION
        )
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_saved_history_and_folder_open_use_launch_authority(self):
        self.assertEqual(
            self.call(
                path="/api/history?offset=20&search=road&outcome=all&refresh=1",
                headers=self.headers(),
            )[0],
            200,
        )
        for query in ("offset=bad", "path=other", "offset=1&offset=2", "a=1&b=2&c=3&d=4&e=5"):
            with self.subTest(query=query):
                self.assertEqual(
                    self.call(path="/api/history?" + query, headers=self.headers())[0], 400
                )
        self.assertEqual(
            self.call(
                "POST",
                "/api/open-results",
                {**self.headers(), "Origin": "https://example.com"},
                {"id": "test"},
            )[0],
            403,
        )
        self.assertEqual(self.manager.opens, 0)
        self.assertEqual(
            self.call(
                "POST", "/api/open-results", self.headers(), {"id": "test", "path": "arbitrary"}
            )[0],
            404,
        )
        self.assertEqual(self.manager.opens, 0)
        self.assertEqual(
            self.call("POST", "/api/open-results", self.headers(), {"id": "test"})[0], 200
        )
        self.assertEqual(self.manager.opens, 1)

    def test_malformed_mutations_and_active_close_are_refused(self):
        for path, value in [
            ("/api/start", {"command": "bad"}),
            ("/api/start", {"trip_id": "allowed", "services": [], "time_scale": True}),
            ("/api/start", {"trip_id": "allowed", "services": [], "time_scale": "20"}),
            ("/api/start", {"trip_id": "allowed", "services": [], "time_scale": 100}),
            ("/api/start?command=bad", {}),
            ("/api/stop", {"pid": 5}),
            ("/api/close", {}),
        ]:
            with self.subTest(path=path):
                self.assertIn(self.call("POST", path, self.headers(), value)[0], (400, 404))
        self.assertFalse(self.manager.closing)

    def test_dashboard_open_requires_launch_authority_and_rejects_url_arguments(self):
        for changes in (
            {"Authorization": "Bearer monitoring-reader"},
            {"Origin": "https://example.com"},
        ):
            self.assertIn(
                self.call(
                    "POST", "/api/open-dashboard", {**self.headers(), **changes}, {"id": "current"}
                )[0],
                (401, 403),
            )
        self.assertEqual(
            self.call(
                "POST",
                "/api/open-dashboard",
                self.headers(),
                {"id": "current", "url": "https://example.com"},
            )[0],
            404,
        )
        self.assertEqual(self.manager.dashboards, 0)
        self.assertEqual(
            self.call("POST", "/api/open-dashboard", self.headers(), {"id": "current"})[0], 200
        )
        self.assertEqual(self.manager.dashboards, 1)

    def test_stop_requires_current_trip_identity_and_launch_authority(self):
        path = "/api/stop"
        self.assertEqual(self.call("POST", path, self.headers(), {})[0], 404)
        self.assertEqual(self.call("POST", path, self.headers(), {"id": True})[0], 400)
        headers = {**self.headers(), "Authorization": "Bearer monitoring-reader"}
        self.assertEqual(self.call("POST", path, headers, {"id": "trip"})[0], 401)
        self.assertIsNone(self.manager.stopped)
        self.assertEqual(self.call("POST", path, self.headers(), {"id": "trip"})[0], 202)
        self.assertEqual(self.manager.stopped, "trip")

    def test_charging_key_requires_primary_launcher_authority_and_run_id(self):
        path = "/api/charging-operator-key"
        value = {"id": "active-trip"}
        for changed, expected in (
            ({"Authorization": "Bearer monitoring-reader"}, 401),
            ({"Origin": "http://127.0.0.1:18765"}, 403),
        ):
            self.assertEqual(
                self.call("POST", path, {**self.headers(), **changed}, value)[0], expected
            )
        self.assertEqual(self.call("GET", path, self.headers())[0], 404)
        self.assertEqual(self.call("POST", path, self.headers(), {"id": True})[0], 400)
        status, response, headers = self.call("POST", path, self.headers(), value)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)["charging_operator_token"], "restricted-key")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_dashboard_control_origin_and_request_scope(self):
        path = "/api/dashboard-trip-control"
        headers = {**self.headers(), "Origin": "http://127.0.0.1:18765"}
        self.assertEqual(self.call("OPTIONS", path, headers)[0], 204)
        self.assertEqual(self.call("OPTIONS", "/api/stop", headers)[0], 403)
        self.assertEqual(self.call("GET", path, self.headers())[0], 403)
        self.assertEqual(self.call("GET", path, headers)[0], 401)
        headers["Authorization"] = "Bearer " + "x" * 43
        self.assertEqual(self.call("GET", "/api/status", headers)[0], 403)
        self.assertEqual(self.call("POST", path, headers, {"stop": True})[0], 409)
        status, _, response = self.call("POST", path, headers, {"revision": 0, "paused": True})
        self.assertEqual(status, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], headers["Origin"])


if __name__ == "__main__":
    unittest.main()
