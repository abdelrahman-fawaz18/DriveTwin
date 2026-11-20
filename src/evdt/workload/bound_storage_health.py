"""Actual scoped API proxy assertions for a connected/stopped bounded sender."""

import argparse
import json
import os

from evdt.monitoring.probe_support import api, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", choices=("connected", "unavailable"))
    args = parser.parse_args()
    route = "/v1/system-health?asset_id=bus-001"
    require(api(route, credential=None)[0] == 401, "Anonymous system-health read")
    require(
        api("/v1/system-health?asset_id=bus-002", credential="bus-001")[0] == 403,
        "System-health reader escaped its asset scope",
    )
    status, health = api(route)
    require(
        status == 200
        and health["run_id"] == os.environ["EVDT_RUN_ID"]
        and health["asset_id"] == "bus-001",
        "Health identity differs",
    )
    sender = health["sender"]
    if args.state == "connected":
        require(
            sender["available"] is True
            and sender["broker_connected"] is True
            and sender["source_id"] == "sumo-adapter"
            and health["physical_quota_enforced"] is True
            and health["dropped_records"] == 0
            and sender["overflow_indication"] is False,
            "Current bounded sender diagnostics differ",
        )
        storage = sender["storage"]
        require(
            sender.get("delivery")
            == {"application_window_limit": 32, "application_retry_seconds": 5},
            "Qualified application receipt-window settings differ",
        )
        require(
            storage["mode"] == "nominal"
            and storage["physical_quota_enforced"] is True
            and storage["filesystems"]["telemetry"]["image_bytes"] == 64 * 1024**2
            and storage["filesystems"]["critical"]["image_bytes"] == 8 * 1024**2
            and storage["critical_fault"]["healthy"] is True
            and storage["critical_fault"]["valid_copies"] == 2,
            "Verified physical budgets or fault health differ",
        )
        q = sender["queue"]
        require(
            q["dropped"] == q["evicted_unconfirmed"] == q["evicted_lifetime"] == 0
            and q["generated"] == sum(q[k] for k in ("accepted", "rejected", "pending", "dropped")),
            "Nominal queue accounting differs",
        )
    else:
        require(
            sender["available"] is False
            and health["dropped_records"] is None
            and health["physical_quota_enforced"] is False,
            "Stopped sender retained a current zero-loss/physical-bound claim",
        )
    print(
        json.dumps(
            {
                "passed": True,
                "state": args.state,
                "anonymous_and_asset_denials_passed": True,
                "current_health_identity_and_storage_checked": True,
                "delivery": sender.get("delivery") if args.state == "connected" else None,
            }
        )
    )


if __name__ == "__main__":
    main()
