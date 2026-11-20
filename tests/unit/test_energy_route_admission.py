"""Current-source admission without a simulator, browser, or database process."""

import json
import unittest
from dataclasses import asdict, replace

from test_energy_route_wire import request

from evdt.energy.route_aware.admission import (
    AcceptedPacket,
    InputUnavailable,
    PreparedPlan,
    TrustedClock,
    build_request,
)
from evdt.energy.route_aware.context import SourceContext, SourcePlan, SourceStop, parse_context
from evdt.energy.route_aware.wire import canonical_bytes, fingerprint


def inputs():
    job = request()
    stop = SourceStop("stop", 0, "lane-0", 80, 20)
    plan = SourcePlan(job.route.network_sha256, ("revisited-street",), (stop,))
    route = replace(job.route, revision=plan.revision)
    context = SourceContext(
        "source-route-context/2.0.0",
        job.state.scope,
        "d" * 64,
        30,
        99,
        plan,
        0,
        "lane-0",
        10,
        "not_charging",
        None,
        None,
    )
    packet = {
        **asdict(job.state.scope),
        "schema_version": "3.0.0",
        "mode": "simulation",
        "clock_domain": "simulation",
        "event_id": "event",
        "sim_time_s": 30,
        "manifest_sha256": "d" * 64,
        "annotations": {"energy_context": fingerprint(context)},
        "samples": [
            {
                "signal_id": name,
                "unit": unit,
                "value": value,
                "quality": "good",
                "provenance": "simulated-observation",
            }
            for name, unit, value in (
                ("battery.pack.soc", "1", 0.8),
                ("motion.speed", "m/s", 0),
                ("auxiliary.power", "W", 8_000),
                ("battery.pack.temperature", "K", 298.15),
            )
        ],
    }
    return {
        "scope": job.state.scope,
        "clock": TrustedClock(job.state.scope, "running", 30, 99, 99.5, "d" * 64),
        "packet": AcceptedPacket(packet, "a" * 64, 99, 99.5),
        "context": context,
        "prepared": PreparedPlan(plan.revision, route, (0,)),
        "approved_network_sha256": route.network_sha256,
        "vehicle": job.vehicle,
        "conditions": job.conditions,
        "policy": job.policy,
        "now_real_life_s": 100,
        "generation": 1,
        "trigger": "first_ready",
    }


def rebind(args, context):
    args["context"] = context
    payload = dict(args["packet"].payload)
    payload["annotations"] = {"energy_context": fingerprint(context)}
    args["packet"] = replace(args["packet"], payload=payload)


class AdmissionTests(unittest.TestCase):
    def test_live_period_does_not_widen_observation_lag(self):
        args = inputs()
        args["policy"] = replace(
            args["policy"], period_working_s=60, max_observation_lag_working_s=30
        )
        args["clock"] = replace(args["clock"], working_s=61)
        args["current_context"] = replace(args["context"], working_s=61)
        with self.assertRaisesRegex(InputUnavailable, "observation_working_lag_exceeded"):
            build_request(**args)

    def test_scenario_suffix_keeps_future_road_identity_and_rejects_missing_turn(self):
        from evdt.energy.route_aware.scenarios import RoadConditions, RouteScenario

        args = inputs()
        original = args["prepared"]
        prepared = replace(original, first_external_index=1, scenario_indices=(2,))
        scenario = RouteScenario(
            "roads",
            args["conditions"][0].origin,
            90,
            roads=(RoadConditions(0, args["conditions"]), RoadConditions(2, args["conditions"])),
            closed_occurrences=(2,),
        )
        projected = prepared.project_scenario(scenario)
        self.assertEqual([r.occurrence_index for r in projected.roads], [0])
        self.assertEqual(projected.closed_occurrences, (0,))
        with self.assertRaisesRegex(InputUnavailable, "scenario_changed_lane_path_requires_update"):
            prepared.project_scenario(replace(scenario, closed_occurrences=(3,)))

    def test_suffix_admission_preserves_authenticated_context(self):
        args = inputs()
        original = args["context"].plan
        plan = replace(
            original,
            edges=("already-traveled", *original.edges),
            remaining_stops=tuple(
                replace(s, external_index=s.external_index + 1) for s in original.remaining_stops
            ),
        )
        rebind(args, replace(args["context"], plan=plan, external_index=1))
        prepared = args["prepared"]
        args["prepared"] = replace(
            prepared,
            plan_sha256=plan.revision,
            route=replace(prepared.route, revision=plan.revision),
            first_external_index=1,
        )
        result = build_request(**args)
        self.assertEqual(result.state.occurrence_index, 0)
        self.assertEqual(result.state.route_revision, plan.revision)
        self.assertEqual(result.state.wire_sha256, args["packet"].wire_sha256)
        rebind(args, replace(args["context"], external_index=0))
        with self.assertRaisesRegex(InputUnavailable, "position_outside_prepared_suffix"):
            build_request(**args)

    def test_first_ready_at_30_and_zero_speed_need_no_history(self):
        result = build_request(**inputs())
        self.assertEqual(result.state.working_s, 30)
        self.assertEqual(result.state.speed_m_s, 0)
        self.assertEqual(result.state.wire_sha256, "a" * 64)

    def test_context_roundtrip_and_strict_fields(self):
        context = inputs()["context"]
        self.assertEqual(parse_context(canonical_bytes(context)), context)
        value = json.loads(canonical_bytes(context))
        value["trusted"] = True
        with self.assertRaises(ValueError):
            parse_context(json.dumps(value).encode())
        for raw in (b" " * 65_537, b'{"version":1,"version":2}', b'{"value":NaN}'):
            with self.subTest(raw=raw[:50]), self.assertRaises(ValueError):
                parse_context(raw)

    def test_wrong_asset_epoch_session_source_and_run_rejected(self):
        for field, value in (
            ("asset_id", "other"),
            ("source_epoch", 2),
            ("session_id", "other"),
            ("source_id", "other"),
            ("run_id", "other"),
        ):
            for location in ("clock", "context", "packet"):
                with self.subTest(field=field, location=location):
                    args = inputs()
                    if location == "packet":
                        payload = dict(args["packet"].payload)
                        payload[field] = value
                        args["packet"] = replace(args["packet"], payload=payload)
                    else:
                        args[location] = replace(
                            args[location], scope=replace(args["scope"], **{field: value})
                        )
                    with self.assertRaisesRegex(InputUnavailable, "source_scope_mismatch"):
                        build_request(**args)

    def test_pause_finish_stale_future_and_charging_suppress_guidance(self):
        for state in ("paused", "finished"):
            args = inputs()
            args["clock"] = replace(args["clock"], state=state)
            with self.assertRaisesRegex(InputUnavailable, "source_" + state):
                build_request(**args)
        for stamp in (94.9, 100.051):
            for location in ("clock", "packet", "context"):
                args = inputs()
                args[location] = replace(args[location], observed_real_life_s=stamp)
                with (
                    self.subTest(stamp=stamp, location=location),
                    self.assertRaisesRegex(InputUnavailable, "required_input_stale_or_future"),
                ):
                    build_request(**args)
        for state in ("charging", "unknown"):
            args = inputs()
            rebind(args, replace(args["context"], charging_state=state))
            with self.assertRaisesRegex(InputUnavailable, "charging_" + state):
                build_request(**args)

    def test_replay_manifest_context_binding_and_working_time(self):
        for key, value, reason in (
            ("mode", "replay", "live_source_required"),
            ("clock_domain", "replay", "live_source_required"),
            ("manifest_sha256", "f" * 64, "registration_mismatch"),
            ("annotations", {"energy_context": "e" * 64}, "context_wire_binding"),
            ("sim_time_s", 29, "context_working_time_mismatch"),
        ):
            args = inputs()
            payload = {**args["packet"].payload, key: value}
            args["packet"] = replace(args["packet"], payload=payload)
            with self.subTest(key=key), self.assertRaisesRegex(InputUnavailable, reason):
                build_request(**args)

    def test_invalid_signal_unit_quality_provenance_and_value(self):
        for key, value in (
            ("unit", "%"),
            ("quality", "invalid"),
            ("provenance", "forecast"),
            ("value", float("nan")),
            ("value", True),
        ):
            args = inputs()
            args["packet"].payload["samples"][0][key] = value
            with (
                self.subTest(key=key, value=value),
                self.assertRaisesRegex(InputUnavailable, "invalid_battery.pack.soc"),
            ):
                build_request(**args)

    def test_unprepared_lane_and_obsolete_route_rejected(self):
        args = inputs()
        rebind(args, replace(args["context"], lane_id="other"))
        with self.assertRaisesRegex(InputUnavailable, "ambiguous_or_unprepared_lane"):
            build_request(**args)
        args = inputs()
        changed = replace(args["context"].plan, remaining_stops=())
        rebind(args, replace(args["context"], plan=changed))
        with self.assertRaisesRegex(InputUnavailable, "route_revision_mismatch"):
            build_request(**args)

    def test_clock_advancement_is_not_confused_with_real_life_age(self):
        args = inputs()
        args["clock"] = replace(args["clock"], working_s=31)
        with self.assertRaisesRegex(InputUnavailable, "context_not_current"):
            build_request(**args)

    def test_current_stop_retains_observed_remaining_dwell(self):
        args = inputs()
        rebind(
            args,
            replace(
                args["context"],
                lane_offset_m=80,
                current_stop_id="stop",
                remaining_dwell_working_s=7,
            ),
        )
        result = build_request(**args)
        self.assertEqual(result.state.remaining_dwell_working_s, 7)
        self.assertEqual(result.state.current_stop.stop_id, "stop")

    def test_future_condition_and_unassigned_network_rejected(self):
        args = inputs()
        args["conditions"] = (replace(args["conditions"][0], available_real_life_s=101),)
        with self.assertRaisesRegex(InputUnavailable, "invalid_trusted_input"):
            build_request(**args)
        args = inputs()
        args["approved_network_sha256"] = "f" * 64
        with self.assertRaisesRegex(InputUnavailable, "unassigned_network"):
            build_request(**args)

    def test_stop_plan_and_reference_offset_cannot_silently_change(self):
        args = inputs()
        route = args["prepared"].route
        args["prepared"] = replace(
            args["prepared"],
            route=replace(route, occurrences=(replace(route.occurrences[0], stops=()),)),
        )
        with self.assertRaisesRegex(InputUnavailable, "route_stop_plan_mismatch"):
            build_request(**args)
        args = inputs()
        rebind(args, replace(args["context"], lane_offset_m=101))
        with self.assertRaisesRegex(InputUnavailable, "position_outside_lane"):
            build_request(**args)
