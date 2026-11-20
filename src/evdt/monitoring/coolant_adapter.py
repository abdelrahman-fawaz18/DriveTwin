"""Optional adapter-owned observation of an assumed externally conditioned supply."""

import json
import math
from pathlib import Path

from evdt.monitoring.plant import finite


class CoolantInlet:
    signal_id = "coolant.inlet.temperature"

    def __init__(self, config):
        if (
            set(config)
            != {"provider", "initial_k", "ambient_offset_k", "response_time_s", "evidence_class"}
            or config["provider"] != "conditioned-coolant-inlet/1.0.0"
            or config["evidence_class"] != "assumed"
        ):
            raise ValueError("Unsupported coolant boundary configuration")
        self.initial = self.value = finite(config["initial_k"], "initial coolant temperature")
        self.offset = finite(config["ambient_offset_k"], "inlet ambient offset")
        self.tau = finite(config["response_time_s"], "inlet response time")
        if not 200 <= self.value <= 400 or not -30 <= self.offset <= 60 or self.tau <= 0:
            raise ValueError("Invalid declared inlet boundary")

    def step(self, readings, dt):
        ambient = finite(readings["ambient.temperature"], "ambient")
        dt = finite(dt, "completed step")
        target = ambient + self.offset
        if dt <= 0 or not 200 <= target <= 400:
            raise ValueError("Invalid coolant boundary target/step")
        self.value = target + (self.value - target) * math.exp(-dt / self.tau)
        return {self.signal_id: self.value}


def optional_boundary(scenario: Path):
    path = scenario / "sensor-extension.json"
    return CoolantInlet(json.loads(path.read_text())) if path.is_file() else None
