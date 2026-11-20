"""Keep local command validity tied to the Linux boot and elapsed real time."""

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from evdt.contract_profile import Rejected

CLOCK_HEADER = "X-EVDT-Command-Clock"
MAX_CLOCK_SKEW_S = 0.5


@dataclass(frozen=True)
class HostClock:
    boot_id: str
    elapsed_ns: int
    utc: datetime


def host_clock() -> HostClock:
    boot_id = str(UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
    elapsed_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    return HostClock(boot_id, elapsed_ns, datetime.now(timezone.utc))


@dataclass(frozen=True)
class ClockLease:
    anchor: HostClock
    deadline_ns: int

    @classmethod
    def until(cls, clock: HostClock, expires: datetime) -> "ClockLease":
        remaining = (expires - clock.utc) // timedelta(microseconds=1)
        if not 0 < remaining <= 30_000_000:
            raise Rejected("wall_validity")
        return cls(clock, clock.elapsed_ns + remaining * 1000)

    def verify(self, current: HostClock) -> None:
        if current.boot_id != self.anchor.boot_id:
            raise Rejected("clock_epoch_changed")
        elapsed_s = (current.elapsed_ns - self.anchor.elapsed_ns) / 1_000_000_000
        if current.elapsed_ns >= self.deadline_ns:
            raise Rejected("elapsed_deadline")
        utc_s = (current.utc - self.anchor.utc).total_seconds()
        if elapsed_s < 0 or abs(utc_s - elapsed_s) > MAX_CLOCK_SKEW_S:
            raise Rejected("clock_skew")
        utc_deadline = self.anchor.utc + timedelta(
            microseconds=(self.deadline_ns - self.anchor.elapsed_ns) // 1000
        )
        if current.utc >= utc_deadline:
            raise Rejected("wall_deadline")

    def encode(self) -> str:
        return json.dumps(
            [
                self.anchor.boot_id,
                self.anchor.elapsed_ns,
                self.anchor.utc.isoformat(),
                self.deadline_ns,
            ],
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, raw: str) -> "ClockLease":
        try:
            boot_id, elapsed_ns, timestamp, deadline_ns = json.loads(raw)
            utc = datetime.fromisoformat(timestamp)
            if (
                str(UUID(boot_id)) != boot_id
                or utc.utcoffset() != timedelta(0)
                or type(elapsed_ns) is not int
                or type(deadline_ns) is not int
                or not 0 <= elapsed_ns < deadline_ns <= elapsed_ns + 30_000_000_000
            ):
                raise ValueError("invalid clock lease")
        except (ValueError, TypeError, AttributeError) as error:
            raise Rejected("invalid_clock_lease") from error
        return cls(HostClock(boot_id, elapsed_ns, utc), deadline_ns)
