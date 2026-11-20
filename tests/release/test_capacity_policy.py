"""Offline checks for the Phase 6 release profiles and live stop conditions."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import full_measurement_report as report
import qualify_extension as controller
import smoke_policy as policy
from test_smoke_policy import GIB, linux, windows


class CapacityPolicyTest(unittest.TestCase):
    def test_exact_profiles_and_worker_budget(self):
        for name, count in zip(policy.CAPACITY, (1, 10)):
            with self.subTest(name=name):
                profile = policy.monitoring_profile(name)
                self.assertEqual(profile["assets"], [f"bus-{i:03}" for i in range(1, count + 1)])
                self.assertEqual(profile["warmup_samples"], 60)
                self.assertEqual(profile["measured_samples"], 1800)
                self.assertEqual(profile["total_samples"], 1860)
                self.assertEqual(profile["signals_per_asset"], 20)
                self.assertEqual(profile["browser"], "chromium-automated")
                self.assertTrue(profile["full_duration"])
                self.assertFalse(profile["host_capacity_qualified"])
                self.assertNotIn("phase_1_policy_version", profile)
                self.assertGreater(policy.CAPACITY_WORKER_TIMEOUT_S, profile["total_samples"] + 600)
                self.assertLess(policy.CAPACITY_WORKER_TIMEOUT_S, 55 * 60)

    def test_profile_rejects_modified_duration_rate_roster_or_count(self):
        for key, value in (
            ("measured_s", 300),
            ("assets", 5),
            ("segments", [{"duration_s": 1800, "messages_per_asset_s": 2}]),
            ("expected_measured_scalar_samples", 60000),
        ):
            spec = policy.baseline()
            spec["sequence"][2][key] = value
            with self.subTest(key=key), patch.object(policy, "baseline", return_value=spec):
                with self.assertRaisesRegex(ValueError, "frozen 30-minute"):
                    policy.monitoring_profile("W1-30")

    def test_report_refuses_shortened_release_before_reading_evidence(self):
        profile = policy.monitoring_profile("W0-30")
        profile.update(measured_samples=120, total_samples=180)
        with self.assertRaisesRegex(ValueError, "frozen duration"):
            report.build(Path("absent-evidence"), profile, phase6=True)

    def test_release_uses_separate_inventory(self):
        with patch.object(policy, "capacity_inventory", return_value={"files": 0, "sha256": ""}):
            for workload in policy.CAPACITY:
                with self.assertRaisesRegex(ValueError, "input inventory differ"):
                    policy.verify_sources(workload=workload, committed=False)

    @unittest.skipUnless(os.name == "nt", "The controller starts on Windows")
    def test_controller_refuses_both_workloads_when_launch_precheck_fails(self):
        scratch = policy.ROOT / ".runtime"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            for workload in policy.CAPACITY:
                with self.subTest(workload=workload):
                    path = root / "evidence/p6" / workload
                    argv = ["controller", "--workload", workload, "--evidence-dir", str(path)]
                    with (
                        patch.object(controller, "ROOT", root),
                        patch.object(controller, "fingerprints", return_value={}),
                        patch.object(controller.sys, "argv", argv),
                        patch.object(policy, "verify_sources", return_value={"passed": True}),
                        patch.object(policy, "preflight", return_value={"passed": False}),
                        patch.object(controller.subprocess, "Popen") as worker,
                    ):
                        self.assertEqual(
                            controller.main(workload_measurement=True, sustained=True, phase6=True),
                            1,
                        )
                    worker.assert_not_called()
                    result = json.loads((path / "qualification.json").read_bytes())
                    self.assertEqual(result["checkpoint"], "P6-B1")
                    self.assertEqual(result["profile"]["workload"], workload)
                    self.assertFalse(result["fixture_started"])
                    self.assertFalse(result["passed"])

    def test_windows_stops_on_live_clock_step(self):
        with patch.object(policy.shutil, "disk_usage", return_value=SimpleNamespace(free=80 * GIB)):
            guard = policy.WindowsSmokeGuard(Path("absent-linux-marker"), live_clock=True)
            guard.observe({**windows(0), "sequence": 0, "utc_s": 1000})
            self.assertTrue(guard.first_sample_validated)
            guard.observe({**windows(1), "sequence": 1, "utc_s": 1001.02})
            self.assertIn("Resource clock stepped", guard.failure["reason"])

    def test_local_trip_accepts_gradual_drift_and_rejects_a_clock_step(self):
        with patch.object(policy.shutil, "disk_usage", return_value=SimpleNamespace(free=80 * GIB)):
            guard = policy.WindowsSmokeGuard(
                Path("absent-linux-marker"), live_clock=True, allow_gradual_wall_drift=True
            )
            for second in range(925):
                guard.observe(
                    {**windows(second), "sequence": second, "utc_s": 1000 + second * 1.000012}
                )
            self.assertTrue(guard.first_sample_validated)
            self.assertIsNone(guard.failure)
            guard.observe({**windows(925), "sequence": 925, "utc_s": 1925.030})
            self.assertEqual(guard.failure["reason"], "Resource clock stepped")

    def test_linux_long_run_does_not_accumulate_samples_and_stops_on_late_gap(self):
        with patch.object(policy.shutil, "disk_usage", return_value=SimpleNamespace(free=80 * GIB)):
            guard = policy.LinuxSmokeGuard()
            for second in range(1860):
                guard.observe(linux(second, swapped=second))
            summary = guard.samples.summary()
            self.assertEqual(summary["samples"], 1860)
            self.assertEqual(summary["rolling_60s_swap_max_bytes"], 60 * 4096)
            self.assertEqual(len(guard.samples.window), 61)
            late = {**linux(1860), "monotonic_s": 1861, "utc_s": 11861}
            with self.assertRaisesRegex(ValueError, "sample coverage gap"):
                guard.observe(late)


if __name__ == "__main__":
    unittest.main()
