"""Forecast lifecycle values, determinism and strict result/input binding."""

import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace

from test_energy_route_wire import request

from evdt.energy.route_aware.forecast import forecast, parse_result, validate_result
from evdt.energy.route_aware.wire import canonical_bytes


class ForecastTests(unittest.TestCase):
    def test_one_request_worker_and_invalid_input(self):
        job = request()
        process = subprocess.run(
            [sys.executable, "-m", "evdt.energy.route_aware.worker"],
            input=canonical_bytes(job),
            capture_output=True,
            timeout=10,
            env={**os.environ, "PYTHONPATH": "src"},
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        validate_result(job, parse_result(process.stdout))
        self.assertEqual(process.stderr, b"")
        invalid = subprocess.run(
            [sys.executable, "-m", "evdt.energy.route_aware.worker"],
            input=b'{"unknown":true}',
            capture_output=True,
            timeout=10,
            env={**os.environ, "PYTHONPATH": "src"},
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, b"")
        self.assertEqual(invalid.stderr.strip(), b"invalid_forecast_request")

    def test_available_forecast_is_deterministic_and_roundtrips(self):
        job = request()
        job = replace(job, vehicle=replace(job.vehicle, max_discharge_w=1_000_000))
        result = forecast(job)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.uncertainty_method, "not_established")
        self.assertEqual(result, forecast(job))
        self.assertEqual(result, parse_result(canonical_bytes(result)))
        validate_result(job, result)
        self.assertGreater(result.summary.duration_working_s, 0)
        self.assertLess(result.summary.arrival_soc, job.state.soc)

    def test_current_speed_is_not_lowered_to_hide_infeasible_stopping(self):
        job = request()
        job = replace(job, state=replace(job.state, speed_m_s=40))
        result = forecast(job)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "current_speed_cannot_meet_plan")
        self.assertIsNone(result.summary)

    def test_unserved_plan_does_not_expose_an_achievable_arrival(self):
        job = request()
        result = forecast(replace(job, vehicle=replace(job.vehicle, max_discharge_w=100)))
        self.assertEqual(result.status, "infeasible")
        self.assertIsNone(result.summary.arrival_soc)
        self.assertGreater(result.summary.ledger.unserved_energy_j, 0)

    def test_wrong_generation_scope_or_inputs_are_rejected(self):
        job = request()
        result = forecast(job)
        for changed in (
            replace(result, generation=result.generation + 1),
            replace(result, scope=replace(result.scope, asset_id="other-bus")),
            replace(result, vehicle_sha256="b" * 64),
        ):
            with self.assertRaises(ValueError):
                validate_result(job, changed)

    def test_output_unknown_fields_nonfinite_and_corrupt_balances_reject(self):
        job = request()
        raw = json.loads(canonical_bytes(forecast(job)))
        raw["unexpected"] = True
        with self.assertRaises(ValueError):
            parse_result(json.dumps(raw).encode())
        del raw["unexpected"]
        raw["summary"]["ledger"]["battery_draw_j"] += 1000
        with self.assertRaises(ValueError):
            parse_result(json.dumps(raw).encode())
        raw["summary"]["ledger"]["battery_draw_j"] = float("nan")
        with self.assertRaises(ValueError):
            parse_result(json.dumps(raw).encode())

    def test_charging_state_remains_a_separate_admission_condition(self):
        job = request()
        result = forecast(replace(job, state=replace(job.state, charging_state="charging")))
        self.assertEqual(result.reason, "charging_state_not_supported")
        self.assertIsNone(result.summary)


if __name__ == "__main__":
    unittest.main()
