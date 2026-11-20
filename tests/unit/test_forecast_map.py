"""Forecast geography preserves input identity and reader permissions."""

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException
from test_energy_route_wire import request

from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.wire import canonical_bytes
from evdt.monitoring.api import route_energy_forecast_map
from evdt.monitoring.forecast_map import project


class ForecastMapTests(unittest.TestCase):
    def input(self):
        job = request()
        roads = tuple(
            replace(
                o,
                shape=tuple(
                    replace(
                        p,
                        longitude_deg=-73.57 + p.x_m / 100000,
                        latitude_deg=45.51 + p.y_m / 100000,
                    )
                    for p in o.shape
                ),
            )
            for o in job.route.occurrences
        )
        return replace(job, route=replace(job.route, occurrences=roads))

    def test_exact_remaining_path_scope_and_observation_time(self):
        job = self.input()
        raw = json.loads(canonical_bytes(job))
        result = json.loads(canonical_bytes(forecast(job)))
        output = project(raw, result, 7, "run", "bus")
        self.assertEqual(output["fence"], 7)
        self.assertEqual(output["input_sha256"], result["input_sha256"])
        self.assertEqual(output["observed_real_life_s"], job.state.observed_real_life_s)
        self.assertEqual(output["cutoff_working_s"], job.state.working_s)
        self.assertEqual(output["context"]["features"][0]["properties"]["layer"], "route")
        self.assertNotIn("request", output)
        with self.assertRaises(ValueError):
            project(raw, result, 7, "run", "other")
        result["input_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            project(raw, result, 7, "run", "bus")

    def test_missing_coordinates_are_not_invented(self):
        job = request()
        with self.assertRaises(ValueError):
            project(
                json.loads(canonical_bytes(job)),
                json.loads(canonical_bytes(forecast(job))),
                1,
                "run",
                "bus",
            )

    def test_unauthorized_asset_and_invalid_fence_never_query_storage(self):
        with patch("evdt.monitoring.route_energy_views.map_view", return_value={}) as read:
            with self.assertRaises(HTTPException) as denied:
                route_energy_forecast_map(1, "bus", {"other"})
            self.assertEqual(denied.exception.status_code, 403)
            with self.assertRaises(HTTPException) as invalid:
                route_energy_forecast_map(0, "bus", {"bus"})
            self.assertEqual(invalid.exception.status_code, 422)
            read.assert_not_called()
            self.assertEqual(route_energy_forecast_map(1, "bus", {"bus"}), {})
            read.assert_called_once_with("bus", 1)
