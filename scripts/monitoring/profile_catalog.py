"""Offline catalog component profiling, not a live workload acceptance result."""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.monitoring.asset_catalog import for_policy, validated_catalog  # noqa: E402
from evdt.monitoring.contracts import monitoring_contract  # noqa: E402
from evdt.workload.correlation import distribution  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()
    if not 3 <= args.samples <= 100:
        parser.error("Choose 3..100 component repetitions")
    raw = (ROOT / "catalog/monitoring-fleet-1.4.0.json").read_bytes()
    catalog = json.loads(raw)
    policy = {
        "sources": [
            {
                "asset_id": "bus-001",
                "source_id": "sumo-adapter",
                "expected": {
                    "catalog_version": "1.4.0",
                    "input_sha256": {"catalog": hashlib.sha256(raw).hexdigest()},
                    "signals": dict.fromkeys(catalog["signals"], {}),
                },
            }
        ]
    }
    old = []
    for _ in range(args.samples):
        started = time.perf_counter()
        original = monitoring_contract().readers["1.4.0"].catalog
        old.append(time.perf_counter() - started)
    original = {**original, "assets": {"bus-001": original["assets"]["bus-001"]}}
    validated_catalog.cache_clear()
    started = time.perf_counter()
    current = for_policy(policy, "bus-001")
    cold = time.perf_counter() - started
    if original != current:
        raise RuntimeError("Read-view definitions differ from the original contract")
    warm = []
    for _ in range(args.samples):
        started = time.perf_counter()
        result = for_policy(policy, "bus-001")
        warm.append(time.perf_counter() - started)
        if result != current:
            raise RuntimeError("Warm catalog view differs")
    print(
        json.dumps(
            {
                "passed": True,
                "scope": "offline component profiling; not W0/W1 or end-to-end latency",
                "original_family_construction": distribution(old),
                "new_view_first_call_s": cold,
                "new_view_warm": distribution(warm),
                "definitions_identical": True,
                "clock": "one process perf_counter elapsed intervals",
                "source_sha256": {
                    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in (
                        "src/evdt/monitoring/asset_catalog.py",
                        "src/evdt/monitoring/contracts.py",
                        "src/evdt/contract_profile.py",
                        "scripts/monitoring/profile_catalog.py",
                    )
                },
            }
        )
    )


if __name__ == "__main__":
    main()
