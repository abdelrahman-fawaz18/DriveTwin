"""City Page Weather validation uses explicit units, issuance, and coverage."""

import sys
import unittest
from pathlib import Path

from evdt.energy.route_aware.contracts import Conditions, Origin
from evdt.energy.route_aware.environment import TimeMapping
from evdt.energy.route_aware.wire import fingerprint

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/route_aware_energy"))
from prepare_weather import conditions_for_route, utc, weather_hours  # noqa: E402


def sample():
    return {
        "type": "Feature",
        "id": "qc-147",
        "properties": {
            "lastUpdated": "2026-11-12T19:00:56Z",
            "hourlyForecastGroup": {
                "timestamp": {"en": "2026-11-12T16:30:00Z"},
                "hourlyForecasts": [
                    {
                        "timestamp": f"2026-11-12T{hour}:00:00Z",
                        "temperature": {"units": {"en": "C"}, "value": {"en": 4}},
                        "wind": {
                            "speed": {"units": {"en": "km/h"}, "value": {"en": 18}},
                            "direction": {"value": {"en": "NE"}},
                        },
                    }
                    for hour in (20, 21)
                ],
            },
        },
    }


class WeatherProviderTests(unittest.TestCase):
    def test_city_forecast_preserves_coverage_and_falls_back_for_missing_pressure(self):
        now = utc("2026-11-12T19:50:00Z")
        origin = Origin("forecast", "ECCC", "Sample weather")
        _, hours = weather_hours(sample(), "qc-147", now)
        mapping = TimeMapping(0, utc("2026-11-12T20:00:00Z"), 1)
        base = Conditions(origin, now, 0, 7200, auxiliary_power_w=8000)
        projected = conditions_for_route((base,), hours, mapping, origin, now)
        self.assertEqual([row.from_working_s for row in projected], [0, 3600])
        self.assertEqual(projected[-1].until_working_s, 7200)
        self.assertIsNone(projected[0].pressure_pa)
        self.assertEqual(projected[0].auxiliary_power_w, 8000)
        self.assertIn(fingerprint(base), projected[0].origin.reference)
        self.assertAlmostEqual(projected[0].wind_east_m_s, -3.535534, places=6)
        self.assertAlmostEqual(projected[0].wind_north_m_s, -3.535534, places=6)
        with self.assertRaisesRegex(ValueError, "does not cover"):
            conditions_for_route((base,), hours[1:], mapping, origin, now)

    def test_rejects_stale_issued_forecast_and_discontinuous_hours(self):
        with self.assertRaisesRegex(ValueError, "outside the allowed age"):
            weather_hours(sample(), "qc-147", utc("2026-11-13T01:00:00Z"))
        broken = sample()
        broken["properties"]["hourlyForecastGroup"]["hourlyForecasts"][1]["timestamp"] = (
            "2026-11-12T22:00:00Z"
        )
        with self.assertRaisesRegex(ValueError, "contiguous"):
            weather_hours(broken, "qc-147", utc("2026-11-12T19:50:00Z"))
