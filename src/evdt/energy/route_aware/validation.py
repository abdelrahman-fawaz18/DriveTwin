"""Small strict validators shared by the route-aware domain records."""

import math
import re


def number(value: object, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} is unrepresentable") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")
    return result


def integer(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def text(value: object, name: str, limit: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError(f"{name} must be nonempty bounded printable text")
    return value


def digest(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def choice(value: object, name: str, values: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in values:
        raise ValueError(f"Unsupported {name}")
    return value


def sequence(value: object, name: str, element: type, minimum: int, maximum: int) -> None:
    if (
        not isinstance(value, tuple)
        or not minimum <= len(value) <= maximum
        or not all(isinstance(item, element) for item in value)
    ):
        raise ValueError(f"{name} must be an immutable bounded tuple of {element.__name__}")
