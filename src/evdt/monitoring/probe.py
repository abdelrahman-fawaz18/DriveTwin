"""Real-service P1 registry, quality, permissions and durable-delivery assertions."""

import argparse
import json

from evdt.monitoring.contracts import monitoring_contract
from evdt.monitoring.probe_support import api, require
from evdt.monitoring.registry_probe import run_final


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["initial", "final"])
    args = parser.parse_args()
    if args.action == "initial":
        require(
            api("/v1/current?asset_id=bus-001", credential=None)[0] == 401,
            "Anonymous reader allowed",
        )
        require(
            api("/v1/current?asset_id=bus-002", credential="bus-001")[0] == 403,
            "Cross-asset reader allowed",
        )
        status, value = api("/v1/current?asset_id=bus-001")
        require(
            status == 200
            and len(value["items"]) == len(monitoring_contract().catalog["signals"])
            and all(r["status"] == "unknown" for r in value["items"]),
            "Initial unavailable measurements fabricated",
        )
        print(
            json.dumps(
                {
                    "passed": True,
                    "checks": [
                        "unknown initial state",
                        "API authentication",
                        "asset-scoped reader",
                    ],
                }
            )
        )
    else:
        run_final()


if __name__ == "__main__":
    main()
