"""Clock changes cannot extend the fixed real-time budget of a command."""

from datetime import timedelta

import pytest
from test_speed_limit_command import NOW, SCOPE, wire

from evdt.command_timing import ClockLease, HostClock
from evdt.commands import SpeedLimitExecutor
from evdt.contract_profile import Rejected

BOOT = "00000000-0000-0000-0000-000000000001"
ANCHOR = HostClock(BOOT, 100_000_000_000, NOW)
LEASE = ClockLease.until(ANCHOR, NOW + timedelta(seconds=10))


def test_aligned_clock_accepts_just_before_the_real_deadline():
    decoded = ClockLease.decode(LEASE.encode())
    assert decoded == LEASE
    decoded.verify(HostClock(BOOT, 109_999_000_000, NOW + timedelta(seconds=9.999)))


@pytest.mark.parametrize(
    "clock,reason",
    [
        (HostClock(BOOT, 110_000_000_000, NOW + timedelta(seconds=10)), "elapsed_deadline"),
        (HostClock(BOOT, 111_000_000_000, NOW + timedelta(seconds=3)), "elapsed_deadline"),
        (HostClock(BOOT, 109_800_000_000, NOW + timedelta(seconds=10)), "wall_deadline"),
        (HostClock(BOOT, 105_000_000_000, NOW + timedelta(seconds=3)), "clock_skew"),
        (HostClock(BOOT, 105_000_000_000, NOW + timedelta(seconds=7)), "clock_skew"),
        (HostClock(BOOT, 99_000_000_000, NOW), "clock_skew"),
        (
            HostClock("00000000-0000-0000-0000-000000000002", 105_000_000_000, NOW),
            "clock_epoch_changed",
        ),
    ],
)
def test_clock_changes_and_deadlines_fail_closed(clock, reason):
    with pytest.raises(Rejected, match=reason):
        LEASE.verify(clock)


def test_executor_rechecks_elapsed_time_even_if_utc_still_looks_valid():
    rollback = HostClock(BOOT, 111_000_000_000, NOW + timedelta(seconds=3))
    with pytest.raises(Rejected, match="elapsed_deadline"):
        SpeedLimitExecutor(SCOPE).execute(
            wire(),
            now=rollback.utc,
            sim_time_s=4,
            current_limit_m_s=12,
            apply_limit=lambda *args: pytest.fail("Expired command reached actuator"),
            verify_handoff=lambda: LEASE.verify(rollback),
        )


def test_malformed_handoff_clock_is_rejected():
    with pytest.raises(Rejected, match="invalid_clock_lease"):
        ClockLease.decode('["invalid",0,"2026-11-05T15:00:00Z",1000000000]')
