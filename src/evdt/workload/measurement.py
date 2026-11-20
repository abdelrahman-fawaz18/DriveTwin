"""Pure strict accounting/clock/percentile rules; collectors remain separate."""

import math
from dataclasses import dataclass


def percentile(values, fraction):
    if not values or not 0 < fraction <= 1 or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Finite nonnegative nonempty observations required")
    return sorted(values)[math.ceil(fraction * len(values)) - 1]


@dataclass(frozen=True)
class OffsetBound:
    """Server UTC minus client UTC, bounded by a bracketed exchange."""

    lower_s: float
    upper_s: float

    def __post_init__(self):
        if (
            any(isinstance(v, bool) or not math.isfinite(v) for v in (self.lower_s, self.upper_s))
            or self.lower_s > self.upper_s
        ):
            raise ValueError("Finite ordered clock bounds required")

    @property
    def midpoint_s(self):
        return (self.lower_s + self.upper_s) / 2

    @property
    def uncertainty_s(self):
        return (self.upper_s - self.lower_s) / 2


def bracket(client_send, server_receive, server_send, client_receive):
    values = (client_send, server_receive, server_send, client_receive)
    if (
        any(not math.isfinite(v) for v in values)
        or client_receive < client_send
        or server_send < server_receive
    ):
        raise ValueError("Invalid clock exchange")
    bound = OffsetBound(server_send - client_receive, server_receive - client_send)
    if bound.lower_s > bound.upper_s:
        raise ValueError("Impossible clock exchange")
    return bound


def common_offset(before, after, max_uncertainty_s=0.05):
    if not math.isfinite(max_uncertainty_s) or max_uncertainty_s < 0:
        raise ValueError("Finite nonnegative uncertainty limit required")
    # Do not use just the narrowest round trip: both endpoint measurements matter.
    lower = max(before.lower_s, after.lower_s)
    upper = min(before.upper_s, after.upper_s)
    if lower > upper:
        raise ValueError("Clock offset changed; cross-clock latency is inconclusive")
    combined = OffsetBound(min(before.lower_s, after.lower_s), max(before.upper_s, after.upper_s))
    if combined.uncertainty_s > max_uncertainty_s:
        raise ValueError("Clock uncertainty exceeds policy; latency is inconclusive")
    return combined


def account(generated, accepted, quarantined, dropped, pending):
    groups = [set(v) for v in (generated, accepted, quarantined, dropped, pending)]
    for raw, unique in zip((generated, accepted, quarantined, dropped, pending), groups):
        if len(raw) != len(unique):
            raise ValueError("Duplicate identity in unique-event accounting")
    dispositions = groups[1:]
    if any(a & b for i, a in enumerate(dispositions) for b in dispositions[i + 1 :]):
        raise ValueError("One generated event has multiple terminal/pending dispositions")
    if set().union(*dispositions) != groups[0]:
        raise ValueError("Generated-event conservation failed")
    return {
        name: len(value)
        for name, value in zip(
            ("generated", "accepted", "quarantined", "dropped", "pending"), groups
        )
    }


def availability(generated_at, rendered_at, offset, *, max_missing_s=10):
    if not math.isfinite(max_missing_s) or max_missing_s <= 0:
        raise ValueError("Finite positive missing-event deadline required")
    if set(generated_at) != set(rendered_at):
        raise ValueError("Missing or extra browser event; never discard it from latency statistics")
    values = []
    for event, stamp in generated_at.items():
        delay = rendered_at[event] + offset.midpoint_s - stamp
        if (
            not math.isfinite(delay)
            or delay < -offset.uncertainty_s
            or delay + offset.uncertainty_s > max_missing_s
        ):
            raise ValueError("Invalid or timed-out event availability")
        values.append(max(0, delay))
    return {
        "count": len(values),
        "p95_upper_s": percentile(values, 0.95) + offset.uncertainty_s,
        "p99_upper_s": percentile(values, 0.99) + offset.uncertainty_s,
        "uncertainty_s": offset.uncertainty_s,
    }
