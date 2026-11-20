"""Cooperative control at the simulator's observation boundary."""

import json
import time
from pathlib import Path


class SourcePause:
    def __init__(self, directory: Path, publish_clock):
        self.directory = directory
        self.publish_clock = publish_clock
        self.revision = 0

    def request(self):
        try:
            value = json.loads((self.directory / "requests/pause.json").read_bytes())
        except FileNotFoundError:
            return None
        if type(value["revision"]) is not int or type(value["paused"]) is not bool:
            raise ValueError("Invalid source pause request")
        return value if value["revision"] > self.revision else None

    def acknowledge(self, request, sim_time_s):
        self.publish_clock("paused" if request["paused"] else "running", sim_time_s)
        temporary = self.directory / "status/pause.tmp"
        temporary.write_text(json.dumps({**request, "sim_time_s": sim_time_s}), encoding="utf-8")
        temporary.replace(self.directory / "status/pause.json")
        self.revision = request["revision"]

    def between_observations(self, sim_time_s):
        request = self.request()
        if request is None:
            return 0.0
        started = time.monotonic()
        self.acknowledge(request, sim_time_s)
        while request["paused"]:
            heartbeat_due = time.monotonic() + 1
            while time.monotonic() < heartbeat_due:
                time.sleep(0.1)
                change = self.request()
                if change is not None:
                    request = change
                    self.acknowledge(request, sim_time_s)
                    break
            else:
                self.publish_clock("paused", sim_time_s)
        return time.monotonic() - started
