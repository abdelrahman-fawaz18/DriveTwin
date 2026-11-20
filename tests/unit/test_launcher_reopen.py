"""An outdated idle launcher refreshes without interrupting an active run."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("launch_entry", ROOT / "scripts/launch_drivetwin.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class Response(io.BytesIO):
    status = 200

    def __init__(self, value):
        super().__init__(json.dumps(value).encode())


class ReopenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        entry.write(self.directory / "instance.json", {"token": "test-launch-key"})

    def test_malformed_connection_receipt_does_not_crash_or_send_a_credential(self):
        for value in (None, [], True, {"token": 123}, {"token": "x" * 129}):
            with self.subTest(value=value):
                entry.write(self.directory / "instance.json", value)
                with patch.object(entry, "urlopen") as request:
                    self.assertFalse(entry.reopen(self.directory, False))
                request.assert_not_called()

    def test_unknown_status_or_activity_never_causes_a_shutdown(self):
        for value in ([], None, {}, {"run": []}, {"run": {}}, {"run": {"active": "false"}}):
            with self.subTest(value=value):
                with (
                    patch.object(entry, "urlopen", return_value=Response(value)) as request,
                    patch.object(entry.webbrowser, "open") as browser,
                ):
                    self.assertFalse(entry.reopen(self.directory, True))
                self.assertEqual(request.call_count, 1)
                browser.assert_not_called()

    def test_close_confirmation_must_be_an_explicit_object(self):
        with patch.object(
            entry,
            "urlopen",
            side_effect=[Response({"run": None}), Response([])],
        ):
            with self.assertRaisesRegex(RuntimeError, "could not close"):
                entry.reopen(self.directory, False)

    def test_current_service_reopens_without_restarting(self):
        with (
            patch.object(
                entry, "urlopen", return_value=Response({"launcher_api_version": entry.API_VERSION})
            ) as request,
            patch.object(entry.webbrowser, "open") as browser,
        ):
            self.assertTrue(entry.reopen(self.directory, True))
        self.assertEqual(request.call_count, 1)
        browser.assert_called_once_with("http://127.0.0.1:18760/#test-launch-key")

    def test_old_idle_service_gets_authenticated_graceful_close(self):
        with (
            patch.object(
                entry,
                "urlopen",
                side_effect=[Response({"run": {"active": False}}), Response({"closed": True})],
            ) as request,
            patch.object(entry.webbrowser, "open") as browser,
        ):
            self.assertFalse(entry.reopen(self.directory, True))
        browser.assert_not_called()
        close = request.call_args_list[1].args[0]
        self.assertEqual(close.full_url, "http://127.0.0.1:18760/api/close")
        self.assertEqual(close.data, b"{}")
        self.assertEqual(close.headers["Origin"], "http://127.0.0.1:18760")
        self.assertEqual(close.headers["Authorization"], "Bearer test-launch-key")

    def test_active_old_service_is_never_closed(self):
        with (
            patch.object(
                entry, "urlopen", return_value=Response({"run": {"active": True}})
            ) as request,
            patch.object(entry.webbrowser, "open"),
        ):
            self.assertTrue(entry.reopen(self.directory, True))
        self.assertEqual(request.call_count, 1)

    def test_second_launch_after_idle_shutdown_race_preserves_new_run(self):
        with (
            patch.object(
                entry,
                "urlopen",
                side_effect=[Response({"run": None}), OSError("Active run now; close refused")],
            ),
            patch.object(entry.webbrowser, "open") as browser,
        ):
            self.assertFalse(entry.reopen(self.directory, True))
        browser.assert_not_called()
