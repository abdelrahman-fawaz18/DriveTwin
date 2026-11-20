"""Bound application-receipt pressure without retiring or changing queued originals."""

import math


class ReceiptWindow:
    def __init__(self, limit: int = 32, retry_s: float = 5.0) -> None:
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("Application receipt window must be between one and 256 originals")
        if (
            type(retry_s) not in (int, float)
            or not math.isfinite(retry_s)
            or not 0.5 <= retry_s <= 30
        ):
            raise ValueError(
                "Application receipt retry must be finite and between 0.5 and 30 seconds"
            )
        self.limit, self.retry_s = limit, retry_s
        self.waiting: dict[str, float] = {}

    def refresh(self, pending_ids: set[str]) -> None:
        # Queue disappearance may mean a terminal receipt or an audited eviction.
        # This local scheduling cache does not decide which durable outcome occurred.
        self.waiting = {key: value for key, value in self.waiting.items() if key in pending_ids}

    def eligible(self, event_id: str, now: float) -> bool:
        if not math.isfinite(now):
            raise ValueError("Application receipt retry clock must be finite")
        previous = self.waiting.get(event_id)
        if previous is not None:
            if now < previous:
                raise ValueError("Application receipt retry clock moved backward")
            return now - previous >= self.retry_s
        return len(self.waiting) < self.limit

    def sent(self, event_id: str, now: float) -> None:
        if not self.eligible(event_id, now):
            raise RuntimeError("Original is outside its application receipt window")
        self.waiting[event_id] = now
