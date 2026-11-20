"""Qualification retains the snapshot that satisfied an availability check."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

folder = Path(__file__).resolve().parents[2] / "scripts/route_aware_energy"
spec = importlib.util.spec_from_file_location(
    "energy_connected_check", folder / "connected_check.py"
)
module = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(folder), *sys.path]):
    spec.loader.exec_module(module)


class ConnectedWaitTests(unittest.TestCase):
    def test_successful_snapshot_is_returned_without_another_read(self):
        snapshot = {"status": "available", "current": {"cutoff_working_s": 6}}
        read = Mock(side_effect=[None, snapshot, None])
        with patch.object(module.time, "sleep"):
            self.assertIs(module.wait_for(read, "missing", 2), snapshot)
        self.assertEqual(read.call_count, 2)

    def test_missing_snapshot_keeps_bounded_failure(self):
        with (
            patch.object(module.time, "monotonic", side_effect=[0, 0, 3]),
            patch.object(module.time, "sleep"),
            self.assertRaisesRegex(RuntimeError, "missing forecast"),
        ):
            module.wait_for(lambda: None, "missing forecast", 2)
