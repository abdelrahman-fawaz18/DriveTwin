"""Configured scalar cadence and deterministic source-side sensor imperfections."""

import math
import random
from typing import Any

from evdt.monitoring.plant import finite


class SensorBank:
    def __init__(self, catalog: dict[str, Any], step_s: float, sampling: dict[str, Any]) -> None:
        finite(step_s, "step")
        if step_s <= 0:
            raise ValueError("Positive simulation step required")
        definitions = catalog["signals"]
        overrides = sampling["period_overrides_s"]
        if set(overrides) - set(definitions):
            raise ValueError("Unregistered sampling override")
        self.definitions, self.step_s = definitions, step_s
        self.period_ticks = {}
        seed = sampling.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("Sensor seed must be an integer")
        self.random = random.Random(seed)
        self.faults = sampling.get("faults", {})
        if set(self.faults) - set(definitions):
            raise ValueError("Unregistered sensor configuration")
        for key in definitions:
            period = finite(overrides.get(key, sampling["default_period_s"]), "sample period")
            ratio = period / step_s
            if ratio < 1 or not math.isclose(ratio, round(ratio), abs_tol=1e-9, rel_tol=0):
                raise ValueError("Cadence must be an integral multiple of the simulation step")
            self.period_ticks[key] = round(ratio)
            fault = self.faults.get(key, {})
            if set(fault) - {"bias", "noise_std", "quantum", "missing", "invalid"}:
                raise ValueError("Unknown sensor configuration field")
            for name in ("bias", "noise_std", "quantum"):
                value = finite(fault.get(name, 0), name)
                if name != "bias" and value < 0:
                    raise ValueError("Noise and quantization must be nonnegative")
            for name in ("missing", "invalid"):
                for start, end in fault.get(name, []):
                    if finite(start, "interval start") < 0 or finite(end, "interval end") <= start:
                        raise ValueError("Invalid sensor interval")

    def timings(self) -> dict[str, dict[str, float]]:
        return {
            key: {
                "period_s": ticks * self.step_s,
                "freshness_s": max(
                    float(self.definitions[key].get("freshness_s", 5)), 3 * ticks * self.step_s
                ),
            }
            for key, ticks in self.period_ticks.items()
        }

    def sample(self, tick: int, readings: dict[str, float]) -> list[dict[str, Any]]:
        if tick < 1:
            raise ValueError("Sample only completed positive ticks")
        if set(readings) != set(self.definitions):
            raise ValueError("Plant/catalog coverage mismatch")
        result = []
        for key, spec in self.definitions.items():
            if tick % self.period_ticks[key]:
                continue
            sample: dict[str, Any] = {
                "signal_id": key,
                "unit": spec["unit"],
                "quality": "good",
                "provenance": "simulated-observation",
            }
            fault = self.faults.get(key, {})
            t = tick * self.step_s
            if any(start <= t < end for start, end in fault.get("missing", [])):
                sample.update(value=None, quality="missing", reason="dropout")
            else:
                try:
                    value = finite(readings[key], key) + fault.get("bias", 0)
                    std = fault.get("noise_std", 0)
                    if std:
                        value += self.random.gauss(0, std)
                    quantum = fault.get("quantum", 0)
                    if quantum:
                        value = round(value / quantum) * quantum
                    value = finite(value, key)
                    reason = (
                        "sensor_fault"
                        if any(start <= t < end for start, end in fault.get("invalid", []))
                        else "out_of_range"
                        if not spec["minimum"] <= value <= spec["maximum"]
                        else None
                    )
                    if reason:
                        sample.update(value=None, quality="invalid", reason=reason, raw_value=value)
                    else:
                        sample["value"] = value
                except ValueError:
                    sample.update(value=None, quality="invalid", reason="nonfinite")
            result.append(sample)
        return result
