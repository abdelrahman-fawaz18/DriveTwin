"""Strict, bounded JSON input for the route-aware worker.

Only the declared record graph can be decoded. No input can select a Python
class, filesystem path, import, URL, or executable. Authentication belongs to
the input builder; a valid job is not evidence of an authorized caller.
"""

import hashlib
import json
import types
from collections.abc import Hashable
from dataclasses import dataclass, fields, is_dataclass
from functools import lru_cache
from typing import Any, TypeVar, cast, get_args, get_origin, get_type_hints

from . import CONTRACT_VERSION
from .contracts import Conditions, LiveState, RouteContext, VehicleProfile
from .policy import ForecastPolicy
from .scenarios import RouteScenario
from .validation import choice, digest, integer, number, sequence

MAX_BYTES = 4_194_304
T = TypeVar("T")


@lru_cache(maxsize=64)
def _hints(record_type: type[Any]) -> dict[str, Any]:
    return get_type_hints(record_type)


@dataclass(frozen=True)
class ForecastRequest:
    version: str
    generation: int
    requested_real_life_s: float
    trigger: str
    state: LiveState
    route: RouteContext
    vehicle: VehicleProfile
    conditions: tuple[Conditions, ...]
    policy: ForecastPolicy
    scenario: RouteScenario | None = None
    assignment_sha256: str | None = None

    def __post_init__(self) -> None:
        choice(self.version, "request version", (CONTRACT_VERSION,))
        integer(self.generation, "input generation", 1, 9_007_199_254_740_991)
        number(self.requested_real_life_s, "request time", 0, 1e12)
        if self.assignment_sha256 is not None:
            digest(self.assignment_sha256, "request assignment")
        choice(
            self.trigger,
            "forecast trigger",
            ("first_ready", "periodic", "route_change", "conditions", "recovery", "qualification"),
        )
        for value, expected in (
            (self.state, LiveState),
            (self.route, RouteContext),
            (self.vehicle, VehicleProfile),
            (self.policy, ForecastPolicy),
        ):
            if not isinstance(value, expected):
                raise ValueError("Forecast request record type mismatch")
        sequence(self.conditions, "conditions", Conditions, 1, 1_000)
        if self.state.route_revision != self.route.revision:
            raise ValueError("Current progress and assigned route revision differ")
        if self.state.occurrence_index >= len(self.route.occurrences):
            raise ValueError("Current occurrence is outside the assigned route")
        if len(self.route.occurrences) > self.policy.max_segments:
            raise ValueError("Route exceeds request policy")
        if any(
            b.from_working_s < a.until_working_s
            for a, b in zip(self.conditions, self.conditions[1:])
        ):
            raise ValueError("Selected condition intervals must be ordered and nonoverlapping")
        if any(c.available_real_life_s > self.requested_real_life_s for c in self.conditions):
            raise ValueError("Conditions were unavailable at the forecast cutoff")
        if any(
            c.loaded_mass_kg is not None and c.loaded_mass_kg > self.vehicle.max_loaded_mass_kg
            for c in self.conditions
        ):
            raise ValueError("Conditions exceed the vehicle's maximum loaded mass")
        if self.scenario is not None:
            if not isinstance(self.scenario, RouteScenario):
                raise ValueError("Route scenario type mismatch")
            self.scenario.validate_for(self.route, self.vehicle, self.requested_real_life_s)
            if self.scenario.equipment is not None and any(
                c.extra_auxiliary_power_w for c in self.conditions
            ):
                raise ValueError(
                    "Equipment model cannot combine with unpartitioned extra auxiliary power"
                )


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON constant")


def _decode(expected: Any, value: Any) -> Any:
    origin, args = get_origin(expected), get_args(expected)
    if origin is types.UnionType and type(None) in args and len(args) == 2:
        return (
            None if value is None else _decode(next(t for t in args if t is not type(None)), value)
        )
    if origin is tuple:
        if not isinstance(value, list) or len(args) != 2 or args[1] is not Ellipsis:
            raise ValueError("Expected a JSON array")
        return tuple(_decode(args[0], item) for item in value)
    if isinstance(expected, type) and is_dataclass(expected):
        record_fields = fields(expected)
        expected_names = {field.name for field in record_fields}
        required = {
            field.name for field in record_fields if not field.metadata.get("omit_none_on_wire")
        }
        if not isinstance(value, dict) or not required <= set(value) <= expected_names:
            raise ValueError("Record has unknown or missing fields")
        hints = _hints(expected)
        return expected(**{key: _decode(hints[key], item) for key, item in value.items()})
    if expected is float:
        return number(value, "numeric field", -1e100, 1e100)
    if expected is int:
        return integer(value, "integer field", -9_007_199_254_740_991, 9_007_199_254_740_991)
    if expected in (str, bool) and type(value) is expected:
        return value
    raise ValueError("Record field type mismatch")


def decode_record(record_type: type[T], value: object) -> T:
    """Decode a caller-selected declared type, never a type chosen by JSON."""
    result: T = _decode(record_type, value)
    return result


def _primitive(value: Any, expected: Any = None) -> Any:
    if is_dataclass(value):
        hints = _hints(cast(Hashable, type(value)))
        return {
            field.name: _primitive(getattr(value, field.name), hints[field.name])
            for field in fields(value)
            if getattr(value, field.name) is not None or not field.metadata.get("omit_none_on_wire")
        }
    if isinstance(value, tuple):
        args = get_args(expected)
        return [_primitive(item, args[0] if args else None) for item in value]
    if expected is float and value is not None:
        # Integers supplied to float-typed constructors have the same identity.
        return float(value)
    if value is not None and get_origin(expected) is types.UnionType:
        actual = next(t for t in get_args(expected) if t is not type(None))
        return _primitive(value, actual)
    return value


def canonical_bytes(record: object) -> bytes:
    return json.dumps(
        _primitive(record),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    ).encode("ascii")


def fingerprint(record: object) -> str:
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


def parse_request(raw: bytes) -> ForecastRequest:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError("Request byte limit exceeded or input is empty")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant
        )
        request = decode_record(ForecastRequest, value)
    except (UnicodeError, RecursionError, OverflowError, TypeError) as exc:
        raise ValueError("Malformed forecast request") from exc
    if len(raw) > request.policy.max_request_bytes:
        raise ValueError("Request exceeds its configured byte limit")
    return request
