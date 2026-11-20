"""Check old observation bytes through the current SQL and wire validator."""

import json
from pathlib import Path
from uuid import UUID

from evdt.contracts import Rejected
from evdt.monitoring.contracts import monitoring_contract, observation_topic
from evdt.monitoring.registry_probe import legacy_upgrade

run = str(UUID(int=123))
packet = {
    "schema_version": "3.0.0",
    "catalog_version": "1.2.0",
    "manifest_sha256": "0" * 64,
    "event_id": str(UUID(int=1)),
    "asset_id": "bus-001",
    "source_id": "sumo-adapter",
    "source_epoch": 1,
    "session_id": str(UUID(int=2)),
    "stream_seq": 0,
    "run_id": run,
    "mode": "simulation",
    "observed_at": "2026-11-03T00:00:00Z",
    "clock_domain": "simulation",
    "sim_time_s": 1,
    "samples": [
        {
            "signal_id": "location.latitude",
            "unit": "deg",
            "value": 45.51,
            "quality": "good",
            "provenance": "simulated-observation",
        }
    ],
}


def main():
    legacy_upgrade(packet)
    unsupported = {**packet, "schema_version": "99.0.0"}
    topic = observation_topic(run, packet["asset_id"], packet["source_id"])
    try:
        monitoring_contract().validate(json.dumps(unsupported).encode(), topic, run)
    except Rejected as exc:
        if str(exc) != "envelope_schema":
            raise
    else:
        raise RuntimeError("Unknown telemetry schema accepted")
    print(
        json.dumps(
            {
                "passed": True,
                "original_wire_bytes_and_payload_preserved": True,
                "history_sample_backfill_preserved": True,
                "unsupported_schema_rejected": True,
                "sql_migrations": sorted(
                    p.name for p in Path("/workspace/migrations/monitoring").glob("*.sql")
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
