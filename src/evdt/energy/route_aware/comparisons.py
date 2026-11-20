"""Bounded live comparison windows; no fitting or parameter changes."""

from collections import deque
from dataclasses import dataclass, field

from .forecast import ForecastResult
from .residuals import EnergyObservation, Residual, assess_saved_window
from .wire import ForecastRequest


@dataclass
class Comparisons:
    observations: deque[EnergyObservation] = field(default_factory=lambda: deque(maxlen=1000))
    pending: dict[int, tuple[ForecastRequest, ForecastResult]] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def observe(self, observation: EnergyObservation) -> None:
        if self.observations and self.observations[-1].event_id == observation.event_id:
            if self.observations[-1] != observation:
                raise ValueError("Accepted observation changed under the same identity")
            return
        if self.observations and (
            self.observations[-1].scope != observation.scope
            or self.observations[-1].route_revision != observation.route_revision
            or self.observations[-1].working_s >= observation.working_s
        ):
            self.observations.clear()
        self.observations.append(observation)

    def add(self, fence: int, request: ForecastRequest, result: ForecastResult) -> None:
        if (
            result.status != "available"
            or result.summary is None
            or result.summary.comparison_window is None
        ):
            return
        if len(self.pending) >= 32:
            self._skip("pending_comparison_budget")
            return
        self.pending[fence] = (request, result)

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def assess(self, now_real_life_s: float) -> list[tuple[int, Residual]]:
        ready = []
        for fence, (request, result) in list(self.pending.items()):
            assert result.summary and result.summary.comparison_window
            end = request.state.working_s + result.summary.comparison_window.duration_working_s
            if not self.observations:
                continue
            latest = self.observations[-1]
            if (
                latest.scope != request.state.scope
                or latest.route_revision != request.route.revision
            ):
                self._skip("source_or_route_changed")
            elif latest.working_s < end:
                continue
            else:
                selected = tuple(
                    o for o in self.observations if request.state.working_s <= o.working_s <= end
                )
                try:
                    residual = assess_saved_window(
                        request, result, selected, assessed_real_life_s=now_real_life_s
                    )
                except ValueError:
                    self._skip("incomplete_or_incompatible_observations")
                else:
                    ready.append((fence, residual))
            del self.pending[fence]
        return ready
