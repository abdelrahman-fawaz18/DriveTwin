"""Review preparation checks run without Docker, browsers, or vehicle workloads."""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / p)
    for p in (
        "scripts/release",
        "scripts/commands",
        "scripts/monitoring",
        "scripts/release_checks",
        "src",
    )
]
import demo_session as demo  # noqa: E402
import qualify_restarts as runner  # noqa: E402


class ReviewPolicyTests(unittest.TestCase):
    def test_counted_history_preserves_short_case_and_reads_full_review(self):
        self.assertIn("limit=17", demo.observation_path(16))
        self.assertIn("limit=301", demo.observation_path(demo.review_profile()["samples"]))
        self.assertIn(demo.DASHBOARD_REVIEW, runner.CASES)

    def test_profile_rejects_unsafe_or_incomplete_edits(self):
        policy = demo.review_profile()
        for field, replacement in (
            ("buses", 10),
            ("signals", 19),
            ("samples", 16),
            ("sample_period_s", 0.1),
            ("pause_seconds", 0),
            ("recording_review_seconds", 0),
            ("worker_timeout_seconds", 3600),
            ("live_capture_rounds", 1),
            ("automatic_dispatch", True),
        ):
            changed = copy.deepcopy(policy)
            changed[field] = replacement
            with self.subTest(field=field), patch.object(demo, "read", return_value=changed):
                with self.assertRaises(RuntimeError):
                    demo.review_profile()

    def test_browser_handoff_refuses_external_address_before_copy_or_open(self):
        with (
            patch.object(runner.subprocess, "run") as process,
            patch.object(runner.os, "startfile", create=True) as browser,
        ):
            with self.assertRaises(RuntimeError):
                runner.open_review_page({"monitoring_url": "https://example.invalid"})
            process.assert_not_called()
            browser.assert_not_called()

    def test_browser_handoff_keeps_monitoring_and_replay_credentials_separate(self):
        ready = {
            "monitoring_url": "http://127.0.0.1:18765",
            "replay_url": "http://127.0.0.1:18766",
            "monitoring_token": "synthetic-reader-value",
            "replay_token": "synthetic-replay-value",
        }
        for replay in (False, True):
            name = "replay" if replay else "monitoring"
            other = "monitoring" if replay else "replay"
            with (
                self.subTest(replay=replay),
                patch.object(runner.subprocess, "run") as process,
                patch.object(runner.os, "startfile", create=True) as browser,
                patch("builtins.print") as output,
            ):
                runner.open_review_page(ready, replay=replay)
                terminal = "\n".join(str(call.args[0]) for call in output.call_args_list)
                if replay:
                    process.assert_not_called()
                    browser.assert_called_once_with(
                        ready["replay_url"] + "/#access=" + ready["replay_token"]
                    )
                    self.assertNotIn(ready["replay_token"], terminal)
                else:
                    self.assertEqual(process.call_args.kwargs["input"], ready[name + "_token"])
                    browser.assert_called_once_with(ready[name + "_url"])
                    self.assertIn(ready[name + "_token"], terminal)
                    self.assertIn(ready[name + "_url"], terminal)
                self.assertNotIn(ready[other + "_token"], terminal)

    def test_check_only_cannot_launch_services(self):
        spec = importlib.util.spec_from_file_location(
            "dashboard_review", ROOT / "scripts/dashboard_review.py"
        )
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)
        with (
            patch.object(sys, "argv", ["dashboard_review.py", "--check-only"]),
            patch.object(wrapper, "check_tools", return_value={}),
            patch.object(wrapper, "run_review") as launch,
        ):
            self.assertEqual(wrapper.main(), 0)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
