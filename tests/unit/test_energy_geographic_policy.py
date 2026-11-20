"""Geographic assignments retain complete, strict policy and legacy wire forms."""

import json
import unittest
from pathlib import Path

from evdt.energy.route_aware.policy import ForecastPolicy
from evdt.energy.route_aware.wire import decode_record


class GeographicPolicyTests(unittest.TestCase):
    def test_geographic_policy_retains_resource_and_timing_limits(self):
        root = Path(__file__).resolve().parents[2]
        data = json.loads(
            (root / "qualification/energy/route-aware/geographic-policy.json").read_bytes()
        )
        policy = decode_record(ForecastPolicy, data)
        self.assertTrue(policy.power_limited_motion)
        self.assertTrue(policy.road_limits_include_turns)
        self.assertEqual(policy.worker_deadline_real_life_s, 30)
        self.assertEqual(policy.period_working_s, 30)
        self.assertEqual(policy.max_request_bytes, 4_194_304)


if __name__ == "__main__":
    unittest.main()
