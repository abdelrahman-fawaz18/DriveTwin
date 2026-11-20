"""Select the exact reviewed runtime baseline without rewriting historical proof."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def verify_shared():
    profile = json.loads((ROOT / "contracts/monitoring.profile.json").read_bytes())
    if (ROOT / "qualification/workload/catalog-view-core.json").is_file():
        from runtime_scope import verify_shared as verify
    elif (ROOT / "qualification/workload/capacity-core.json").is_file():
        from capacity_scope import verify_shared as verify
    elif (ROOT / "qualification/workload/measurement-core.json").is_file():
        from measurement_scope import verify_shared as verify
    elif "1.4.0" in profile["catalogs"]:
        from workload_scope import verify_shared as verify
    else:
        from extension_scope import verify_shared as verify
    return verify()
