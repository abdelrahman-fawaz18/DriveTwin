"""Pause the ordinary simulated telemetry source at two test checkpoints."""

import time
from pathlib import Path

from evdt.monitoring import adapter
from evdt.monitoring.source import SumoVehicleSource

CONTROL = Path("/restart-control")
original_frames = SumoVehicleSource.frames


def gated_frames(source):
    for number, frame in enumerate(original_frames(source), 1):
        yield frame
        if number in (2, 3):
            (CONTROL / f"sample-{number}-queued").touch()
            deadline = time.monotonic() + 40
            while not (CONTROL / f"advance-{number}").is_file():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"No controller release after sample {number}")
                time.sleep(0.1)


if __name__ == "__main__":
    SumoVehicleSource.frames = gated_frames
    adapter.main()
