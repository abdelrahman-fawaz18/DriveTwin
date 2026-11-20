"""Deterministic exporter tests; these fixtures establish no measured result."""

import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "demo_export", Path(__file__).resolve().parents[2] / "scripts/demo/export_case.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DemoExportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original_root = MODULE.ROOT
        MODULE.ROOT = self.root
        self.profile = {"time_scale": 1, "starting_charge_percent": 11.6, "title": "Test fixture"}
        self.protocol = {
            "version": "browser-demo-capture/1.0.0",
            "fork_working_s": 20,
            "selected_bay": "station-1",
            "profile": self.profile,
            "outcomes": {},
        }
        ids = ["battery.pack.soc", "location.latitude", "location.longitude", "motion.speed"] + [
            f"test.signal{i}" for i in range(16)
        ]
        for branch in ("continue", "charge"):
            directory = self.root / "evidence/energy-v2" / branch
            directory.mkdir(parents=True)
            events, steps = [], []
            for t in range(1, 41):
                soc = 0.116 - t * 0.0001 if t <= 30 else (0.12 if branch == "charge" else 0.08)
                samples = [
                    {
                        "signal_id": s,
                        "unit": "1",
                        "value": soc if s == "battery.pack.soc" else 0,
                        "quality": "good",
                        "provenance": "test",
                    }
                    for s in ids
                ]
                events.append({"body": {"sim_time_s": t, "samples": samples}})
                steps.append(
                    {"sim_time_s": t, "values": {s["signal_id"]: s["value"] for s in samples}}
                )
            moving = {
                "delivered_j": 10_800_000 if branch == "charge" else 0,
                "events": [
                    {"event": state, "working_s": t, "bay_id": "station-1"}
                    for state, t in (
                        ("stop_inserted", 32),
                        ("charging_observed", 33),
                        ("target_observed", 34),
                        ("motion_resumed", 35),
                    )
                ]
                if branch == "charge"
                else [],
            }
            self.save(
                directory / "source-report.json",
                {
                    "passed": True,
                    "events": events,
                    "plant_summary": {
                        "mission_finished": True,
                        "moving_charge": moving,
                        "energy_ledger": {"maximum_residual_j": 0, "tolerance_j": 0.01},
                        "completed_sim_time_s": 40,
                    },
                },
            )
            self.save(directory / "plant-steps.json", steps)
            self.save(directory / "profile.json", self.profile)
            self.save(
                directory / "operator-result.json",
                {
                    "passed": True,
                    "source": {"files": 1, "sha256": "a" * 64},
                    "session": {
                        "status": "arrived",
                        "backup_saved": True,
                        "cleanup_verified": True,
                    },
                },
            )
            self.save(
                directory / "run-manifest.json",
                {
                    "sumo_version": "SUMO unit fixture",
                    "traci_protocol": 1,
                    "packages": {},
                    "model_version": "unit-fixture",
                    "step_s": 0.2,
                    "initial_energy_j": 146160000,
                    "capacity_j": 1260000000,
                    "sampling": {},
                    "catalog_version": "test",
                },
            )
            context = {
                "working_s": 30,
                "plan": {"remaining_stops": [{"stop_id": "passenger-stop"}]},
                "lane_id": "road_0",
                "lane_offset_m": 12,
                "external_index": 0,
                "charging_state": "not_charging",
                "current_stop_id": None,
                "remaining_dwell_working_s": None,
            }
            estimate = {
                "bay_id": "station-1",
                "target_soc": 0.12,
                "minimum_soc": 0.105,
                "destination_soc": 0.11,
                "arrival_working_s": 40,
                "wait_working_s": 0,
                "estimated_grid_energy_j": 11_000_000,
                "charging_working_s": 2,
            }
            rows = [
                {
                    "kind": "context",
                    "available_working_s": 30,
                    "http": 200,
                    "body": {"context": context},
                },
                {
                    "kind": "context",
                    "available_working_s": 38,
                    "http": 200,
                    "body": {
                        "context": {**context, "working_s": 38, "current_stop_id": "passenger-stop"}
                    },
                },
                {
                    "kind": "advisory",
                    "available_working_s": 10,
                    "http": 200,
                    "body": {"estimate": estimate},
                },
                {
                    "kind": "map",
                    "available_working_s": 1,
                    "http": 200,
                    "body": {
                        "bbox": [-74, 45, -73, 46],
                        "context_version": "1.0.0",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {
                                    "id": "station-1",
                                    "layer": "stations",
                                    "name": "Charging bay 2",
                                    "private": "not exported",
                                },
                                "geometry": {"type": "Point", "coordinates": [-73.5, 45.5]},
                            }
                        ],
                    },
                },
            ]
            if branch == "charge":
                rows.append(
                    {
                        "kind": "approval",
                        "available_working_s": 31,
                        "http": 200,
                        "body": {"command_id": "private-command"},
                    }
                )
                rows.append(
                    {
                        "kind": "advisory",
                        "available_working_s": 36,
                        "http": 200,
                        "body": {
                            "observed_effect": {
                                "state": "motion_resumed",
                                "observed_working_s": 35,
                                "delivered_j": 10_800_000,
                            }
                        },
                    }
                )
            (directory / "demo-capture.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
            )
            self.protocol["outcomes"][branch] = {
                "directory": directory.relative_to(self.root).as_posix(),
                "exit_code": 0,
                "actor_errors": [],
            }
        self.protocol_path = self.root / "evidence/energy-v2/protocol.json"
        self.save(self.protocol_path, self.protocol)

    @staticmethod
    def save(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def tearDown(self):
        MODULE.ROOT = self.original_root
        self.temporary.cleanup()

    def test_export_preserves_numbers_excludes_original_metadata_and_is_deterministic(self):
        first, second = self.root / "candidate-a", self.root / "candidate-b"
        MODULE.export_case(self.protocol_path, first)
        MODULE.export_case(self.protocol_path, second)
        self.assertEqual(
            {p.name: p.read_bytes() for p in first.iterdir()},
            {p.name: p.read_bytes() for p in second.iterdir()},
        )
        public = b"".join(p.read_bytes() for p in first.iterdir())
        self.assertNotIn(b"private-command", public)
        self.assertNotIn(b"not exported", public)
        manifest = MODULE.read(first / "manifest.json")
        self.assertEqual(manifest["decision"]["at"], 30)
        common = MODULE.read(first / "common.json")
        self.assertEqual(common["frames"][-1][0], 30)
        self.assertEqual(MODULE.read(first / "charge.json")["events"][0]["t"], 31)

    def test_physical_mismatch_is_never_hidden_by_a_tolerance(self):
        path = self.root / "evidence/energy-v2/charge/source-report.json"
        report = MODULE.read(path)
        report["events"][3]["body"]["samples"][0]["value"] += 0.00000000001
        self.save(path, report)
        with self.assertRaisesRegex(ValueError, "prefix differs"):
            MODULE.verify_pair(self.protocol)

    def test_old_approval_or_missing_cleanup_cannot_be_accepted(self):
        path = self.root / "evidence/energy-v2/charge/source-report.json"
        report = MODULE.read(path)
        original = deepcopy(report)
        report["plant_summary"]["moving_charge"]["events"][0]["working_s"] = 7
        self.save(path, report)
        with self.assertRaisesRegex(ValueError, "before the decision"):
            MODULE.verify_pair(self.protocol)
        self.save(path, original)
        self.save(
            path.parent / "operator-result.json",
            {"passed": True, "session": {"cleanup_verified": False}},
        )
        with self.assertRaisesRegex(ValueError, "cleanup not accepted"):
            MODULE.verify_pair(self.protocol)

    def test_route_context_and_source_changes_require_a_new_review(self):
        path = self.root / "evidence/energy-v2/charge/operator-result.json"
        result = MODULE.read(path)
        result["source"]["sha256"] = "b" * 64
        self.save(path, result)
        with self.assertRaisesRegex(ValueError, "inventory changed"):
            MODULE.verify_pair(self.protocol)

    def test_consumed_admission_does_not_replace_durable_source_validation(self):
        for branch in ("continue", "charge"):
            self.assertFalse((self.root / "evidence/energy-v2" / branch / "launch.json").exists())
        MODULE.verify_pair(self.protocol)
        path = self.root / "evidence/energy-v2/charge/operator-result.json"
        result = MODULE.read(path)
        result.pop("source")
        self.save(path, result)
        with self.assertRaisesRegex(ValueError, "durable source inventory"):
            MODULE.verify_pair(self.protocol)


if __name__ == "__main__":
    unittest.main()
