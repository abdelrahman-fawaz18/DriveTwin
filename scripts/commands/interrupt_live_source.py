"""Test-only interlocks around the actual source process and its live command calls."""

import argparse
import signal
import sys

from evdt.live_speed_command import LiveSpeedCommand
from evdt.monitoring import adapter
from evdt.monitoring.source import SumoVehicleSource

CASES = ("before_actuation", "after_actuation")
PARKED = "EV-DT source parked for crash"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=CASES)
    args, remaining = parser.parse_known_args()
    real_post = LiveSpeedCommand.post
    real_frames = SumoVehicleSource.frames

    def park():
        print(PARKED, flush=True)
        signal.raise_signal(signal.SIGSTOP)
        raise RuntimeError("Crash fixture unexpectedly resumed")

    def post(self, endpoint, value):
        if args.case == "after_actuation" and endpoint == "result":
            if value["status"] != "acknowledged":
                raise RuntimeError("Expected a successful SUMO actuator call")
            print("EV-DT discarded live execution result", flush=True)
            raise TimeoutError("Injected loss before result publication")
        response = real_post(self, endpoint, value)
        if args.case == "before_actuation" and endpoint == "claim" and response[0] == 200:
            park()
        return response

    def frames(self):
        for frame in real_frames(self):
            yield frame
            if args.case == "after_actuation" and frame.sim_time_s == 8:
                park()

    LiveSpeedCommand.post = post
    SumoVehicleSource.frames = frames
    sys.argv[1:] = remaining
    adapter.main()


if __name__ == "__main__":
    main()
