"""The finite-run accounting guard rejects lost, altered, or incomplete history."""

import copy
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/geographic"))
from live_validation import reconcile, validate_current_forecast  # noqa: E402

from evdt.energy.route_aware.policy import ForecastPolicy


class ForecastFreshnessTests(unittest.TestCase):
    def test_configured_limits_replace_historical_runner_limits(self):
        policy = replace(
            ForecastPolicy(),
            period_working_s=120,
            forecast_max_age_working_s=150,
            forecast_max_age_real_life_s=270,
        )
        view = {
            "current": {"status": "available"},
            "status": "available",
            "working_age_s": 61,
            "real_life_age_s": 30.5,
        }
        validate_current_forecast(view, policy)
        validate_current_forecast({**view, "working_age_s": 120, "real_life_age_s": 240}, policy)
        for changes in (
            {"working_age_s": 151},
            {"real_life_age_s": 271},
            {"status": "unavailable"},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, "freshness"):
                validate_current_forecast({**view, **changes}, policy)
        validate_current_forecast({**view, "current": None, "status": "unavailable"}, policy)


class RecordingTests(unittest.TestCase):
    def test_missing_packets_modified_fingerprints_and_signal_gaps_fail(self):
        events = [
            {
                "event_id": str(i),
                "wire_sha256": str(i),
                "body": {"sim_time_s": i, "samples": [{"signal_id": str(s)} for s in range(20)]},
            }
            for i in range(1, 4)
        ]
        rows = [(str(i), str(i)) for i in range(1, 4)]
        counts = [(str(i), 3) for i in range(20)]
        self.assertEqual(reconcile(events, rows, counts)["readings"], 60)
        for bad_rows, bad_counts in (
            (rows[:2], counts),
            (rows + [rows[0]], counts),
            ([("1", "altered"), *rows[1:]], counts),
            (rows, counts[:19]),
        ):
            with self.assertRaises(ValueError):
                reconcile(events, bad_rows, bad_counts)
        changed = copy.deepcopy(events)
        changed[1]["body"]["sim_time_s"] = 4
        with self.assertRaises(ValueError):
            reconcile(changed, rows, counts)
