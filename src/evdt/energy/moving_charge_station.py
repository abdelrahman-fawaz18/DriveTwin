"""Station inputs for the moving-bus advisor."""

from dataclasses import dataclass

from .route_aware.validation import choice, digest, integer, number, text

STATION_VERSION = "moving-charge-station/1.0.0"
STATUS_VERSION = "moving-charge-status/1.0.0"


@dataclass(frozen=True)
class StationBay:
    version: str
    site_id: str
    bay_id: str
    connector: str
    platform_length_m: float
    lane_id: str
    start_offset_m: float
    end_offset_m: float
    power_w: float
    efficiency: float
    setup_working_s: float
    network_sha256: str
    access_evidence: str
    location_reference: str
    operating_domain: str = "sumo"

    def __post_init__(self):
        choice(self.version, "station record version", (STATION_VERSION,))
        choice(self.operating_domain, "operating domain", ("sumo",))
        for name in (
            "site_id",
            "bay_id",
            "connector",
            "lane_id",
            "access_evidence",
            "location_reference",
        ):
            text(getattr(self, name), name)
        number(self.platform_length_m, "platform length", 1, 100)
        number(self.start_offset_m, "bay start", 0, 10_000_000)
        number(
            self.end_offset_m, "bay end", self.start_offset_m + self.platform_length_m, 10_000_000
        )
        number(self.power_w, "bay input power", 1, 1_000_000)
        number(self.efficiency, "bay transfer efficiency", 0.01, 1)
        number(self.setup_working_s, "bay setup duration", 0, 900)
        digest(self.network_sha256, "reviewed network")

    def access_problem(self, network, *, connector, vehicle_length_m):
        if self.network_sha256 != network.sha256:
            return "bay_network_changed"
        lane = network.lanes.get(self.lane_id)
        if lane is None or lane.internal or self.end_offset_m > lane.length_m:
            return "bay_lane_unavailable"
        if self.connector != connector:
            return "connector_incompatible"
        if self.platform_length_m < vehicle_length_m:
            return "insufficient_bay_length"
        return None


@dataclass(frozen=True)
class StationStatus:
    version: str
    bay_id: str
    scenario_id: str
    revision: int
    observed_real_life_s: float
    received_real_life_s: float
    observed_working_s: float
    state: str
    open_until_working_s: float
    available_from_working_s: float | None
    offered_power_w: float | None

    def __post_init__(self):
        choice(self.version, "station status version", (STATUS_VERSION,))
        text(self.bay_id, "observed bay")
        text(self.scenario_id, "authorized scenario")
        integer(self.revision, "station status revision", 1, 9_007_199_254_740_991)
        choice(
            self.state,
            "bay status",
            ("available", "occupied", "reserved", "out_of_order", "unknown"),
        )
        number(self.observed_real_life_s, "status observation time", 0, 1e12)
        number(self.received_real_life_s, "status receipt time", self.observed_real_life_s, 1e12)
        number(self.observed_working_s, "status working time", 0, 1e12)
        number(self.open_until_working_s, "access window end", self.observed_working_s, 1e12)
        if self.state == "available" or (
            self.state == "occupied"
            and (self.available_from_working_s is not None or self.offered_power_w is not None)
        ):
            number(
                self.available_from_working_s,
                "estimated ready time",
                self.observed_working_s,
                self.open_until_working_s,
            )
            number(self.offered_power_w, "observed charging power limit", 1, 1_000_000)
        elif self.available_from_working_s is not None or self.offered_power_w is not None:
            raise ValueError("Unavailable bay cannot advertise a ready time or power")
        if self.state == "available" and self.available_from_working_s != self.observed_working_s:
            raise ValueError("Available bay must report readiness at its observation time")

    def window(
        self,
        bay,
        *,
        scenario_id,
        now_real_life_s,
        current_working_s,
        arrival_working_s,
        status_max_age_real_life_s,
    ):
        """Read a provisional booking window. The executor needs a fresh status at handoff."""
        number(now_real_life_s, "status evaluation time", 0, 1e12)
        number(current_working_s, "current working time", 0, 1e12)
        number(arrival_working_s, "candidate arrival", current_working_s, 1e12)
        number(status_max_age_real_life_s, "maximum bay status age", 0.01, 600)
        if (self.scenario_id, self.bay_id) != (scenario_id, bay.bay_id):
            raise ValueError("Station status belongs to a different bay or scenario")
        if (
            self.received_real_life_s > now_real_life_s
            or self.observed_working_s > current_working_s
            or now_real_life_s - self.observed_real_life_s > status_max_age_real_life_s
        ):
            raise ValueError("Station status is stale or has not yet been received")
        if self.available_from_working_s is None or self.offered_power_w is None:
            raise ValueError("Station has no usable offer")
        charge_from = max(arrival_working_s, self.available_from_working_s) + bay.setup_working_s
        if charge_from >= self.open_until_working_s:
            raise ValueError("Station closes before charging can begin")
        return ChargeWindow(
            charge_from,
            self.open_until_working_s,
            min(bay.power_w, self.offered_power_w),
            bay.efficiency,
        )


@dataclass(frozen=True)
class ChargeWindow:
    charge_from_working_s: float
    charge_until_working_s: float
    input_power_w: float
    efficiency: float

    def __post_init__(self):
        start = number(self.charge_from_working_s, "charge window start", 0, 1e12)
        end = number(self.charge_until_working_s, "charge window end", start, 1e12)
        if end <= start:
            raise ValueError("Charge window must have positive duration")
        number(self.input_power_w, "station input power", 1, 1_000_000)
        number(self.efficiency, "station transfer efficiency", 0.01, 1)
