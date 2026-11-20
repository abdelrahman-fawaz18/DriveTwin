"""Strict offline validation of real collector records; no missing-event exclusion."""

import json
import math
from collections import defaultdict, deque

from evdt.workload.measurement import OffsetBound, bracket, percentile

IDENTITY = ("run_id", "asset_id", "source_id", "event_id")
STAGES = {
    "sample_ready",
    "enqueue_committed",
    "publish_attempt",
    "publish_return",
    "broker_ack",
    "receipt_applied",
    "receiver_callback",
    "delivery_complete",
    "projection_staged",
    "transaction_committed",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(record):
    result = tuple(record.get(key) for key in IDENTITY)
    require(
        all(isinstance(value, str) and 1 <= len(value) <= 128 for value in result),
        "Incomplete correlation identity",
    )
    return result


def iter_trace(path):
    """Validate every record, including the header, without loading the file."""
    with path.open("rb") as source:
        header = None
        previous = -1
        reference = None
        for raw in source:
            require(raw.endswith(b"\n") and len(raw) <= 65536, "Missing or truncated trace")
            row = json.loads(raw)
            require(
                all(
                    isinstance(row[k], (int, float))
                    and not isinstance(row[k], bool)
                    and math.isfinite(row[k])
                    for k in ("utc_s", "monotonic_s")
                ),
                "Invalid trace timestamp",
            )
            if header is None:
                header = row
                require(header.get("version") == "event-trace/1.1.0", "Unsupported trace format")
                require(
                    header["stage"] == "process_start" and header["sequence"] == 0,
                    "Missing trace header",
                )
                reference = header["utc_s"] - header["monotonic_s"]
            require(
                row["process"] == header["process"] and row["sequence"] == previous + 1,
                "Missing, duplicate or reordered trace record",
            )
            require(
                abs(row["utc_s"] - row["monotonic_s"] - reference) <= 0.01,
                "Linux wall clock stepped",
            )
            previous = row["sequence"]
            yield row
        require(header is not None, "Missing or truncated trace")


def load_trace(path):
    rows = iter_trace(path)
    header = next(rows)
    return header, list(rows)


def validate_clock_exchanges(exchanges, *, start_s, end_s, resolution_s=0.002):
    require(len(exchanges) >= 3 and 0 <= resolution_s <= 0.005, "Insufficient clock evidence")
    bounds = []
    previous = None
    reference = None
    for row in exchanges:
        bound = bracket(
            row["client_send_s"], row["received_utc_s"], row["sent_utc_s"], row["client_receive_s"]
        )
        require(abs(row["wall_residual_s"]) <= 0.01, "Browser wall clock stepped")
        require(
            abs(
                (row["client_receive_s"] - row["client_send_s"])
                - (row["client_receive_monotonic_s"] - row["client_send_monotonic_s"])
            )
            <= 0.005,
            "Client elapsed clocks disagree",
        )
        offset = row["received_utc_s"] - row["received_monotonic_s"]
        if reference is None:
            reference = offset
        require(abs(offset - reference) <= 0.01, "Server wall clock stepped")
        require(
            abs(
                (row["sent_utc_s"] - row["received_utc_s"])
                - (row["sent_monotonic_s"] - row["received_monotonic_s"])
            )
            <= 0.005,
            "Server elapsed clocks disagree",
        )
        if previous is not None:
            require(0 <= row["client_send_s"] - previous <= 7.5, "Periodic clock coverage missing")
        previous = row["client_send_s"]
        bounds.append(OffsetBound(bound.lower_s - resolution_s, bound.upper_s + resolution_s))
    require(
        exchanges[0]["client_send_s"] <= start_s and exchanges[-1]["client_receive_s"] >= end_s,
        "Clock exchanges do not bracket rendered events",
    )
    require(len({row["boot_id"] for row in exchanges}) == 1, "Linux boot changed")
    return bounds


def clock_bounds(exchanges, *, start_s, end_s, resolution_s=0.002):
    bounds = validate_clock_exchanges(
        exchanges, start_s=start_s, end_s=end_s, resolution_s=resolution_s
    )
    require(
        max(b.lower_s for b in bounds) <= min(b.upper_s for b in bounds), "Clock offset changed"
    )
    combined = OffsetBound(min(b.lower_s for b in bounds), max(b.upper_s for b in bounds))
    require(combined.uncertainty_s <= 0.05, "Clock uncertainty exceeds 50 ms")
    return combined


def distribution(values):
    return {
        "count": len(values),
        "min_s": min(values),
        "max_s": max(values),
        "p95_s": percentile(values, 0.95),
        "p99_s": percentile(values, 0.99),
    }


def correlate(
    originals, records, rendered, *, warmup=60, clock_resolution_s=0.000001, include_samples=False
):
    require(
        math.isfinite(clock_resolution_s) and 0 < clock_resolution_s <= 0.05,
        "Unsupported local elapsed-clock resolution",
    )
    expected = {identity(event): event for event in originals}
    require(len(expected) == len(originals), "Duplicate generated identity")
    stages = defaultdict(lambda: defaultdict(list))
    for row in records:
        require(row["stage"] in STAGES, "Unknown observation stage")
        key = identity(row)
        require(key in expected, "Extra traced event")
        stages[key][row["stage"]].append(row)
    require(set(stages) == set(expected), "Missing traced event")
    require(set(rendered) == {key[-1] for key in expected}, "Missing or extra DOM event")
    spans = defaultdict(list)
    upper, witness_errors = [], []
    attempt_total = 0
    delivery_total = 0
    duplicates = 0
    for key, original in expected.items():
        event = key[-1]
        by_stage = stages[key]
        unique = {}
        for name in (
            "sample_ready",
            "enqueue_committed",
            "projection_staged",
            "transaction_committed",
        ):
            require(len(by_stage[name]) == 1, "Missing or duplicate unique stage: " + name)
            unique[name] = by_stage[name][0]
        starts = by_stage["publish_attempt"]
        returns = by_stage["publish_return"]
        acknowledgments = by_stage["broker_ack"]
        require(
            starts and len(starts) == len(returns) == len(acknowledgments),
            "Incomplete publish attempt",
        )
        attempt_ids = {row["attempt_id"] for row in starts}
        require(
            len(attempt_ids) == len(starts)
            and attempt_ids
            == {r["attempt_id"] for r in returns}
            == {r["attempt_id"] for r in acknowledgments},
            "Publish attempt identity mismatch",
        )
        require(
            all(row["rc"] == 0 for row in returns)
            and all(not row["failed"] for row in acknowledgments),
            "Publish attempt failed",
        )
        for start in starts:
            returned = next(r for r in returns if r["attempt_id"] == start["attempt_id"])
            ack = next(r for r in acknowledgments if r["attempt_id"] == start["attempt_id"])
            require(
                start["monotonic_s"] <= returned["monotonic_s"]
                and start["monotonic_s"] <= ack["monotonic_s"]
                and returned["mid"] == ack["mid"],
                "Invalid publish attempt order",
            )
        receives, completed = by_stage["receiver_callback"], by_stage["delivery_complete"]
        require(receives and len(receives) == len(completed), "Incomplete receiver delivery")
        require(
            sum(row["outcome"] == "accepted" for row in completed) == 1
            and all(row["outcome"] in ("accepted", "duplicate") for row in completed),
            "Unexpected delivery disposition",
        )
        require(
            unique["transaction_committed"]["outcome"] == "accepted", "Transaction was not accepted"
        )
        applied = by_stage["receipt_applied"]
        require(
            sum(row["applied"] is True for row in applied) == 1,
            "Missing applied persistence receipt",
        )
        for row in (
            receives + [unique["enqueue_committed"], unique["transaction_committed"]] + completed
        ):
            require(row["wire_sha256"] == original["wire_sha256"], "Traced original bytes differ")
        ordered = [
            unique["sample_ready"],
            unique["enqueue_committed"],
            min(starts, key=lambda r: r["monotonic_s"]),
            min(receives, key=lambda r: r["monotonic_s"]),
            unique["projection_staged"],
            unique["transaction_committed"],
        ]
        require(
            all(a["monotonic_s"] <= b["monotonic_s"] for a, b in zip(ordered, ordered[1:])),
            "Observation stages moved backward",
        )
        dom = rendered[event]
        require(
            identity(dom) == key and abs(dom["wall_residual_s"]) <= 0.01,
            "Rendered identity/clock differs",
        )
        require(
            set(dom["values"]) == {s["signal_id"] for s in original["body"]["samples"]},
            "Rendered signals differ",
        )
        # Browser captures actual text; compare formatting independently in its native Intl formatter.
        require(
            dom.get("values_verified") is True and dom["wire_sha256"] == original["wire_sha256"],
            "Rendered values were not checked against originals",
        )
        witness = dom["witness"]
        require(
            witness["version"] == "clock-exchange/1.1.0"
            and witness["asset_id"] == key[1]
            and witness["event_id"] == event,
            "DOM witness identity differs",
        )
        require(
            witness["client_send_monotonic_s"] >= dom["monotonic_s"]
            and witness["client_receive_monotonic_s"] >= witness["client_send_monotonic_s"],
            "DOM witness preceded rendering",
        )
        error = witness["client_receive_monotonic_s"] - dom["monotonic_s"] + 0.002
        delay = (
            witness["received_monotonic_s"]
            - unique["sample_ready"]["monotonic_s"]
            + clock_resolution_s
        )
        require(
            math.isfinite(error) and 0 <= error <= 10,
            "DOM witness confirmation exceeded ten seconds",
        )
        require(math.isfinite(delay) and 0 <= delay <= 10, "Invalid or timed-out DOM availability")
        if original["body"]["stream_seq"] >= warmup:
            labels = (
                "sample_to_enqueue",
                "queue_to_first_publish",
                "publish_to_receive",
                "receive_to_projection_staged",
                "projection_staged_to_commit_return",
            )
            for name, a, b in zip(labels, ordered, ordered[1:]):
                spans[name].append(b["monotonic_s"] - a["monotonic_s"])
            spans["source_to_receive"].append(ordered[3]["monotonic_s"] - ordered[0]["monotonic_s"])
            spans["receive_to_commit_return"].append(
                ordered[-1]["monotonic_s"] - ordered[3]["monotonic_s"]
            )
            upper.append(delay)
            witness_errors.append(error)
            spans["commit_return_to_dom_upper"].append(
                max(0, witness["received_monotonic_s"] - ordered[-1]["monotonic_s"])
            )
        attempt_total += len(starts)
        delivery_total += len(receives)
        duplicates += len(receives) - 1
    result = {
        "latency_method": "same-Linux-clock upper bound from sample-ready to server receipt of post-DOM witness",
        "witness_confirmation_overhead_bound_max_s": max(witness_errors),
        "local_elapsed_clock_resolution_s": clock_resolution_s,
        "cross_domain_timestamp_subtraction": False,
        "unique_generated": len(expected),
        "accepted": len(expected),
        "quarantined": 0,
        "dropped": 0,
        "pending": 0,
        "measured_events": len(upper),
        "warmup_events": len(expected) - len(upper),
        "publish_attempts": attempt_total,
        "receiver_deliveries": delivery_total,
        "duplicate_deliveries": duplicates,
        "source_to_dom_upper": distribution(upper),
        "stages": {k: distribution(v) for k, v in spans.items()},
    }
    if include_samples:
        result["samples"] = {"source_to_dom_upper": upper, **spans}
    return result


class ResourceTimeline:
    """Check clock continuity and sample coverage."""

    def __init__(self, *, allow_gradual_wall_drift=False):
        self.allow_gradual_wall_drift = allow_gradual_wall_drift
        self.reference = None
        self.previous = None
        self.count = 0

    def observe(self, row):
        require(
            all(
                isinstance(row[k], (float, int))
                and not isinstance(row[k], bool)
                and math.isfinite(row[k])
                for k in ("utc_s", "monotonic_s")
            ),
            "Invalid resource timestamp",
        )
        require(row["sequence"] == self.count, "Resource sequence gap")
        stamp = row["monotonic_s"]
        offset = row["utc_s"] - stamp
        if self.reference is not None:
            require(abs(offset - self.reference) <= 0.01, "Resource clock stepped")
        if self.previous is not None:
            require(0 < stamp - self.previous <= 1.5, "Resource sample coverage gap")
        if self.reference is None or self.allow_gradual_wall_drift:
            self.reference = offset
        self.previous = stamp
        self.count += 1


def resource_summary(rows, *, platform):
    require(len(rows) >= 2, "Insufficient resource coverage")
    if platform == "windows":
        timeline = ResourceTimeline()
        for row in rows:
            timeline.observe(row)
        require(
            all(
                isinstance(row[key], int) and not isinstance(row[key], bool) and row[key] >= 0
                for row in rows
                for key in (
                    "physical_available_bytes",
                    "browser_name_total_bytes",
                )
            ),
            "Invalid Windows memory quantity",
        )
        require(
            all(
                r["wsl_name_total_bytes"] is None
                or (
                    isinstance(r["wsl_name_total_bytes"], int)
                    and not isinstance(r["wsl_name_total_bytes"], bool)
                    and r["wsl_name_total_bytes"] >= 0
                )
                for r in rows
            ),
            "Invalid WSL memory quantity",
        )
        longest = current = 0.0
        for previous, row in zip(rows, rows[1:]):
            if previous["physical_available_bytes"] < 2 * 1024**3:
                current += row["monotonic_s"] - previous["monotonic_s"]
            else:
                current = 0
            longest = max(longest, current)
            require(
                all(math.isfinite(v) and v >= 0 for v in row["paging"].values()),
                "Invalid Windows paging",
            )
        return {
            "samples": len(rows),
            "available_min_bytes": min(r["physical_available_bytes"] for r in rows),
            "sampled_longest_below_2GiB_s": longest,
            "short_run_host_conditions_met": longest <= 30,
            "browser_name_total_peak_bytes": max(r["browser_name_total_bytes"] for r in rows),
            "wsl_name_total_peak_bytes": max(
                (r["wsl_name_total_bytes"] for r in rows if r["wsl_name_total_bytes"] is not None),
                default=None,
            ),
            "unreadable_wsl_process_samples": sum(
                r.get("unreadable_wsl_processes", 0) for r in rows
            ),
            "unreadable_browser_process_samples": sum(
                r.get("unreadable_browser_processes", 0) for r in rows
            ),
            "process_scope": "name totals may include other sessions; not isolated browser/WSL overhead",
            "paging_peak_pages_input_s": max(r["paging"]["pages_input_s"] for r in rows),
            "paging_peak_pages_output_s": max(r["paging"]["pages_output_s"] for r in rows),
        }
    require(platform == "linux", "Unknown resource platform")
    samples = LinuxResourceSamples()
    for row in rows:
        samples.observe(row)
    return samples.summary()


class LinuxResourceSamples:
    """Validate once per sample and keep only the history needed for rolling swap."""

    def __init__(self, *, restartable_services=()):
        self.timeline = ResourceTimeline()
        self.boot_id = None
        self.counters = {}
        self.restartable_services = frozenset(restartable_services)
        self.window = deque()
        self.previous_swap = (0, 0)
        self.core_peak = 0
        self.available_min = None
        self.swap_peak = None
        self.excessive_streak = 0
        self.maximum_streak = 0

    def observe(self, row):
        self.timeline.observe(row)
        if self.boot_id is None:
            self.boot_id = row["boot_id"]
        require(self.boot_id == row["boot_id"], "Linux resource boot changed")
        require(
            all(
                isinstance(row[key], int) and not isinstance(row[key], bool) and row[key] >= 0
                for key in ("swap_in_pages", "swap_out_pages", "page_bytes")
            ),
            "Invalid Linux resource quantity",
        )
        paths = [g["group"].rstrip("/") for g in row["containers"]]
        require(
            len(paths) == len(set(paths))
            and not any(a != b and b.startswith(a + "/") for a in paths for b in paths),
            "Cgroup double counting",
        )
        require(all(not s["oom_killed"] for s in row["stopped"]), "Container OOM kill")
        for group in row["containers"]:
            require(
                isinstance(group["memory_bytes"], int)
                and not isinstance(group["memory_bytes"], bool)
                and group["memory_bytes"] >= 0,
                "Invalid cgroup memory quantity",
            )
            require(
                group["memory_events"].get("oom", 0) == 0
                and group["memory_events"].get("oom_kill", 0) == 0,
                "Cgroup OOM event",
            )
            started_at = None
            if self.restartable_services and group["service"] in self.restartable_services:
                # Docker can reuse a cgroup path when the planned restart creates a new process.
                started_at = group.get("container_started_at")
                require(
                    isinstance(started_at, str) and bool(started_at),
                    "Missing container start identity",
                )
            for counter, value in group["memory_events"].items():
                key = (group["group"], started_at, counter)
                require(value >= self.counters.get(key, 0), "Cgroup counter reset")
                self.counters[key] = value
        self.core_peak = max(self.core_peak, sum(g["memory_bytes"] for g in row["containers"]))
        available = row["linux_memory"]["MemAvailable"]
        self.available_min = (
            available if self.available_min is None else min(self.available_min, available)
        )
        swap = (row["swap_in_pages"], row["swap_out_pages"])
        require(
            all(new >= old for new, old in zip(swap, self.previous_swap)),
            "Swap counter reset",
        )
        self.previous_swap = swap
        stamp = row["monotonic_s"]
        self.window.append((stamp, sum(swap)))
        while len(self.window) > 1 and stamp - self.window[1][0] >= 60:
            self.window.popleft()
        if stamp - self.window[0][0] >= 60:
            traffic = (sum(swap) - self.window[0][1]) * row["page_bytes"]
            self.swap_peak = traffic if self.swap_peak is None else max(self.swap_peak, traffic)
            self.excessive_streak = self.excessive_streak + 1 if traffic > 64 * 1024**2 else 0
            self.maximum_streak = max(self.maximum_streak, self.excessive_streak)

    @property
    def within_budget(self):
        return self.core_peak <= 6 * 1024**3 and self.maximum_streak < 3

    def summary(self):
        return {
            "samples": self.timeline.count,
            "core_peak_bytes": self.core_peak,
            "rolling_60s_swap_max_bytes": self.swap_peak,
            "consecutive_excessive_rolling_windows": self.maximum_streak,
            "swap_window_method": "60-second rolling windows sampled each second; three consecutive excessive windows fail Q-12",
            "short_run_budget_conditions_met": self.within_budget,
            "whole_linux_available_min_bytes": self.available_min,
            "oom_events": 0,
        }
