"""Reject unsafe command input and keep actuation separate from its outcome."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from evdt.commands import RESULT, OperatorScope, SpeedLimitExecutor, admit
from evdt.contract_profile import Rejected

NOW = datetime(2026, 11, 5, 15, tzinfo=timezone.utc)
SCOPE = OperatorScope("00000000-0000-0000-0000-000000000001", "bus-001", "local-operator")


def wire(**changes):
    return json.dumps(
        {
            "schema_version": "evdt-command/1.0.0",
            "command_id": "00000000-0000-0000-0000-000000000002",
            "run_id": SCOPE.run_id,
            "asset_id": SCOPE.asset_id,
            "mode": "simulation",
            "issuer": SCOPE.issuer,
            "capability": "set_speed_limit",
            "parameters": {"value": 4, "unit": "m/s"},
            "reason": "Qualification speed cap",
            "issued_at": "2026-11-05T15:00:00Z",
            "expires_at": "2026-11-05T15:00:10Z",
            "not_before_sim_s": 4,
            "expires_sim_s": 6,
            **changes,
        }
    ).encode()


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"mode": "replay"}, "replay_mode"),
        ({"capability": "charge"}, "unsupported_capability"),
        ({"issuer": "other-operator"}, "unauthorized_issuer"),
        ({"asset_id": "bus-002"}, "wrong_asset"),
        ({"run_id": "00000000-0000-0000-0000-000000000007"}, "wrong_run"),
        ({"parameters": {"value": 4, "unit": "km/h"}}, "command_schema"),
        ({"parameters": {"value": 5.01, "unit": "m/s"}}, "command_schema"),
        ({"parameters": {"value": True, "unit": "m/s"}}, "command_schema"),
        ({"expires_at": "2026-11-05T15:00:31Z"}, "wall_validity"),
        ({"expires_sim_s": 4}, "simulation_validity"),
    ],
)
def test_unsupported_or_unauthorized_commands_never_reach_actuator(change, reason):
    with pytest.raises(Rejected, match=reason):
        SpeedLimitExecutor(SCOPE).execute(
            wire(**change),
            now=NOW,
            sim_time_s=4,
            current_limit_m_s=12,
            apply_limit=lambda *args: pytest.fail("rejected command reached actuator"),
        )


@pytest.mark.parametrize(
    "raw, reason",
    [
        (b'{"mode":"simulation","mode":"replay"}', "invalid_json"),
        (b" " * 2049, "payload_too_large"),
    ],
)
def test_command_wire_format_fails_closed(raw, reason):
    with pytest.raises(Rejected, match=reason):
        admit(raw, SCOPE, NOW, 4)


def test_both_clocks_must_be_in_their_own_half_open_window():
    assert admit(wire(), SCOPE, NOW, 4)["parameters"]["value"] == 4
    with pytest.raises(Rejected, match="wall_validity"):
        admit(wire(), SCOPE, NOW + timedelta(seconds=10), 4)
    with pytest.raises(Rejected, match="simulation_validity"):
        admit(wire(), SCOPE, NOW, 6)


def test_executor_preserves_lower_local_limit():
    with pytest.raises(Rejected, match="not_a_speed_reduction"):
        SpeedLimitExecutor(SCOPE).execute(
            wire(),
            now=NOW,
            sim_time_s=4,
            current_limit_m_s=3,
            apply_limit=lambda *args: pytest.fail("local speed limit was overridden"),
        )


@pytest.mark.parametrize("failure", [False, True])
def test_duplicate_delivery_in_a_run_does_not_repeat_an_uncertain_or_acknowledged_effect(failure):
    calls = []

    def actuator(asset, speed):
        calls.append((asset, speed))
        if failure:
            raise TimeoutError("simulator reply lost")

    executor = SpeedLimitExecutor(SCOPE)
    args = dict(now=NOW, sim_time_s=4, current_limit_m_s=12, apply_limit=actuator)
    result = executor.execute(wire(), **args)
    assert RESULT.is_valid(result)
    assert result["status"] == ("unknown_outcome" if failure else "acknowledged")
    assert executor.execute(wire(), **args) == result
    with pytest.raises(Rejected, match="command_already_attempted"):
        executor.execute(wire(reason="Conflicting command content"), **args)
    assert calls == [("bus-001", 4)]
