"""Inspect launch boundaries without starting Docker or a bus."""

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "route_worker_runtime", ROOT / "scripts/route_aware_energy/runtime.py"
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class WorkerBoundaryTests(unittest.TestCase):
    def test_foreign_worker_cannot_be_removed(self):
        raw = json.dumps(
            [{"Config": {"Labels": {"drivetwin.route-energy.owner": "other"}}}]
        ).encode()
        with patch.object(
            runtime, "docker", return_value=subprocess.CompletedProcess([], 0, raw, b"")
        ) as call:
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                runtime.remove_owned(12, "expected")
            self.assertEqual(call.call_count, 1)

    def test_failed_inspection_does_not_turn_daemon_failure_into_cleanup(self):
        with patch.object(
            runtime,
            "docker",
            side_effect=[
                subprocess.CompletedProcess([], 1, b"", b""),
                RuntimeError("daemon unavailable"),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "daemon unavailable"):
                runtime.remove_owned(12, "owner")

    def test_worker_name_is_bounded_and_not_a_shell_expression(self):
        for value in (True, 0, -1, "1;other", 2**54):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.worker_name(value)
        self.assertEqual(runtime.worker_name(23), "evdt-route-energy-23")

    def test_image_cannot_use_mutable_tag_or_wrong_source(self):
        with patch.object(runtime, "docker") as call:
            with self.assertRaises(ValueError):
                runtime.verify_image("latest")
            call.assert_not_called()
        raw = json.dumps([{"Config": {"Labels": {}}}]).encode()
        with patch.object(
            runtime, "docker", return_value=subprocess.CompletedProcess([], 0, raw, b"")
        ):
            with self.assertRaisesRegex(ValueError, "source"):
                runtime.verify_image("sha256:" + "a" * 64)
