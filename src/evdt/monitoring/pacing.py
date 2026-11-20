"""Wall-clock pacing without changing physical integration or sample intervals."""

import time

TIME_SCALES = (0.5, 1, 2, 5, 10, 20)


def time_scale(value=1):
    if type(value) not in (int, float) or value not in TIME_SCALES:
        raise ValueError("Choose a trip speed: 0.5, 1, 2, 5, 10, or 20")
    return float(value)


class WorkingClock:
    def __init__(self, scale=1, started=None):
        self.scale = time_scale(scale)
        self.started = time.monotonic() if started is None else started

    def wait_until(self, working_s):
        target = self.started + working_s / self.scale
        time.sleep(max(0.0, target - time.monotonic()))
        return max(0.0, time.monotonic() - target)

    def delay(self, real_life_s):
        self.started += real_life_s
