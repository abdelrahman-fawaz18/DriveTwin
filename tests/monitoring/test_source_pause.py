import json
from unittest.mock import patch

from evdt.monitoring.source_pause import SourcePause


def test_pause_heartbeats_hold_working_time_until_revisioned_resume(tmp_path):
    requests, status = tmp_path / "requests", tmp_path / "status"
    requests.mkdir()
    status.mkdir()
    command = requests / "pause.json"
    clocks = []
    now = 0.0
    command.write_text('{"revision": 1, "paused": true}')

    def wait(seconds):
        nonlocal now
        now += seconds
        if now >= 2.5:
            command.write_text('{"revision": 2, "paused": false}')

    source = SourcePause(tmp_path, lambda *clock: clocks.append(clock))
    with patch("evdt.monitoring.source_pause.time") as clock:
        clock.monotonic.side_effect = lambda: now
        clock.sleep.side_effect = wait
        elapsed = source.between_observations(42)
    assert elapsed >= 2.5
    assert len(clocks) >= 4
    assert all(event == ("paused", 42) for event in clocks[:-1])
    assert clocks[-1] == ("running", 42)
    assert json.loads((status / "pause.json").read_text()) == {
        "revision": 2,
        "paused": False,
        "sim_time_s": 42,
    }
    assert source.between_observations(43) == 0
