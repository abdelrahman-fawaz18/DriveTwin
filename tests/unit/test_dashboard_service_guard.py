"""Resource stops must prevent work while preserving bounded owned cleanup."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/release"))
from dashboard_service_guard import ReviewGuard  # noqa: E402


class ServiceGuardTests(unittest.TestCase):
    def guard(self):
        guard = ReviewGuard.__new__(ReviewGuard)
        guard.active = True
        guard.sampler = Mock(error=None)
        guard.guard = Mock(failure=None, previous=10.0, low_start=None)
        guard.collector = None
        return guard

    def test_failed_guard_refuses_new_process(self):
        guard = self.guard()
        guard.guard.failure = {"reason": "memory reserve"}
        with patch("dashboard_service_guard.subprocess.Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "memory reserve"):
                guard.run(["node.exe", "browser.mjs"])
            launch.assert_not_called()

    def test_linux_timeout_cancels_only_its_invocation(self):
        guard = self.guard()
        process = Mock()
        process.poll.return_value = None
        with (
            patch("dashboard_service_guard.subprocess.Popen", return_value=process) as launch,
            patch("dashboard_service_guard.subprocess.run") as cleanup,
            patch("dashboard_service_guard.time.monotonic", side_effect=[0, 2]),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                guard.run(
                    [sys.executable, "scripts/linux.py", "--", "python3", "case.py"], timeout=1
                )
            args = launch.call_args.args[0]
            invocation = args[args.index("--run-id") + 1]
            self.assertEqual(cleanup.call_args.args[0][-2:], ["--cancel", invocation])
            process.wait.assert_called_once_with(timeout=60)

    def test_measurement_tail_requires_samples_and_reserve(self):
        guard = self.guard()
        with patch("dashboard_service_guard.time.monotonic", return_value=12):
            with self.assertRaisesRegex(RuntimeError, "cover service completion"):
                guard.finish_measurement()
        guard.guard.previous = 40.0
        guard.guard.low_start = 9.0
        with patch("dashboard_service_guard.time.monotonic", return_value=40.5):
            with self.assertRaisesRegex(RuntimeError, "below 2 GiB"):
                guard.finish_measurement()

    def test_cleanup_is_not_blocked_by_failed_guard(self):
        guard = self.guard()
        guard.active = False
        guard.guard.failure = {"reason": "memory reserve"}
        with patch("dashboard_service_guard.subprocess.run") as cleanup:
            guard.run(["python", "cleanup.py"])
            cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
