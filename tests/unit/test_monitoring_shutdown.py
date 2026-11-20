"""Only an explicit monitoring shutdown closes the live dashboard stream."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from evdt.monitoring import api


class MonitoringShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_keeps_running_at_arrival_and_sends_final_snapshot_on_shutdown(self):
        snapshot = {
            "run_id": "trip",
            "asset_id": "bus-001",
            "cursor": "trip:bus-001:12",
            "items": [{"source_state": "finished"}],
        }
        with tempfile.TemporaryDirectory() as folder:
            closed = Path(folder) / "closed"
            with (
                patch.dict(api.os.environ, {"EVDT_MONITORING_COMPLETION_FILE": str(closed)}),
                patch.object(api.dashboard, "snapshot", side_effect=lambda asset: dict(snapshot)),
            ):
                stream = api.dashboard_stream("bus-001", assets={"bus-001"}).body_iterator
                self.assertNotIn(
                    "monitoring_closed", json.loads((await anext(stream)).split("data: ")[1])
                )
                self.assertNotIn(
                    "monitoring_closed", api.dashboard_snapshot("bus-001", {"bus-001"})
                )
                closed.touch()
                with self.assertRaises(HTTPException) as denied:
                    api.dashboard_snapshot("bus-002", {"bus-001"})
                self.assertEqual(denied.exception.status_code, 403)
                self.assertIs(
                    api.dashboard_snapshot("bus-001", {"bus-001"})["monitoring_closed"], True
                )
                final = json.loads((await anext(stream)).split("data: ")[1])
                self.assertEqual(final["cursor"], snapshot["cursor"])
                self.assertIs(final["monitoring_closed"], True)
                with self.assertRaises(StopAsyncIteration):
                    await anext(stream)
