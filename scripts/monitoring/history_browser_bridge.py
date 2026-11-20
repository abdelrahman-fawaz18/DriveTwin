"""Keep a verified restored history API alive for the Windows browser controller."""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def serve(control, checkpoint, base, token, manifest):
    metadata = manifest["metadata"]
    assets = list(dict.fromkeys((metadata["assets"][0], metadata["assets"][-1])))
    protocol = json.loads((ROOT / "benchmarks/history.json").read_bytes())
    fixture = {
        "base_url": base,
        "reader_token": token,
        "run_id": metadata["run_id"],
        "assets": assets,
        "from_s": 0,
        "to_s": min(3600, metadata["samples_per_asset"]),
        "signals": protocol["query"]["signals"],
        "expected_rows": min(3600, metadata["samples_per_asset"]) * 5,
        "full_W2_volume": len(metadata["assets"]) == 10 and metadata["samples_per_asset"] == 7200,
    }
    temporary = control / "ready.tmp"
    temporary.write_text(json.dumps(fixture))
    temporary.replace(control / "ready.json")
    deadline = time.monotonic() + 900
    while not (control / "browser-done").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("History browser controller deadline exceeded")
        time.sleep(0.25)
    result = json.loads((control / "browser-result.json").read_bytes())
    if not result.get("passed"):
        raise ValueError("History browser collection or independent download audit failed")
    return {
        "passed": True,
        "scope": "Actual bounded-history browser plots and complete raw downloads",
        "assets": assets,
        "rows_per_download": fixture["expected_rows"],
        "full_W2_volume": fixture["full_W2_volume"],
        "browser_result": result,
    }
