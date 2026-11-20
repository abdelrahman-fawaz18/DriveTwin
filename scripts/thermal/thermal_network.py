"""Passive one- and two-node heat networks for offline qualification."""

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter


@dataclass(frozen=True)
class HeatNetwork:
    capacity: tuple[float, ...]  # J/K
    ambient: tuple[float, ...]  # W/K
    coolant: tuple[float, ...]  # W/K
    coupling: float = 0.0  # W/K between two nodes

    def discrete(self, seconds):
        n = len(self.capacity)
        coefficients = (*self.capacity, *self.ambient, *self.coolant, self.coupling, seconds)
        if (
            n not in (1, 2)
            or len(self.ambient) != n
            or len(self.coolant) != n
            or not all(map(math.isfinite, coefficients))
            or min(*self.capacity, seconds) <= 0
            or min(*self.ambient, *self.coolant, self.coupling) < 0
            or (n == 1 and self.coupling != 0)
        ):
            raise ValueError("Invalid thermal network or step")
        scale = np.sqrt(self.capacity)
        conductance = np.diag(np.add(self.ambient, self.coolant, dtype=float))
        if n == 2:
            conductance += self.coupling * np.array([[1.0, -1.0], [-1.0, 1.0]])
        rates, modes = np.linalg.eigh(-conductance / np.outer(scale, scale))
        rates = np.minimum(rates, 0.0)  # A passive network can only decay or conserve heat.
        decay = np.exp(rates * seconds)
        integral = np.divide(
            np.expm1(rates * seconds), rates, out=np.full(n, float(seconds)), where=rates != 0
        )
        to_modes = scale[:, None] * modes
        from_modes = modes.T / scale
        source = modes / scale[:, None] * integral
        return DiscreteNetwork(to_modes, from_modes, decay, source, self.ambient, self.coolant)


@dataclass(frozen=True)
class DiscreteNetwork:
    to_modes: np.ndarray
    from_modes: np.ndarray
    decay: np.ndarray
    source: np.ndarray
    ambient: tuple[float, ...]
    coolant: tuple[float, ...]

    @property
    def transition(self):
        """State transition for a column vector of physical node temperatures."""
        return ((self.to_modes * self.decay) @ self.from_modes).T

    def increments(self, ambient, coolant, heat):
        return (
            heat + ambient[..., None] * self.ambient + coolant[..., None] * self.coolant
        ) @ self.source

    def rollout(self, initial, ambient, coolant, heat):
        """Each input row acts over one interval. Return the state after each interval."""
        initial, ambient, coolant, heat = map(np.asarray, (initial, ambient, coolant, heat))
        n = len(self.decay)
        if (
            initial.shape != (n,)
            or ambient.ndim != 1
            or coolant.shape != ambient.shape
            or heat.shape != (len(ambient), n)
            or not len(ambient)
            or not all(np.all(np.isfinite(x)) for x in (initial, ambient, coolant, heat))
            or any(np.any(x <= 0) for x in (initial, ambient, coolant))
            or np.any(heat < 0)
        ):
            raise ValueError("Invalid thermal trajectory inputs")
        start = initial @ self.to_modes
        forcing = self.increments(ambient, coolant, heat)
        result = np.empty_like(forcing)
        for i, decay in enumerate(self.decay):
            result[:, i], _ = lfilter([1.0], [1.0, -decay], forcing[:, i], zi=[decay * start[i]])
        return result @ self.from_modes
