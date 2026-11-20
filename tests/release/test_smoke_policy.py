"""Phase 6 must fail closed without changing the approved Phase 1 profile."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import smoke_policy as policy
from workload_profile import select

GIB = 1024**3


def windows(second, *, available=3 * GIB, commit=2 * GIB, wsl=100):
    return {
        "monotonic_s": second,
        "physical_available_bytes": available,
        "commit": {
            "total_bytes": 12 * GIB,
            "limit_bytes": 12 * GIB + commit,
            "available_bytes": commit,
        },
        "wsl_name_total_bytes": wsl,
    }


def linux(second, *, memory=GIB, swapped=0, oom=0):
    return {
        "sequence": second,
        "monotonic_s": second,
        "utc_s": 10000 + second,
        "boot_id": "fixture",
        "swap_in_pages": swapped,
        "swap_out_pages": 0,
        "page_bytes": 4096,
        "containers": [{"group": "/core", "memory_bytes": memory, "memory_events": {"oom": oom}}],
        "stopped": [],
        "linux_memory": {"MemAvailable": 2 * GIB},
    }


class SmokePolicyTest(unittest.TestCase):
    def test_history_db_restart_starts_fresh_counters_with_all_guards_active(self):
        def row(second, service, started_at, maximum, oom=0):
            sample = linux(second, oom=oom)
            group = sample["containers"][0]
            group.update(service=service, container_started_at=started_at)
            group["memory_events"]["max"] = maximum
            return sample

        for enabled, service, start, second, oom, error in (
            (True, "db", "new", 1, 0, None),
            (True, "db", "old", 1, 0, "Cgroup counter reset"),
            (True, "api", "new", 1, 0, "Cgroup counter reset"),
            (False, "db", "new", 1, 0, "Cgroup counter reset"),
            (True, "db", "", 1, 0, "Missing container start identity"),
            (True, "db", "new", 1, 1, "Cgroup OOM event"),
            (True, "db", "new", 2, 0, "Resource sample coverage gap"),
        ):
            with self.subTest(enabled=enabled, service=service, start=start, error=error):
                samples = policy.LinuxResourceSamples(restartable_services=("db",) if enabled else ())
                samples.observe(row(0, service, "old", 108791))
                after = row(second, service, start, 0, oom)
                after["sequence"] = 1
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        samples.observe(after)
                else:
                    samples.observe(after)
                    self.assertTrue(samples.within_budget)

    def test_smoke_counts_and_phase1_unchanged(self):
        smoke = policy.smoke_profile()
        self.assertEqual(smoke["assets"], ["bus-001"])
        self.assertEqual(
            (smoke["warmup_samples"], smoke["measured_samples"], smoke["total_samples"]),
            (60, 30, 90),
        )
        self.assertEqual(smoke["signals_per_asset"] * smoke["measured_samples"], 600)
        self.assertFalse(smoke["full_duration"])
        self.assertEqual(select("W0", smoke["browser"])["measured_samples"], 120)
        self.assertEqual(select("W1", smoke["browser"])["measured_samples"], 300)

    def test_smoke_rejects_changed_duration(self):
        baseline = policy.baseline()
        baseline["sequence"][0]["measured_s"] = 29
        with patch.object(policy, "baseline", return_value=baseline):
            with self.assertRaisesRegex(ValueError, "frozen one-bus"):
                policy.smoke_profile()

    def test_verified_sources_refuse_inventory_change(self):
        with patch.object(policy, "inventory", return_value={"files": 0, "sha256": "other"}):
            with self.assertRaisesRegex(ValueError, "inventory differ"):
                policy.verify_sources(committed=False)

    def test_live_requires_the_committed_release(self):
        with patch.object(policy, "git", return_value=b"other release"):
            with self.assertRaisesRegex(ValueError, "Commit the reviewed"):
                policy.verify_sources()

    def test_precheck_exact_limits_are_accepted(self):
        limits = policy.baseline()["host_launch_precheck"]
        good = [windows(i, available=2 * GIB, commit=GIB) for i in range(31)]
        self.assertTrue(policy.host_precheck(good, limits)["passed"])
        self.assertFalse(policy.host_precheck(good[:-1], limits)["passed"])

    def test_precheck_refuses_one_low_or_missing_sample(self):
        limits = policy.baseline()["host_launch_precheck"]
        for bad in (
            windows(15, available=2 * GIB - 1),
            windows(15, commit=GIB - 1),
            windows(15, wsl=None),
        ):
            with self.subTest(bad=bad):
                rows = [windows(i) for i in range(31)]
                rows[15] = bad
                self.assertFalse(policy.host_precheck(rows, limits)["passed"])
        self.assertFalse(
            policy.host_precheck([windows(i) for i in range(32) if i != 15], limits)["passed"]
        )

    def test_live_guard_requires_valid_first_sample_and_rejects_long_low_period(self):
        with patch.object(
            policy.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * GIB)
        ):
            guard = policy.WindowsSmokeGuard(Path("absent-linux-marker"))
            guard.observe(windows(0, available=2 * GIB - 1))
            self.assertFalse(guard.first_sample_validated)
            self.assertIn("launch floor", guard.failure["reason"])
            guard = policy.WindowsSmokeGuard(Path("absent-linux-marker"))
            guard.observe(windows(0))
            self.assertTrue(guard.first_sample_validated)
            for i in range(1, 31):
                guard.observe(windows(i, available=2 * GIB - 1))
            self.assertIsNone(guard.failure)
            guard.observe(windows(31))
            self.assertIn("over 30 seconds", guard.failure["reason"])

    def test_linux_guard_refuses_oom_core_and_swap(self):
        with patch.object(
            policy.shutil, "disk_usage", return_value=SimpleNamespace(free=100 * GIB)
        ):
            for row, reason in (
                (linux(1, oom=1), "OOM"),
                (linux(1, memory=6 * GIB + 1), "core-memory"),
            ):
                with self.subTest(reason=reason):
                    guard = policy.LinuxSmokeGuard()
                    guard.observe(linux(0))
                    with self.assertRaisesRegex(ValueError, reason):
                        guard.observe(row)
            guard = policy.LinuxSmokeGuard()
            for i in range(62):
                guard.observe(linux(i, swapped=512 * i))
            with self.assertRaisesRegex(ValueError, "swap"):
                guard.observe(linux(62, swapped=512 * 62))

    def test_linux_guard_refuses_disk_reserve(self):
        with patch.object(
            policy.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * GIB - 1)
        ):
            with self.assertRaisesRegex(ValueError, "Ubuntu disk reserve"):
                policy.LinuxSmokeGuard().observe(linux(0))

    def test_launch_receipt_is_target_bound_expiring_and_one_use(self):
        scratch = policy.ROOT / ".runtime"
        scratch.mkdir(exist_ok=True)
        root = Path(self.enterContext(TemporaryDirectory(dir=scratch)))
        target = root / "services"
        with patch.object(policy, "ROOT", root), patch.object(policy.time, "time", return_value=10):
            policy.launch_receipt(root, target)
            with self.assertRaisesRegex(ValueError, "exact evidence"):
                policy.consume_receipt(root, root / "other")
            with self.assertRaisesRegex(ValueError, "exact evidence"):
                policy.consume_receipt(root, target, "W0-30")
            policy.consume_receipt(root, target)
            with self.assertRaises(FileNotFoundError):
                policy.consume_receipt(root, target)
            policy.launch_receipt(root, target)
            with patch.object(policy.time, "time", return_value=56):
                with self.assertRaisesRegex(ValueError, "fresh precheck"):
                    policy.consume_receipt(root, target)


if __name__ == "__main__":
    unittest.main()
