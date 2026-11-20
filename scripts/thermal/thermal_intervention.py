"""Single-writer cooling and charging handoff for an isolated simulated mission."""

from dataclasses import dataclass, replace
from datetime import timedelta

from thermal_advisory import ThermalAdvisory, invalid_reason

from evdt.command_timing import ClockLease
from evdt.contract_profile import Rejected
from evdt.energy.charging_dispatch import check_diversion

HANDOFF_SIM_S = 2
HANDOFF_REAL_S = 3
OBSERVATION_REAL_NS = 2_000_000_000


@dataclass(frozen=True)
class Interlocks:
    operator_override: bool = False
    local_protection: bool = False
    cooling_available: bool = True


@dataclass(frozen=True)
class CoolingReservation:
    scope: tuple[str, str]
    observed_s: int
    lease: ClockLease


class MissionBoundary:
    """Own the simulated actuator. A journal keeps a granted boost spent after restart."""

    def __init__(self, scope, journal, rules, warmup_s, boost_duration_s):
        self.scope = scope
        self.journal = journal
        self.duration_s = boost_duration_s
        self.monitor = ThermalAdvisory(rules, warmup_s)
        self.sample = self.advice = self.observed_clock = None
        self.pending = self.start_permit = self.active_until_s = self.station_id = None
        self.journal.execute(
            """CREATE TABLE IF NOT EXISTS cooling_grants (
                run_id TEXT, vehicle_id TEXT, observed_s INTEGER, granted_s INTEGER,
                PRIMARY KEY (run_id, vehicle_id)
            )"""
        )
        self.journal.execute(
            """CREATE TABLE IF NOT EXISTS operator_vetoes (
                run_id TEXT, vehicle_id TEXT, PRIMARY KEY (run_id, vehicle_id)
            )"""
        )
        self.operator_override = bool(
            self.journal.execute(
                "SELECT 1 FROM operator_vetoes WHERE (run_id, vehicle_id)=(?, ?)", self.scope
            ).fetchone()
        )

    def observe(self, sample, clock):
        advice = self.monitor.step(sample)
        self.sample, self.advice, self.observed_clock = sample, advice, clock
        return advice

    def override(self):
        self.operator_override = True
        self.pending = self.start_permit = self.active_until_s = None
        with self.journal:
            self.journal.execute("INSERT OR IGNORE INTO operator_vetoes VALUES (?, ?)", self.scope)

    def check_interlocks(self, interlocks):
        if interlocks.local_protection:
            raise Rejected("local_protection")
        if self.operator_override or interlocks.operator_override:
            raise Rejected("operator_override")

    def check_cooling(self, now, clock, interlocks, *, demand):
        self.check_interlocks(interlocks)
        if not interlocks.cooling_available or self.station_id is not None:
            raise Rejected("cooling_unavailable")
        if (
            self.sample is None
            or self.sample.time_s != now
            or clock.boot_id != self.observed_clock.boot_id
            or not 0 <= clock.elapsed_ns - self.observed_clock.elapsed_ns < OBSERVATION_REAL_NS
        ):
            raise Rejected("thermal_stale_observation")
        for probe in (self.sample.motor, self.sample.housing):
            if invalid_reason(probe, now, 0, None) is not None:
                raise Rejected("thermal_ineligible_probe")
        if self.advice.state in {"sensor_suspect", "data_unavailable"} or (
            demand and self.advice.state != "cooling_consistent"
        ):
            raise Rejected("thermal_evidence_ineligible")

    def reserve(self, scope, now, clock, interlocks):
        if scope != self.scope:
            raise Rejected("thermal_scope_mismatch")
        self.check_cooling(now, clock, interlocks, demand=True)
        if self.pending is not None:
            raise Rejected("thermal_already_reserved")
        if self.journal.execute(
            "SELECT 1 FROM cooling_grants WHERE (run_id, vehicle_id)=(?, ?)", scope
        ).fetchone():
            raise Rejected("thermal_grant_spent")
        lease = ClockLease.until(clock, clock.utc + timedelta(seconds=HANDOFF_REAL_S))
        self.pending = CoolingReservation(scope, now, lease)
        return self.pending

    def check_pending(self, reservation, now, clock, interlocks):
        if (
            reservation is None
            or reservation is not self.pending
            or reservation.scope != self.scope
        ):
            raise Rejected("thermal_reservation_invalid")
        self.check_handoff_deadline(reservation, now, clock)
        self.check_cooling(now, clock, interlocks, demand=True)

    @staticmethod
    def check_handoff_deadline(reservation, now, clock):
        if not reservation.observed_s <= now < reservation.observed_s + HANDOFF_SIM_S:
            raise Rejected("thermal_sim_deadline")
        reservation.lease.verify(clock)

    def grant(self, reservation, now, clock, interlocks):
        try:
            self.check_pending(reservation, now, clock, interlocks)
        finally:
            if reservation is self.pending:
                self.pending = None
        # Save the one-use grant before the simulator can see a cooling request.
        with self.journal:
            receipt = self.journal.execute(
                "INSERT OR IGNORE INTO cooling_grants VALUES (?, ?, ?, ?)",
                (*self.scope, reservation.observed_s, now),
            )
            if receipt.rowcount != 1:
                raise Rejected("thermal_grant_spent")
        self.start_permit = reservation
        self.active_until_s = now + self.duration_s

    def pump_on(self, now, clock, interlocks):
        if self.active_until_s is None:
            return False
        try:
            if now >= self.active_until_s:
                raise Rejected("thermal_duration_elapsed")
            if self.start_permit is not None:
                self.check_handoff_deadline(self.start_permit, now, clock)
            self.check_cooling(now, clock, interlocks, demand=self.start_permit is not None)
        except Rejected:
            self.start_permit = self.active_until_s = None
            return False
        self.start_permit = None
        return True

    def divert(self, proposal, context, clock, interlocks):
        """Use the Phase 4 admission gate before the same writer can select a station."""
        self.check_interlocks(interlocks)
        if self.scope != (context.run_id, context.vehicle_id):
            raise Rejected("charging_scope_mismatch")
        if self.station_id is not None:
            raise Rejected("charging_already_active")
        now = context.now_sim_s
        active = self.active_until_s is not None and now < self.active_until_s
        check_diversion(
            proposal,
            replace(
                context,
                thermal_block=context.thermal_block or active,
            ),
            clock,
            automatic=True,
        )
        if self.pending is not None:
            try:
                self.check_pending(self.pending, now, clock, interlocks)
            except Rejected:
                self.pending = None
            else:
                raise Rejected("thermal_handoff_pending")
        self.station_id = proposal.station_id
