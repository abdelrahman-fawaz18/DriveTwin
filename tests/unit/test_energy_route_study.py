"""Study partition and denominator behavior without a simulator or database."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def load():
    sys.path.insert(0, str(ROOT / "scripts/route_aware_energy"))
    spec = importlib.util.spec_from_file_location(
        "route_study_test", ROOT / "scripts/route_aware_energy/study.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StudyTests(unittest.TestCase):
    def test_freeze_requires_host_guard_and_exact_source(self):
        module = load()
        result = {
            "partition": "development",
            "passed": True,
            "cleanup_verified": True,
            "candidate": {"source": "a"},
        }
        guarded = {
            "case": "study-development",
            "passed": True,
            "runtime_host_guard": {"first_sample_validated": True, "failure": None},
            "worker": {"source_unchanged": True},
        }
        module.check_freeze_evidence(result, guarded, "development", {"source": "a"})
        for changes in (
            {"passed": False},
            {"runtime_host_guard": {}},
            {"worker": {"source_unchanged": False}},
        ):
            with self.assertRaisesRegex(ValueError, "guarded evidence"):
                module.check_freeze_evidence(
                    result, {**guarded, **changes}, "development", {"source": "a"}
                )
        with self.assertRaises(ValueError):
            module.check_freeze_evidence(result, guarded, "development", {"source": "b"})

    def test_late_memory_failure_cannot_become_a_pass(self):
        load()
        from qualify import finish_host_guard

        report = {"passed": True}
        guard = SimpleNamespace(first_sample_validated=True, failure=None)
        sampler = SimpleNamespace(
            error=None,
            close=Mock(
                side_effect=lambda: setattr(guard, "failure", {"reason": "late reserve violation"})
            ),
        )
        finish_host_guard(report, sampler, guard)
        self.assertFalse(report["passed"])
        self.assertEqual(
            report["runtime_host_guard"]["failure"]["reason"], "late reserve violation"
        )

    def test_families_do_not_cross_partitions(self):
        module = load()
        partitions = [
            {r["family"] for r in module.recipes(name)}
            for name in ("development", "validation", "reserved")
        ]
        self.assertEqual([len(p) for p in partitions], [3, 3, 3])
        self.assertEqual(len(set.union(*partitions)), 9)

    def test_fresh_b4_replaces_only_the_reserved_routes(self):
        module = load()
        historical = []
        for partition in ("development", "validation", "reserved"):
            trips = module.recipes(partition)
            historical.extend(trips)
            if partition != "reserved":
                self.assertEqual(trips, module.recipes(partition, fresh_b4=True))
        fresh = module.recipes("reserved", fresh_b4=True)
        self.assertEqual(len(fresh), 3)
        for key in ("id", "family", "seed"):
            self.assertTrue(
                {trip[key] for trip in fresh}.isdisjoint(trip[key] for trip in historical)
            )
        historical_plans = {
            tuple(tuple(trip["points"][visit]) for visit in trip["visits"]) for trip in historical
        }
        for trip in fresh:
            plan = tuple(tuple(trip["points"][visit]) for visit in trip["visits"])
            self.assertNotIn(plan, historical_plans)
            self.assertNotIn(plan[::-1], historical_plans)

    def test_expanded_routes_cover_a_reserved_trip_over_an_hour(self):
        import math

        module = load()
        trips = module.recipes("reserved", expanded_routes=True)
        self.assertEqual(len(trips), 3)
        self.assertNotEqual(
            module.study_directory(True), module.study_directory(False, expanded_routes=True)
        )
        for trip in trips:
            self.assertTrue(trip["links"])
        longest = max(
            sum(
                math.dist(trip["points"][a], trip["points"][b])
                for a, b in zip(trip["visits"], trip["visits"][1:])
            )
            / trip["speed_m_s"]
            for trip in trips
        )
        self.assertGreater(longest, 3600)

    def test_fresh_b4_claim_cannot_be_reopened_by_changing_freeze(self):
        module = load()
        with tempfile.TemporaryDirectory() as temp, patch.object(module, "ROOT", Path(temp)):
            directory = module.study_directory(True)
            directory.mkdir(parents=True)
            output = module.ROOT / "evidence/energy-v2/reserved"
            freeze = {"study_id": module.FRESH_B4, "created_real_life_s": 1}
            path = module.consume_reservation(output, freeze, fresh_b4=True)
            with self.assertRaises(FileExistsError):
                module.consume_reservation(
                    output, {**freeze, "created_real_life_s": 2}, fresh_b4=True
                )
            recorded = json.loads((module.ROOT / path).read_bytes())
            self.assertTrue(recorded["consumed"])
            self.assertEqual(recorded["freeze_sha256"], module.fingerprint(freeze))

    def test_fresh_b4_uses_one_verified_worker_image(self):
        module = load()
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(module, "ROOT", Path(temp)),
            patch.object(module, "build_image", return_value="first-image") as build,
            patch.object(module, "verify_image") as verify,
        ):
            output = module.ROOT / "study"
            self.assertEqual(module.fresh_worker_image(output, None), "first-image")
            self.assertEqual(module.fresh_worker_image(output, None), "first-image")
            self.assertEqual(
                module.fresh_worker_image(output, {"worker_image": "frozen-image"}), "frozen-image"
            )
            build.assert_called_once_with(output)
            self.assertEqual(
                [call.args[0] for call in verify.call_args_list],
                ["first-image", "first-image", "frozen-image"],
            )

    def test_fresh_b4_freeze_requires_matching_complete_evidence(self):
        module = load()
        candidate = {"source": "a"}
        result = {
            "partition": "validation",
            "study_id": module.FRESH_B4,
            "passed": True,
            "cleanup_verified": True,
            "candidate": candidate,
            "trips": [
                {"id": trip["id"], "forecasts": [{}] * 7}
                for trip in module.recipes("validation", fresh_b4=True)
            ],
        }
        guarded = {
            "case": "study-validation",
            "fresh_b4": True,
            "passed": True,
            "runtime_host_guard": {"first_sample_validated": True, "failure": None},
            "worker": {**result, "source_unchanged": True},
        }
        module.check_freeze_evidence(result, guarded, "validation", candidate, fresh_b4=True)
        with self.assertRaises(ValueError):
            module.check_freeze_evidence(result, guarded, "validation", candidate)
        result["trips"][0]["forecasts"].pop()
        with self.assertRaisesRegex(ValueError, "all seven cutoffs"):
            module.check_freeze_evidence(result, guarded, "validation", candidate, fresh_b4=True)

    def test_missing_prediction_cannot_improve_accuracy(self):
        result = load().metrics(
            [
                {
                    "forecasts": [
                        {"status": "unavailable", "elapsed_real_life_s": 1, "baseline_j": None}
                    ]
                }
            ]
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["available_cutoffs"], 0)
        self.assertIsNone(result["trip_balanced_normalized_mae"])

    def test_equal_trip_weight_and_false_safe_failure(self):
        good = {
            "signed_error_j": 10,
            "reference_net_j": 100,
            "normalized_absolute_error": 0.1,
            "normalized_worst_error": 0.1,
            "false_safe": False,
            "convergence_relative": 0.001,
            "elapsed_real_life_s": 1,
            "baseline_j": 150,
            "distance_closure_m": 0.01,
            "distance_closure_limit_m": 0.1,
        }
        high = {**good, "normalized_absolute_error": 0.9, "false_safe": True}
        result = load().metrics([{"forecasts": [good] * 10}, {"forecasts": [high]}])
        self.assertAlmostEqual(result["trip_balanced_normalized_mae"], 0.5)
        self.assertEqual(result["false_safe_count"], 1)
        self.assertFalse(result["passed"])

    def test_stops_on_repeated_roads_get_distinct_occurrences(self):
        load()
        from reference_sumo import stop_indices, validate_progress

        stops = [{"external_index": i, "lane_id": "b_0", "offset_m": 50} for i in (1, 5)]
        self.assertEqual(stop_indices(["a", "b", "c", "d"] * 2, stops), [0, 2])
        row = {
            "working_s": 120,
            "external_index": 1,
            "lane_id": "b_0",
            "lane_offset_m": 50,
            "speed_m_s": 0,
            "completed_stop_indices": [],
            "current_stop_index": 0,
        }
        validate_progress(row, stops)
        with self.assertRaisesRegex(ValueError, "assigned occurrence"):
            validate_progress(
                {**row, "completed_stop_indices": [0], "current_stop_index": 1}, stops
            )
        with self.assertRaisesRegex(ValueError, "before its assigned occurrence"):
            validate_progress(
                {**row, "completed_stop_indices": [0, 1], "current_stop_index": None}, stops
            )
        completed = {**row, "completed_stop_indices": [0], "current_stop_index": None}
        with self.assertRaisesRegex(ValueError, "before its assigned occurrence"):
            validate_progress({**completed, "lane_offset_m": 1.04}, stops)
        junction = {**completed, "lane_id": ":n2_0_0", "lane_offset_m": 1.04}
        validate_progress(junction, stops)
        with self.assertRaisesRegex(ValueError, "before its assigned occurrence"):
            validate_progress({**junction, "completed_stop_indices": [0, 1]}, stops)

    def test_distance_failure_cannot_pass_on_energy_error_alone(self):
        row = {
            "signed_error_j": 0,
            "reference_net_j": 1,
            "normalized_absolute_error": 0,
            "normalized_worst_error": 0,
            "false_safe": False,
            "convergence_relative": 0,
            "elapsed_real_life_s": 1,
            "baseline_j": None,
            "distance_closure_m": 2,
            "distance_closure_limit_m": 0.1,
        }
        result = load().metrics([{"forecasts": [row]}])
        self.assertFalse(result["passed"])
        self.assertFalse(result["distance_closure_passed"])

    def test_sumo_stop_entry_retains_measured_speed_but_is_not_a_forecast_cutoff(self):
        module = load()
        from reference_sumo import validate_progress

        stop = {"external_index": 0, "lane_id": "e0_0", "offset_m": 484.96}
        entering = {
            "working_s": 30,
            "external_index": 0,
            "lane_id": "e0_0",
            "lane_offset_m": 484.96,
            "speed_m_s": 0.044118,
            "completed_stop_indices": [],
            "current_stop_index": 0,
            "stop_entry_transition": True,
        }
        validate_progress(entering, [stop])
        for changed in (
            {"stop_entry_transition": False},
            {"speed_m_s": 0.11},
            {"external_index": 1},
            {"lane_id": "e1_0"},
            {"lane_offset_m": 484.5},
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, "assigned occurrence"):
                    validate_progress({**entering, **changed}, [stop])
        with self.assertRaisesRegex(ValueError, "requires a current stop"):
            validate_progress({**entering, "current_stop_index": None}, [stop])

        settled = {
            **entering,
            "working_s": 31,
            "speed_m_s": 0,
            "stop_entry_transition": False,
        }
        validate_progress(settled, [stop])
        rows = [{"working_s": t, "lane_id": "e0_0"} for t in (0.2, 30, 31, 60, 120, 150, 180, 200)]
        rows[1], rows[2] = entering, settled
        cutoffs = [rows[i]["working_s"] for i in module.cutoffs(rows)]
        self.assertIn(31, cutoffs)
        self.assertNotIn(30, cutoffs)
