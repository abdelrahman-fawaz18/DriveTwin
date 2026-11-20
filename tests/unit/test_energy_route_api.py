"""Producer credentials and source scope gate the new metadata endpoint."""

import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi import HTTPException
from test_energy_route_admission import inputs

from evdt.energy.moving_charge_recovery import HeldChargeStop
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint
from evdt.monitoring.registration_api import held_charge_observation, producer, route_context


class Request:
    def __init__(self, raw):
        self.raw = raw

    async def stream(self):
        yield self.raw


class ContextApiTests(unittest.TestCase):
    def setUp(self):
        context = inputs()["context"]
        self.context = replace(
            context, scope=replace(context.scope, run_id="00000000-0000-0000-0000-000000000001")
        )
        self.writer = {
            "run_id": self.context.scope.run_id,
            "asset_id": self.context.scope.asset_id,
            "source_id": self.context.scope.source_id,
            "token": "unit-placeholder",
        }

    def test_reader_or_missing_credential_cannot_register_context(self):
        with (
            patch.dict("os.environ", {"WRITERS_FILE": "unused-test-path"}),
            patch("pathlib.Path.read_text", return_value=json.dumps({"writers": [self.writer]})),
        ):
            for header in (None, "Bearer reader-placeholder", "Bearer replay-placeholder"):
                with self.subTest(header=header), self.assertRaises(HTTPException) as denied:
                    producer(header)
                self.assertEqual(denied.exception.status_code, 401)
            self.assertEqual(producer("Bearer unit-placeholder"), self.writer)

    def test_source_bound_writer_can_publish_only_its_own_context(self):
        async def store(function, raw):
            return {"accepted": True, "context_sha256": fingerprint(self.context)}

        with patch("evdt.monitoring.registration_api.run_in_threadpool", side_effect=store) as call:
            result = asyncio.run(route_context(Request(canonical_bytes(self.context)), self.writer))
            self.assertTrue(result["accepted"])
            self.assertEqual(call.call_count, 1)
            for field in ("asset_id", "source_id"):
                value = replace(self.context, scope=replace(self.context.scope, **{field: "other"}))
                with self.subTest(field=field), self.assertRaises(HTTPException) as denied:
                    asyncio.run(route_context(Request(canonical_bytes(value)), self.writer))
                self.assertEqual(denied.exception.status_code, 403)
            self.assertEqual(call.call_count, 1)

    def test_size_and_invalid_record_do_not_reach_database(self):
        with patch("evdt.monitoring.registration_api.run_in_threadpool") as call:
            for raw, code in ((b"x" * 65_537, 413), (b"{}", 409)):
                with self.subTest(code=code), self.assertRaises(HTTPException) as denied:
                    asyncio.run(route_context(Request(raw), self.writer))
                self.assertEqual(denied.exception.status_code, code)
            call.assert_not_called()

    def test_held_charge_receipt_requires_the_registered_producer_and_exact_bytes(self):
        held = HeldChargeStop(
            self.context.scope,
            "00000000-0000-0000-0000-000000000002",
            self.context.manifest_sha256,
            "a" * 64,
            self.context.plan.revision,
            "b" * 64,
            "bay",
            1,
            "bay_lane",
            36,
            150,
            0.12,
            self.context.observed_real_life_s,
        )
        raw = canonical_bytes(held)
        receipt = {"observation_sha256": fingerprint(held), "duplicate": False}
        with patch(
            "evdt.monitoring.registration_api.run_in_threadpool", return_value=receipt
        ) as call:
            self.assertEqual(
                asyncio.run(held_charge_observation(Request(raw), self.writer)), receipt
            )
            self.assertEqual(call.call_args.args[1], held)
            for data, writer, code in (
                (raw, {**self.writer, "asset_id": "other"}, 403),
                (raw + b" ", self.writer, 409),
                (b"x" * 8193, self.writer, 413),
            ):
                with self.subTest(code=code), self.assertRaises(HTTPException) as denied:
                    asyncio.run(held_charge_observation(Request(data), writer))
                self.assertEqual(denied.exception.status_code, code)
            self.assertEqual(call.call_count, 1)
