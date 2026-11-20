"""Trusted snapshot projection, assignment boundaries, and scoped API access."""

import json
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from test_energy_route_admission import inputs, rebind

from evdt.energy.route_aware.admission import InputUnavailable
from evdt.energy.route_aware.assignment import Assignment
from evdt.energy.route_aware.forecast import forecast
from evdt.energy.route_aware.product import prepare_input
from evdt.energy.route_aware.residuals import Residual
from evdt.energy.route_aware.scenarios import RoadConditions, RouteScenario
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint
from evdt.monitoring.api import (
    route_energy_forecast_record,
    route_energy_forecasts,
    route_energy_input,
)
from evdt.monitoring.route_energy_views import latest_assignment, mismatch_summary, provision

RUN = "00000000-0000-0000-0000-000000000001"


def product_case():
    args = inputs()
    scope = replace(args["scope"], run_id=RUN)
    args["scope"] = scope
    args["clock"] = replace(args["clock"], scope=scope)
    args["packet"] = replace(args["packet"], payload=dict(args["packet"].payload, run_id=RUN))
    rebind(args, replace(args["context"], scope=scope))
    assignment = Assignment(
        "route-energy-assignment/2.0.0",
        RUN,
        scope.asset_id,
        scope.source_id,
        1,
        True,
        args["approved_network_sha256"],
        "d" * 64,
        args["vehicle"],
        args["conditions"],
        args["policy"],
    )
    snapshot = {
        "version": "trusted-route-energy-input/2.0.0",
        "run_id": RUN,
        "asset_id": scope.asset_id,
        "as_of_real_life_s": args["now_real_life_s"],
        "assignment": asdict(assignment),
        "assignment_sha256": fingerprint(assignment),
        "clock": asdict(args["clock"]),
        "packet": asdict(args["packet"]),
        "context": asdict(args["context"]),
        "reason": None,
    }
    return args, assignment, json.loads(json.dumps(snapshot))


class ProductTests(unittest.TestCase):
    def test_infeasible_display_obeys_the_same_source_and_age_gates(self):
        args, assignment, snapshot = product_case()
        _, job = prepare_input(
            snapshot,
            run_id=RUN,
            asset_id=assignment.asset_id,
            prepare_route=lambda a, c: args["prepared"],
        )
        job = replace(job, state=replace(job.state, soc=0.00001))
        payload = json.loads(canonical_bytes(forecast(job)))
        self.assertEqual(payload["status"], "infeasible")
        saved = {
            "fence": 1,
            "classification": "current",
            "payload": payload,
            "assignment_sha256": fingerprint(assignment),
            "route_revision": job.state.route_revision,
        }
        for kind in ("fresh", "stale", "route", "historical", "age"):
            current = json.loads(json.dumps(snapshot))
            row = json.loads(json.dumps(saved))
            if kind == "stale":
                current["reason"] = "source_stale_or_future"
            elif kind == "route":
                row["route_revision"] = "f" * 64
            elif kind == "historical":
                row["classification"] = "historical"
            elif kind == "age":
                current["as_of_real_life_s"] += 1000
            with (
                self.subTest(kind=kind),
                patch.dict("os.environ", {"EVDT_RUN_ID": RUN}),
                patch("evdt.monitoring.route_energy_views.collect", return_value=current),
                patch("evdt.monitoring.route_energy_views.connection") as connection,
            ):
                cursor = connection.return_value.__enter__.return_value.execute.return_value
                cursor.fetchone.return_value = None
                cursor.fetchall.side_effect = [[], [row]]
                view = route_energy_forecasts(assignment.asset_id, None, 20, [assignment.asset_id])
                self.assertIsNone(view["current"])
                self.assertEqual(view["diagnostic"] is not None, kind == "fresh")

    def test_forecast_view_reads_fresh_database_snapshot_before_and_after_result(self):
        args, assignment, snapshot = product_case()
        _, job = prepare_input(
            snapshot,
            run_id=RUN,
            asset_id=assignment.asset_id,
            prepare_route=lambda a, c: args["prepared"],
        )
        payload = json.loads(canonical_bytes(forecast(job)))
        self.assertEqual(payload["status"], "available")
        now = datetime.fromtimestamp(args["now_real_life_s"], timezone.utc)
        observed = datetime.fromtimestamp(args["clock"].observed_real_life_s, timezone.utc)
        received = datetime.fromtimestamp(args["clock"].received_real_life_s, timezone.utc)
        clock_row = {
            "source_epoch": args["scope"].source_epoch,
            "session_id": args["scope"].session_id,
            "state": "running",
            "sim_time_s": args["clock"].working_s,
            "observed_at": observed,
            "received_at": received,
            "manifest_sha256": args["clock"].manifest_sha256,
        }
        observation_row = {
            "payload": args["packet"].payload,
            "wire_sha256": args["packet"].wire_sha256,
            "observed_at": observed,
            "received_at": received,
            "raw_context": canonical_bytes(args["context"]),
        }
        saved = {
            "fence": 1,
            "classification": "current",
            "assignment_sha256": fingerprint(assignment),
            "route_revision": job.state.route_revision,
            "payload": payload,
        }
        for rows in ([], [saved]):
            with (
                self.subTest(saved=bool(rows)),
                patch.dict("os.environ", {"EVDT_RUN_ID": RUN}),
                patch("evdt.monitoring.route_energy_views.connection") as connection,
            ):
                cursor = connection.return_value.__enter__.return_value.execute.return_value
                cursor.fetchone.side_effect = [
                    {"now": now},
                    {
                        "assignment": json.loads(canonical_bytes(assignment)),
                        "assignment_sha256": fingerprint(assignment),
                    },
                    clock_row,
                    {"raw_context": canonical_bytes(args["context"])},
                    None,
                ]
                cursor.fetchall.side_effect = [[observation_row], [], rows]
                view = route_energy_forecasts(assignment.asset_id, None, 20, [assignment.asset_id])
                self.assertEqual(view["comparison_status"]["status"], "insufficient_data")
                self.assertEqual(view["reason"], None if rows else "forecast_pending")
                self.assertEqual(view["current"], payload if rows else None)

    def test_persistent_mismatch_excludes_overlaps_and_other_assignments(self):
        args, assignment, snapshot = product_case()
        records = []
        for start in (0, 30, 60):
            residual = Residual(
                "a" * 64,
                args["scope"],
                args["context"].plan.revision,
                start,
                start + 30,
                200,
                "b" * 64,
                1_000_000,
                1,
                999_999,
            )
            records.append(
                {
                    "assignment_sha256": fingerprint(assignment),
                    "payload": json.loads(canonical_bytes(residual)),
                }
            )
        summary = mismatch_summary(records, snapshot)
        self.assertEqual(summary["status"], "persistent_mismatch")
        self.assertEqual(summary["intervals"], 3)
        self.assertEqual(mismatch_summary([records[0]] * 3, snapshot)["intervals"], 1)
        records[0]["assignment_sha256"] = "c" * 64
        self.assertEqual(mismatch_summary(records, snapshot)["status"], "insufficient_data")

    def test_assigned_trusted_snapshot_produces_bound_request(self):
        args, assignment, snapshot = product_case()
        provider = MagicMock(return_value=args["prepared"])
        actual, job = prepare_input(
            snapshot, run_id=RUN, asset_id=args["scope"].asset_id, prepare_route=provider
        )
        self.assertEqual(actual, assignment)
        self.assertEqual(job.assignment_sha256, fingerprint(assignment))
        self.assertEqual(job.state.scope, args["scope"])
        self.assertLess(len(canonical_bytes(job)), job.policy.max_request_bytes)
        provider.assert_called_once()

    def test_wrong_scope_stale_and_tampered_assignment_rejected(self):
        args, _, snapshot = product_case()
        for changes in (
            {"run_id": "different"},
            {"asset_id": "other"},
            {"assignment_sha256": "a" * 64},
            {"reason": "source_paused"},
            {"version": "old-version"},
        ):
            with self.subTest(changes=changes), self.assertRaises((InputUnavailable, ValueError)):
                prepare_input(
                    snapshot | changes,
                    run_id=RUN,
                    asset_id=args["scope"].asset_id,
                    prepare_route=lambda a, c: args["prepared"],
                )
        snapshot["clock"]["scope"]["source_id"] = "unassigned"
        with self.assertRaises(InputUnavailable):
            prepare_input(
                snapshot,
                run_id=RUN,
                asset_id=args["scope"].asset_id,
                prepare_route=lambda a, c: args["prepared"],
            )

    def test_assignment_provision_is_idempotent_and_revision_ordered(self):
        _, assignment, _ = product_case()
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        with patch.dict("os.environ", {"EVDT_RUN_ID": RUN}):
            self.assertTrue(provision(conn, assignment))
            conn.execute.return_value.fetchone.return_value = {
                "revision": 1,
                "assignment_sha256": fingerprint(assignment),
            }
            self.assertFalse(provision(conn, assignment))
            with self.assertRaises(ValueError):
                provision(conn, replace(assignment, revision=3))
            with self.assertRaises(ValueError):
                provision(conn, replace(assignment, enabled=False))

    def test_local_conditions_cannot_move_to_a_different_route(self):
        args, assignment, snapshot = product_case()
        scenario = RouteScenario(
            "road-weather",
            assignment.conditions[0].origin,
            0,
            roads=(RoadConditions(0, assignment.conditions),),
        )
        with self.assertRaisesRegex(ValueError, "bound"):
            replace(assignment, scenario=scenario)
        revised = replace(assignment, scenario=scenario, scenario_route_revision="0" * 64)
        snapshot["assignment"] = json.loads(canonical_bytes(revised))
        snapshot["assignment_sha256"] = fingerprint(revised)
        provider = MagicMock()
        with self.assertRaisesRegex(InputUnavailable, "updated_route_assignment"):
            prepare_input(
                snapshot, run_id=RUN, asset_id=args["scope"].asset_id, prepare_route=provider
            )
        provider.assert_not_called()

    def test_latest_assignment_checks_integrity(self):
        _, assignment, _ = product_case()
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "assignment_sha256": "0" * 64,
            "assignment": json.loads(canonical_bytes(assignment)),
        }
        with self.assertRaises(ValueError):
            latest_assignment(conn, RUN, assignment.asset_id)

    def test_all_new_api_reads_remain_asset_scoped(self):
        with (
            patch("evdt.monitoring.api.route_energy_views.input_view") as input_read,
            patch("evdt.monitoring.api.route_energy_views.read") as result_read,
        ):
            for callback in (
                lambda: route_energy_input("other", ["bus"]),
                lambda: route_energy_forecasts("other", None, 20, ["bus"]),
                lambda: route_energy_forecast_record(1, "other", ["bus"]),
            ):
                with self.assertRaises(HTTPException) as caught:
                    callback()
                self.assertEqual(caught.exception.status_code, 403)
            input_read.assert_not_called()
            result_read.assert_not_called()
            route_energy_input("bus", ["bus"])
            route_energy_forecasts("bus", None, 20, ["bus"])
            route_energy_forecast_record(1, "bus", ["bus"])
            self.assertEqual(input_read.call_count, 1)
            self.assertEqual(result_read.call_count, 2)
