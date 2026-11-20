"""Failed archive creation preserves the only recording for owned recovery."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / p) for p in ("src", "scripts/geographic", "scripts/launcher")]
import live_session  # noqa: E402
import recover_run  # noqa: E402


class LiveFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "live-journey-20261118T000000Z-abcdef"
        self.output.mkdir()
        self.project = recover_run.project_name(self.output)
        self.resources = {
            "container": ["owned-db", "owned-source"],
            "network": ["owned-network"],
            "volume": ["owned-database", "owned-spool"],
        }
        self.calls = []
        self.docker_calls = []
        self.archive_valid = True
        self.report = {"status": "failed_or_incomplete", "cleanup_verified": False}

    def recovery_resources(self, project, kind):
        self.assertEqual(project, self.project)
        return self.resources[kind].copy()

    def recovery_docker(self, *args, **kwargs):
        self.docker_calls.append(args)
        if args[:2] == ("container", "inspect"):
            identity = args[-1]
            return json.dumps(
                [
                    self.project,
                    "db" if identity == "owned-db" else "source",
                    "exited",
                    [{"Type": "volume", "Name": "owned-database"}],
                ]
            ).encode()
        if args[:2] == ("container", "rm"):
            self.resources["container"].clear()
        elif args[:2] == ("network", "rm"):
            self.resources["network"].clear()
        elif args[:3] == ("volume", "ls", "-q"):
            return "\n".join(self.resources["volume"]).encode()
        elif args[0] in ("start", "stop") or "pg_isready" in args:
            return b""
        elif "pg_dump" in args:
            kwargs["stdout"].write(b"PGDMParchive fixture")
        elif "pg_restore" in args:
            if not self.archive_valid:
                raise subprocess.CalledProcessError(1, args)
            self.assertTrue(kwargs["stdin"].read().startswith(b"PGDMP"))
        else:
            self.fail("Unexpected Docker action: " + repr(args))
        return b""

    def shell(self, *args):
        self.assertIn("label=com.docker.compose.project=" + self.project, args)
        return "\n".join(self.resources[args[1]]).encode()

    def compose(self, *args, **_options):
        self.calls.append(args)
        if args[0] == "down":
            self.resources["container"].clear()
            self.resources["network"].clear()
            if "--volumes" in args:
                self.resources["volume"].clear()

    def release(self, **options):
        return live_session.release_services(
            self.compose,
            self.shell,
            self.output,
            self.project,
            self.report,
            backup_expected=options.get("backup_expected", True),
        )

    def test_failed_or_unreadable_archive_is_preserved_for_inspection(self):
        for payload, dump_code, restore_code in (
            (b"partial recording", 1, 0),
            (b"", 0, 0),
            (b"wrong-format", 0, 0),
            (b"PGDMPbroken archive", 0, 1),
        ):
            with self.subTest(payload=payload, dump_code=dump_code, restore_code=restore_code):
                archive = self.output / "database.dump"
                if archive.exists():
                    archive.unlink()

                def command(args, **kwargs):
                    if "pg_dump" in args:
                        kwargs["stdout"].write(payload)
                        return SimpleNamespace(returncode=dump_code)
                    self.assertIn("pg_restore", args)
                    self.assertEqual(kwargs["stdin"].read(), payload)
                    return SimpleNamespace(returncode=restore_code)

                with patch.object(live_session.subprocess, "run", side_effect=command):
                    with self.assertRaises(RuntimeError):
                        live_session.save_recording(["owned-compose"], self.output, {})
                self.assertEqual(archive.read_bytes(), payload)

    def test_timeout_and_existing_archive_never_overwrite_original_bytes(self):
        payload = b"partial original recording"

        def interrupted(args, **kwargs):
            kwargs["stdout"].write(payload)
            raise subprocess.TimeoutExpired(args, 90)

        with patch.object(live_session.subprocess, "run", side_effect=interrupted) as command:
            with self.assertRaises(subprocess.TimeoutExpired):
                live_session.save_recording(["owned-compose"], self.output, {})
            self.assertEqual((self.output / "database.dump").read_bytes(), payload)
            with self.assertRaises(FileExistsError):
                live_session.save_recording(["owned-compose"], self.output, {})
            self.assertEqual(command.call_count, 1)
        self.assertEqual((self.output / "database.dump").read_bytes(), payload)

    def test_valid_archive_is_read_before_it_can_be_reported_saved(self):
        payload = b"PGDMPreadable fixture"

        def command(args, **kwargs):
            if "pg_dump" in args:
                kwargs["stdout"].write(payload)
            else:
                self.assertEqual(kwargs["stdin"].read(), payload)
                self.assertIn("--list", args)
            return SimpleNamespace(returncode=0)

        with patch.object(live_session.subprocess, "run", side_effect=command) as process:
            live_session.save_recording(["owned-compose"], self.output, {})
        self.assertEqual(process.call_count, 2)

    def test_backup_failure_stops_services_and_retains_recoverable_ownership(self):
        with self.assertRaisesRegex(RuntimeError, "need recovery"):
            self.release()
        self.assertEqual(self.calls, [("stop", "--timeout", "5")])
        self.assertEqual(self.resources["volume"], ["owned-database", "owned-spool"])
        self.assertEqual(self.resources["container"], ["owned-db", "owned-source"])
        self.assertFalse(self.report["cleanup_verified"])
        self.assertTrue(self.report["recovery_required"])
        receipt = recover_run.read_receipt(self.output)
        self.assertFalse(receipt["cleanup_verified"])
        self.assertFalse(receipt["backup_saved"])
        self.assertEqual(receipt["retained_volumes"], self.resources["volume"])

    def test_valid_dump_does_not_discard_source_data_after_prior_finalization_error(self):
        archive = self.output / "database.dump"
        archive.write_bytes(b"PGDMPvalid database snapshot")
        self.resources["volume"].append("owned-source-output")
        for action in ("source", "controller", "logs"):
            with self.subTest(action=action):
                self.calls.clear()
                self.report.update(
                    backup_saved=True,
                    finalization_errors={action: "Earlier capture or writer shutdown failed"},
                )
                with self.assertRaisesRegex(RuntimeError, "finalization is incomplete"):
                    self.release()
                self.assertEqual(self.calls, [("stop", "--timeout", "5")])
                self.assertEqual(len(self.resources["volume"]), 3)
                self.assertEqual(len(self.resources["container"]), 2)
                self.assertTrue(self.report["backup_saved"])
                self.assertFalse(self.report["cleanup_verified"])
                receipt = recover_run.read_receipt(self.output)
                self.assertTrue(receipt["original_backup_saved"])
                self.assertFalse(receipt["cleanup_verified"])
                self.assertIn("finalization is incomplete", receipt["backup_warning"])
                self.assertEqual(archive.read_bytes(), b"PGDMPvalid database snapshot")

    def test_ownership_receipt_survives_stop_failure(self):
        with patch.object(self, "compose", side_effect=RuntimeError("stop failed")):
            with self.assertRaisesRegex(RuntimeError, "stop failed"):
                self.release()
        self.assertTrue(recover_run.read_receipt(self.output))
        self.assertFalse(self.report["cleanup_verified"])
        self.assertEqual(len(self.resources["volume"]), 2)

    def test_unwritable_receipt_still_stops_services_without_deleting_data(self):
        with patch.object(live_session, "write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.release()
        self.assertEqual(self.calls, [("stop", "--timeout", "5")])
        self.assertEqual(len(self.resources["volume"]), 2)
        self.assertFalse(self.report["cleanup_verified"])

    def test_discovery_failure_still_saves_project_context_and_stops_services(self):
        with patch.object(self, "shell", side_effect=OSError("resource list failed")):
            with self.assertRaisesRegex(OSError, "resource list failed"):
                self.release()
        self.assertEqual(self.calls, [("stop", "--timeout", "5")])
        self.assertEqual(len(self.resources["volume"]), 2)
        self.assertTrue(self.report["recovery_required"])
        self.assertEqual(self.report["recovery_project"], self.project)
        receipt = recover_run.read_receipt(self.output)
        self.assertEqual(receipt["project"], self.project)
        self.assertFalse(receipt["discovery_complete"])
        self.assertFalse(receipt["cleanup_verified"])

    def test_success_or_setup_without_recording_releases_owned_resources(self):
        for options in ({"backup_saved": True}, {"backup_expected": False}):
            with self.subTest(options=options):
                self.resources = {"container": ["db"], "network": ["network"], "volume": ["db"]}
                self.report["backup_saved"] = options.get("backup_saved", False)
                self.release(**options)
                self.assertTrue(self.report["cleanup_verified"])
                self.assertFalse(any(self.resources.values()))

    def test_remaining_owned_resource_never_reports_success(self):
        self.report["backup_saved"] = True
        with patch.object(self, "compose"):
            with self.assertRaisesRegex(RuntimeError, "Owned resource remains"):
                self.release()
        self.assertFalse(self.report["cleanup_verified"])

    def test_existing_recovery_retries_retained_database_without_deleting_volumes(self):
        partial = self.output / "database.dump"
        partial.write_bytes(b"failed original dump")
        with self.assertRaises(RuntimeError):
            self.release()
        (self.output / "operator-result.json").write_text(json.dumps({"session": self.report}))

        def save_again(container, folder):
            self.assertEqual(container, "owned-db")
            (folder / recover_run.BACKUP).write_bytes(b"PGDMPrecovered fixture")
            return True

        with (
            patch.object(recover_run, "resources", side_effect=self.recovery_resources),
            patch.object(recover_run, "docker", side_effect=self.recovery_docker),
            patch.object(recover_run, "dump_database", side_effect=save_again),
        ):
            recover_run.release_trip(self.output)
        receipt = recover_run.read_receipt(self.output)
        self.assertTrue(receipt["backup_saved"])
        self.assertTrue(receipt["cleanup_verified"])
        self.assertFalse(self.resources["container"])
        self.assertFalse(self.resources["network"])
        self.assertEqual(self.resources["volume"], ["owned-database", "owned-spool"])
        self.assertEqual(partial.read_bytes(), b"failed original dump")

    def test_header_only_cached_archive_is_validated_and_refused(self):
        archive = self.output / recover_run.BACKUP
        archive.write_bytes(b"PGDMP")
        self.archive_valid = False
        with patch.object(recover_run, "docker", side_effect=self.recovery_docker):
            with self.assertRaises(subprocess.CalledProcessError):
                recover_run.dump_database("owned-db", self.output)
        self.assertTrue(any("pg_restore" in args for args in self.docker_calls))
        self.assertFalse(any("pg_dump" in args for args in self.docker_calls))
        self.assertEqual(self.docker_calls[-1], ("stop", "--time", "10", "owned-db"))
        self.assertEqual(archive.read_bytes(), b"PGDMP")

    def test_prior_saved_flag_cannot_bypass_corrupt_archive_validation(self):
        archive = self.output / recover_run.BACKUP
        archive.write_bytes(b"PGDMP")
        recover_run.save_receipt(
            self.output,
            {
                "version": 1,
                "trip": self.output.name,
                "project": self.project,
                "cleanup_verified": False,
                "backup_saved": True,
                "retained_volumes": self.resources["volume"],
            },
        )
        self.archive_valid = False
        with (
            patch.object(recover_run, "resources", side_effect=self.recovery_resources),
            patch.object(recover_run, "docker", side_effect=self.recovery_docker),
        ):
            with self.assertRaisesRegex(ValueError, "resources remain for retry"):
                recover_run.release_trip(self.output)
        receipt = recover_run.read_receipt(self.output)
        self.assertFalse(receipt["backup_saved"])
        self.assertFalse(receipt["cleanup_verified"])
        self.assertEqual(len(self.resources["container"]), 2)
        self.assertEqual(len(self.resources["volume"]), 2)
        self.assertEqual(archive.read_bytes(), b"PGDMP")

    def test_failed_retry_can_succeed_later_with_original_partial_preserved(self):
        self.archive_valid = False
        with (
            patch.object(recover_run, "resources", side_effect=self.recovery_resources),
            patch.object(recover_run, "docker", side_effect=self.recovery_docker),
        ):
            with self.assertRaisesRegex(ValueError, "resources remain for retry"):
                recover_run.release_trip(self.output)
            partial = self.output / "recovered-database.partial"
            original = partial.read_bytes()
            self.assertEqual(len(self.resources["container"]), 2)
            self.assertFalse(recover_run.read_receipt(self.output)["cleanup_verified"])
            self.archive_valid = True
            recover_run.release_trip(self.output)
        receipt = recover_run.read_receipt(self.output)
        self.assertTrue(receipt["cleanup_verified"])
        self.assertTrue(receipt["backup_saved"])
        self.assertEqual(
            receipt["backup_sha256"], recover_run.archive_sha256(self.output / recover_run.BACKUP)
        )
        self.assertEqual(partial.read_bytes(), original)
        self.assertFalse(self.resources["container"])
        self.assertFalse(self.resources["network"])
        self.assertEqual(len(self.resources["volume"]), 2)

    def test_unbound_cached_saved_flag_is_refused_without_database_owner(self):
        self.resources["container"] = ["owned-source"]
        archive = self.output / recover_run.BACKUP
        archive.write_bytes(b"PGDMP")
        recover_run.save_receipt(
            self.output,
            {
                "version": 1,
                "trip": self.output.name,
                "project": self.project,
                "cleanup_verified": False,
                "backup_saved": True,
                "retained_volumes": self.resources["volume"],
            },
        )
        with (
            patch.object(recover_run, "resources", side_effect=self.recovery_resources),
            patch.object(recover_run, "docker", side_effect=self.recovery_docker),
        ):
            with self.assertRaisesRegex(ValueError, "cannot be validated"):
                recover_run.release_trip(self.output)
            # The failed validation must not become a successful cleanup on retry.
            with self.assertRaisesRegex(ValueError, "cannot be validated"):
                recover_run.release_trip(self.output)
        self.assertFalse(recover_run.read_receipt(self.output)["backup_saved"])
        self.assertEqual(archive.read_bytes(), b"PGDMP")
        self.assertEqual(self.resources["container"], ["owned-source"])

    def test_owner_gone_archive_identity_survives_interrupted_receipt_write(self):
        self.resources["container"] = ["owned-source"]
        archive = self.output / recover_run.BACKUP
        archive.write_bytes(b"PGDMPpreviously validated archive")
        sha = recover_run.archive_sha256(archive)
        recover_run.save_receipt(
            self.output,
            {
                "version": 1,
                "trip": self.output.name,
                "project": self.project,
                "cleanup_verified": False,
                "backup_saved": True,
                "backup_sha256": sha,
                "retained_volumes": self.resources["volume"],
            },
        )
        with (
            patch.object(recover_run, "resources", side_effect=self.recovery_resources),
            patch.object(recover_run, "docker", side_effect=self.recovery_docker),
        ):
            with patch.object(recover_run, "archive_sha256", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    recover_run.release_trip(self.output)
            self.assertEqual(recover_run.read_receipt(self.output)["backup_sha256"], sha)
            recover_run.release_trip(self.output)
        self.assertTrue(recover_run.read_receipt(self.output)["backup_saved"])
        self.assertTrue(recover_run.read_receipt(self.output)["cleanup_verified"])

    def test_malformed_original_result_cannot_bypass_resource_recovery(self):
        (self.output / "operator-result.json").write_text(
            json.dumps(
                {
                    "status": "failed_or_incomplete",
                    "session": {"cleanup_verified": True, "backup_saved": True},
                    "runtime_host_guard": ["malformed"],
                }
            )
        )
        with (
            patch.object(recover_run, "resources", side_effect=self.recovery_resources),
            patch.object(recover_run, "docker", side_effect=self.recovery_docker),
        ):
            recover_run.release_trip(self.output)
        self.assertFalse(self.resources["container"])
        self.assertFalse(self.resources["network"])
        self.assertEqual(len(self.resources["volume"]), 2)
        receipt = recover_run.read_receipt(self.output)
        self.assertTrue(receipt["cleanup_verified"])
        self.assertTrue(receipt["backup_saved"])


if __name__ == "__main__":
    unittest.main()
