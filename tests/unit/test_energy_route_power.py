"""Power feasibility is a movement constraint, independent of map identity."""

import unittest
from dataclasses import replace

from test_energy_route_wire import request

from evdt.energy.route_aware.forecast import forecast, validate_result
from evdt.energy.route_aware.wire import canonical_bytes, parse_request


class PowerMovementTests(unittest.TestCase):
    def test_power_policy_is_optional_and_strict(self):
        job = request()
        self.assertNotIn(b"power_limited_motion", canonical_bytes(job))
        with self.assertRaises(ValueError):
            replace(job.policy, power_limited_motion=1)
        enabled = replace(job, policy=replace(job.policy, power_limited_motion=True))
        self.assertEqual(parse_request(canonical_bytes(enabled)), enabled)

    def test_low_power_reduces_motion_without_changing_limits(self):
        job = request()
        job = replace(job, vehicle=replace(job.vehicle, max_discharge_w=80_000))
        enabled = replace(job, policy=replace(job.policy, power_limited_motion=True))
        result = forecast(enabled)
        self.assertEqual(result.status, "available", result.reason)
        self.assertLessEqual(result.summary.ledger.unserved_energy_j, 1e-5)
        validate_result(enabled, result)
        self.assertEqual(result.model_version, "route-aware-energy/2.1.0-dev")
        self.assertEqual(enabled.vehicle.max_discharge_w, 80_000)

    def test_impossible_equipment_load_is_not_hidden(self):
        job = request()
        job = replace(
            job,
            vehicle=replace(job.vehicle, max_discharge_w=1),
            policy=replace(job.policy, power_limited_motion=True),
        )
        result = forecast(job)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "equipment_exceeds_available_power")

    def test_low_charge_still_reports_unserved_energy(self):
        job = request()
        job = replace(
            job,
            state=replace(job.state, soc=0.00001),
            policy=replace(job.policy, power_limited_motion=True),
        )
        result = forecast(job)
        self.assertEqual(result.status, "infeasible")
        self.assertIsNone(result.summary.arrival_soc)


if __name__ == "__main__":
    unittest.main()
