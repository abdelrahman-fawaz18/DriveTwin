"""Normalize selected already-admitted observations; never read plant internals."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping

SUPPORTED_ENVELOPE_VERSIONS = ("3.0.0", "3.1.0", "3.2.0")


class InputUnavailable(ValueError):
    """A stable reason code, without leaking rejected values into a forecast."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputUnavailable("invalid_" + name)
    try:
        result = float(value)
    except OverflowError as exc:
        raise InputUnavailable("invalid_" + name) from exc
    if not math.isfinite(result):
        raise InputUnavailable("invalid_" + name)
    return result


def text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 256:
        raise InputUnavailable("invalid_" + name)
    return value


def integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
        raise InputUnavailable("invalid_" + name)
    return value


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class ObservationScope:
    mode: str
    run_id: str
    asset_id: str
    source_id: str
    session_id: str
    source_epoch: int
    clock_domain: str

    def __post_init__(self) -> None:
        for name in ("mode", "run_id", "asset_id", "source_id", "session_id", "clock_domain"):
            text(getattr(self, name), name)
        integer(self.source_epoch, "source_epoch")


@dataclass(frozen=True)
class SignalBindings:
    energy: str = "battery.pack.energy"
    distance: str = "motion.distance"
    soc: str = "battery.pack.soc"
    speed: str = "motion.speed"

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            text(value, name)
        if len(set(self.__dict__.values())) != 4:
            raise ValueError("Each required quantity needs a distinct signal binding")


@dataclass(frozen=True)
class EnergyPoint:
    scope: ObservationScope
    event_id: str
    stream_seq: int
    sim_time_s: float
    energy_j: float
    distance_m: float
    soc: float
    speed_m_s: float
    provenance: tuple[str, ...]

    @property
    def normalized_sha256(self) -> str:
        """Fingerprint of selected inputs, not a claim about original wire bytes."""
        return fingerprint(asdict(self))


def observation_point(
    packet: Mapping[str, Any], bindings: SignalBindings = SignalBindings()
) -> EnergyPoint:
    """Read four coincident canonical-unit signals from a current admitted packet.

    This is not a replacement for upstream telemetry schema/authentication checks.
    Additional sensors and annotations are deliberately not model inputs.
    """
    if (
        not isinstance(packet, Mapping)
        or packet.get("schema_version") not in SUPPORTED_ENVELOPE_VERSIONS
    ):
        raise InputUnavailable("unsupported_observation_format")
    scope = ObservationScope(
        text(packet.get("mode"), "mode"),
        text(packet.get("run_id"), "run_id"),
        text(packet.get("asset_id"), "asset_id"),
        text(packet.get("source_id"), "source_id"),
        text(packet.get("session_id"), "session_id"),
        integer(packet.get("source_epoch"), "source_epoch"),
        text(packet.get("clock_domain"), "clock_domain"),
    )
    event_id = text(packet.get("event_id"), "event_id")
    sequence = integer(packet.get("stream_seq"), "stream_seq")
    sim_time = number(packet.get("sim_time_s"), "sim_time_s")
    if sim_time < 0:
        raise InputUnavailable("invalid_sim_time_s")
    samples = packet.get("samples")
    if not isinstance(samples, list) or len(samples) > 512:
        raise InputUnavailable("invalid_sample_group")
    required = {
        bindings.energy: "J",
        bindings.distance: "m",
        bindings.soc: "1",
        bindings.speed: "m/s",
    }
    selected: dict[str, float] = {}
    provenance: dict[str, str] = {}
    for sample in samples:
        if not isinstance(sample, Mapping):
            raise InputUnavailable("invalid_sample_group")
        signal = sample.get("signal_id")
        if not isinstance(signal, str):
            raise InputUnavailable("invalid_signal_id")
        if signal not in required:
            continue
        if signal in selected:
            raise InputUnavailable("duplicate_required_signal")
        if sample.get("quality") != "good":
            raise InputUnavailable("required_signal_not_good")
        if sample.get("unit") != required[signal]:
            raise InputUnavailable("required_signal_unit_mismatch")
        value = number(sample.get("value"), "required_signal_value")
        if value < 0 or (signal == bindings.soc and value > 1):
            raise InputUnavailable("required_signal_out_of_range")
        selected[signal] = value
        provenance[signal] = text(sample.get("provenance"), "provenance")
    if set(selected) != set(required):
        raise InputUnavailable("required_signal_missing")
    return EnergyPoint(
        scope,
        event_id,
        sequence,
        sim_time,
        selected[bindings.energy],
        selected[bindings.distance],
        selected[bindings.soc],
        selected[bindings.speed],
        tuple(provenance[name] for name in required),
    )
