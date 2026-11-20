"""Input minimization, identity and malformed-request checks."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from test_energy_route_contracts import occurrence, vehicle

from evdt.energy.route_aware import CONTRACT_VERSION
from evdt.energy.route_aware.contracts import (
    Conditions,
    LiveState,
    RouteContext,
    SourceScope,
    VehicleProfile,
)
from evdt.energy.route_aware.forecast import forecast, parse_result, validate_result
from evdt.energy.route_aware.policy import ForecastPolicy
from evdt.energy.route_aware.wire import (
    MAX_BYTES,
    ForecastRequest,
    canonical_bytes,
    decode_record,
    fingerprint,
    parse_request,
)


def request() -> ForecastRequest:
    return ForecastRequest(
        CONTRACT_VERSION,
        1,
        100,
        "qualification",
        LiveState(
            SourceScope("run", "bus", "adapter", 1, "session"),
            "event",
            "a" * 64,
            30,
            99,
            100,
            "running",
            "route-1",
            0,
            10,
            0.8,
            5,
            8_000,
            "not_charging",
        ),
        RouteContext(
            "b" * 64,
            "route-1",
            "local-east-north-meters",
            "synthetic-height",
            vehicle().origin,
            (occurrence(),),
        ),
        vehicle(),
        (Conditions(vehicle().origin, 90, 0, 3_600),),
        ForecastPolicy(),
    )


class WireTests(unittest.TestCase):
    def test_development_profile_decodes_without_route_specific_data(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "qualification/energy/route-aware/vehicle-profile.json"
        )
        profile = decode_record(VehicleProfile, json.loads(path.read_bytes()))
        self.assertEqual(profile.origin.kind, "declared")
        self.assertEqual(profile.capacity_kwh, 350)

    def test_roundtrip_keeps_typed_immutable_records(self):
        original = request()
        decoded = parse_request(canonical_bytes(original))
        self.assertEqual(decoded, original)
        self.assertEqual(fingerprint(decoded), fingerprint(original))
        self.assertIsInstance(decoded.route.occurrences, tuple)

    def test_numeric_encoding_is_normalized(self):
        first = request()
        second = replace(first, state=replace(first.state, speed_m_s=5.0))
        self.assertEqual(fingerprint(first), fingerprint(second))

    def test_sumo_position_policy_keeps_legacy_identity_and_binds_worker_version(self):
        original = request()
        raw = canonical_bytes(original)
        legacy = json.loads(raw)
        self.assertNotIn("sumo_euler_step_working_s", legacy["policy"])
        legacy["policy"]["sumo_euler_step_working_s"] = None
        self.assertEqual(canonical_bytes(parse_request(json.dumps(legacy).encode())), raw)
        configured = replace(
            original, policy=replace(original.policy, sumo_euler_step_working_s=0.2)
        )
        self.assertEqual(parse_request(canonical_bytes(configured)), configured)
        self.assertNotEqual(fingerprint(original), fingerprint(configured))
        old = parse_result(canonical_bytes(forecast(original)))
        current = parse_result(canonical_bytes(forecast(configured)))
        self.assertNotEqual(old.model_version, current.model_version)
        validate_result(original, old)
        validate_result(configured, current)
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_result(configured, replace(current, model_version=old.model_version))

    def test_revised_route_changes_identity(self):
        first = request()
        second = replace(
            first,
            state=replace(first.state, route_revision="route-2"),
            route=replace(first.route, revision="route-2"),
        )
        self.assertNotEqual(fingerprint(first), fingerprint(second))

    def test_route_generation_cannot_be_mixed(self):
        with self.assertRaises(ValueError):
            replace(request(), route=replace(request().route, revision="other"))

    def test_future_unavailable_conditions_are_rejected(self):
        with self.assertRaises(ValueError):
            replace(
                request(), conditions=(replace(request().conditions[0], available_real_life_s=101),)
            )

    def test_unknown_and_missing_fields_are_rejected_at_every_depth(self):
        for path in ((), ("state",), ("vehicle",), ("route",), ("policy",)):
            for operation in ("add", "remove"):
                with self.subTest(path=path, operation=operation):
                    value = json.loads(canonical_bytes(request()))
                    target = value
                    for key in path:
                        target = target[key]
                    if operation == "add":
                        target["hidden_future_energy"] = 42
                    else:
                        target.pop(next(iter(target)))
                    with self.assertRaises(ValueError):
                        parse_request(json.dumps(value).encode())

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        raw = canonical_bytes(request())
        samples = (
            raw.replace(b'"generation":1', b'"generation":1,"generation":2'),
            raw.replace(b'"soc":0.8', b'"soc":NaN'),
            raw.replace(b'"soc":0.8', b'"soc":1e999'),
            raw.replace(b'"generation":1', b'"generation":true'),
            raw.replace(b'"soc":0.8', b'"soc":"0.8"'),
            b"\xff",
            b"[" * 2_000 + b"]" * 2_000,
        )
        for raw_case in samples:
            with self.subTest(length=len(raw_case)), self.assertRaises(ValueError):
                parse_request(raw_case)

    def test_request_size_limits_are_enforced(self):
        for raw in (b"", b" " * (MAX_BYTES + 1)):
            with self.assertRaises(ValueError):
                parse_request(raw)
        small_policy = replace(request(), policy=replace(ForecastPolicy(), max_request_bytes=20))
        with self.assertRaises(ValueError):
            parse_request(canonical_bytes(small_policy))

    def test_conditions_must_be_nonoverlapping_and_mass_admissible(self):
        base = request()
        for conditions in (
            (base.conditions[0], base.conditions[0]),
            (replace(base.conditions[0], loaded_mass_kg=30_000),),
        ):
            with self.assertRaises(ValueError):
                replace(base, conditions=conditions)


if __name__ == "__main__":
    unittest.main()
