"""Forecast admission across independently delivered clock and scalar records."""

import json
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from test_energy_route_admission import inputs
from test_energy_route_product import RUN, product_case

from evdt.energy.route_aware.admission import InputUnavailable, build_request
from evdt.energy.route_aware.product import prepare_input
from evdt.energy.route_aware.wire import canonical_bytes
from evdt.monitoring.route_energy_views import collect


class DeliveryTests(unittest.TestCase):
    def test_current_head_permits_bounded_complete_observation_without_relabeling_cutoff(self):
        for lag in (0, 1, 5, 20, 30):
            args = inputs()
            args["clock"] = replace(args["clock"], working_s=30 + lag)
            args["current_context"] = replace(args["context"], working_s=30 + lag)
            with self.subTest(lag=lag):
                job = build_request(**args)
                self.assertEqual(job.state.working_s, 30)
                self.assertEqual(job.state.wire_sha256, args["packet"].wire_sha256)

    def test_head_proof_cannot_bypass_identity_route_charging_or_freshness(self):
        base = inputs()["context"]
        head = replace(base, working_s=35)
        variants = (
            (None, "context_not_current"),
            (replace(head, scope=replace(head.scope, source_epoch=2)), "source_scope"),
            (replace(head, scope=replace(head.scope, session_id="another")), "source_scope"),
            (replace(head, manifest_sha256="c" * 64), "registration_mismatch"),
            (replace(head, working_s=34), "context_not_current"),
            (replace(head, observed_real_life_s=94.9), "stale_or_future"),
            (replace(head, observed_real_life_s=100.1), "stale_or_future"),
            (replace(head, plan=replace(head.plan, remaining_stops=())), "route_plan_changed"),
            (replace(head, plan=replace(head.plan, network_sha256="e" * 64)), "route_plan_changed"),
            (replace(head, charging_state="charging"), "charging_charging"),
            (replace(head, charging_state="unknown"), "charging_unknown"),
        )
        for context, reason in variants:
            args = inputs()
            args["clock"] = replace(args["clock"], working_s=35)
            args["current_context"] = context
            with self.subTest(reason=reason), self.assertRaisesRegex(InputUnavailable, reason):
                build_request(**args)
        for lag in (-1, 30.001):
            args = inputs()
            args["clock"] = replace(args["clock"], working_s=30 + lag)
            args["current_context"] = replace(base, working_s=30 + lag)
            with self.assertRaisesRegex(InputUnavailable, "working_lag_exceeded"):
                build_request(**args)
        for state in ("paused", "finished", "disconnected"):
            args = inputs()
            args["clock"] = replace(args["clock"], state=state, working_s=35)
            args["current_context"] = head
            with self.assertRaisesRegex(InputUnavailable, "source_" + state):
                build_request(**args)
        args = inputs()
        args["clock"] = replace(args["clock"], working_s=35)
        args["current_context"] = head
        args["packet"] = replace(args["packet"], observed_real_life_s=94.9)
        with self.assertRaisesRegex(InputUnavailable, "stale_or_future"):
            build_request(**args)

    def test_new_input_version_requires_head_proof_and_preserves_old_strict_behavior(self):
        args, assignment, snapshot = product_case()
        snapshot["clock"]["working_s"] += 5

        def prepare(value):
            return prepare_input(
                value,
                run_id=RUN,
                asset_id=assignment.asset_id,
                prepare_route=lambda a, c: args["prepared"],
            )

        with self.assertRaisesRegex(InputUnavailable, "context_not_current"):
            prepare(snapshot)
        snapshot["version"] = "trusted-route-energy-input/2.1.0"
        with self.assertRaisesRegex(InputUnavailable, "unsupported_trusted_input"):
            prepare(snapshot)
        snapshot["current_context"] = asdict(replace(args["context"], working_s=35))
        _, job = prepare(json.loads(json.dumps(snapshot)))
        self.assertEqual(job.state.working_s, 30)

    def test_collection_uses_complete_record_and_actual_head_then_rejects_changes(self):
        args, assignment, _ = product_case()

        def timestamp(value):
            return datetime.fromtimestamp(value, timezone.utc)

        clock = {
            "source_epoch": args["scope"].source_epoch,
            "session_id": args["scope"].session_id,
            "state": "running",
            "sim_time_s": 35,
            "observed_at": timestamp(99),
            "received_at": timestamp(99.5),
            "manifest_sha256": args["clock"].manifest_sha256,
        }
        observation = {
            "payload": args["packet"].payload,
            "wire_sha256": args["packet"].wire_sha256,
            "observed_at": timestamp(99),
            "received_at": timestamp(99.5),
            "raw_context": canonical_bytes(args["context"]),
        }
        for case in ("delivery_lag", "route_changed", "charging", "missing_head", "duplicate"):
            head = replace(args["context"], working_s=35)
            if case == "route_changed":
                head = replace(head, plan=replace(head.plan, remaining_stops=()))
            elif case == "charging":
                head = replace(head, charging_state="charging")
            conn = MagicMock()
            cursor = conn.execute.return_value
            cursor.fetchone.side_effect = [
                {"now": timestamp(100)},
                clock,
                None if case == "missing_head" else {"raw_context": canonical_bytes(head)},
            ]
            cursor.fetchall.return_value = [observation] * (2 if case == "duplicate" else 1)
            with (
                self.subTest(case=case),
                patch(
                    "evdt.monitoring.route_energy_views.latest_assignment", return_value=assignment
                ),
            ):
                result = collect(conn, RUN, assignment.asset_id)
                if case == "delivery_lag":
                    self.assertIsNone(result["reason"])
                    self.assertEqual(result["clock"]["working_s"], 35)
                    self.assertEqual(result["packet"]["payload"]["sim_time_s"], 30)
                    _, job = prepare_input(
                        result,
                        run_id=RUN,
                        asset_id=assignment.asset_id,
                        prepare_route=lambda a, c: args["prepared"],
                    )
                    self.assertEqual(job.state.working_s, 30)
                else:
                    self.assertIsNotNone(result["reason"])


if __name__ == "__main__":
    unittest.main()
