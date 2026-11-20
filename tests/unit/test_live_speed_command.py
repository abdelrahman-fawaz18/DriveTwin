"""The opt-in source continues monitoring when a handoff cannot be verified."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from test_speed_limit_command import SCOPE, wire

from evdt.command_authority import FENCE_HEADER, FENCE_PROTOCOL
from evdt.command_timing import CLOCK_HEADER, ClockLease, HostClock
from evdt.live_speed_command import ARBITRATION_PROFILE, LiveSpeedCommand


def current_wire(now, **changes):
    utc_format = "%Y-%m-%dT%H:%M:%SZ"
    return wire(
        issued_at=now.strftime(utc_format),
        expires_at=(now + timedelta(seconds=20)).strftime(utc_format),
        **changes,
    )


def test_unreachable_command_service_leaves_actuator_alone(tmp_path, monkeypatch):
    credential = tmp_path / "executor"
    credential.write_text("x" * 36)
    command = LiveSpeedCommand(SCOPE.run_id, SCOPE.asset_id, credential)
    vehicle = Mock()
    post = Mock(side_effect=TimeoutError)
    monkeypatch.setattr(command, "post", post)

    command.step(4, vehicle)

    post.assert_called_once_with("claim", {"sim_time_s": 4, "state": "running"})
    assert vehicle.mock_calls == []


def test_missing_clock_lease_records_refusal_before_actuation(tmp_path, monkeypatch):
    credential = tmp_path / "executor"
    credential.write_text("x" * 36)
    command = LiveSpeedCommand(SCOPE.run_id, SCOPE.asset_id, credential)
    vehicle = Mock()
    vehicle.getMaxSpeed.return_value = 12
    raw = current_wire(datetime.now(timezone.utc))
    post = Mock(side_effect=[(200, raw, {}), (204, b"", {})])
    monkeypatch.setattr(command, "post", post)

    command.step(4, vehicle)

    vehicle.setMaxSpeed.assert_not_called()
    assert post.call_count == 2
    assert post.call_args.args[0] == "refusal"
    assert post.call_args.args[1]["reason"] == "invalid_clock_lease"


@pytest.mark.parametrize("generation", [None, "0", "9223372036854775808", "1"])
def test_live_arbitration_fails_closed_with_invalid_or_unverified_fence(
    tmp_path, monkeypatch, generation
):
    credential = tmp_path / "executor"
    credential.write_text("x" * 36)
    command = LiveSpeedCommand(SCOPE.run_id, SCOPE.asset_id, credential, ARBITRATION_PROFILE)
    vehicle = Mock()
    vehicle.getMaxSpeed.return_value = 12
    now = datetime.now(timezone.utc)
    clock = HostClock(SCOPE.run_id, 100, now)
    lease = ClockLease.until(clock, now + timedelta(seconds=10)).encode()
    raw = current_wire(now, issuer="dispatch-coordinator")
    replies = [(200, raw, {CLOCK_HEADER: lease, FENCE_HEADER: generation})]
    if generation == "1":
        replies.append(TimeoutError())
    post = Mock(side_effect=[*replies, (204, b"", {})])
    monkeypatch.setattr(command, "post", post)

    command.step(4, vehicle)

    vehicle.setMaxSpeed.assert_not_called()
    assert post.call_args_list[0].args == (
        "claim",
        {"sim_time_s": 4, "state": "running", "fence_protocol": FENCE_PROTOCOL},
    )
    assert [call.args[0] for call in post.call_args_list[1:]] == (
        ["verify-fence", "refusal"] if generation == "1" else ["refusal"]
    )
    assert post.call_args.args[1]["reason"] == "authority_fence_refused"
