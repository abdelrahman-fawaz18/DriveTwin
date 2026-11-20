"""Frozen cold/warm one-hour query cases, independently checked against source originals."""

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if __package__:
    from .history_query_audit import audit_plot, audit_summary, source_points
    from .qualify_history_restore import require, restore
else:
    from history_query_audit import audit_plot, audit_summary, source_points
    from qualify_history_restore import require, restore

ROOT = Path(__file__).resolve().parents[2]
MAX_RESPONSE = 8 * 1024 * 1024


def cases():
    protocol = json.loads((ROOT / "benchmarks/history.json").read_bytes())
    frozen = json.loads((ROOT / "benchmarks/history-query-cases.json").read_bytes())
    expected = [
        {
            "id": f"{i:02}-{j + 1}",
            "asset_id": f"bus-{i:03}",
            "from_s": j * 3600,
            "to_s": (j + 1) * 3600,
        }
        for i in range(1, 11)
        for j in range(2)
    ]
    require(
        protocol["version"] == "history-volume/1.0.0"
        and frozen["version"] == "history-query-cases/1.0.0"
        and frozen["cases"] == expected
        and protocol["query"]["window_s"] == 3600
        and frozen["point_limit_total"] == protocol["query"]["point_limit_total"] == 5000
        and frozen["p95_limit_s"] == protocol["query"]["p95_limit_s"] == 2
        and protocol["query"]["cold_cases"]
        == protocol["query"]["warm_cases"]
        == len(expected)
        == 20
        and len(set(protocol["query"]["signals"])) == 5,
        "Query cases no longer match the frozen quality profile",
    )
    return frozen, protocol


def distribution(values):
    require(values and all(math.isfinite(v) and v >= 0 for v in values), "Invalid query durations")
    ordered = sorted(values)
    return {
        "count": len(values),
        "p95_s": ordered[math.ceil(len(ordered) * 0.95) - 1],
        "min_s": ordered[0],
        "max_s": ordered[-1],
    }


def qualify(checkpoint, output, compose, base, token, manifest):
    frozen, protocol = cases()
    metadata = manifest["metadata"]
    selected_cases = [c for c in frozen["cases"] if c["asset_id"] in metadata["assets"]]
    full = len(selected_cases) == 20 and metadata["samples_per_asset"] == 7200
    result = {
        "passed": False,
        "Q05_W2_passed": False,
        "full_W2_volume": full,
        "profile": frozen,
        "clock_resolution_s": time.get_clock_info("monotonic").resolution,
        "primary_timing": "HTTP response plus JSON parsing on one client monotonic clock; independent validation excluded",
        "cold": [],
        "warm": [],
    }
    query_dir = output / "queries"
    query_dir.mkdir()

    def read(path):
        began = time.monotonic()
        with urlopen(
            Request(base + path, headers={"Authorization": "Bearer " + token}), timeout=10
        ) as response:
            raw = response.read(MAX_RESPONSE + 1)
            require(len(raw) <= MAX_RESPONSE, "Historical response exceeded 8 MiB")
            parsed = json.loads(raw)
        ended = time.monotonic()
        return parsed, raw, ended - began

    cursors = {
        asset: read("/v1/snapshot?" + urlencode({"asset_id": asset}))[0]["cursor"]
        for asset in metadata["assets"]
    }
    profile = json.loads((checkpoint / "artifacts/contracts/monitoring.profile.json").read_bytes())
    catalog = json.loads((checkpoint / "artifacts" / profile["catalogs"]["1.4.0"]).read_bytes())
    signals = protocol["query"]["signals"]
    last_asset, points, source = None, None, None
    try:
        for temperature in ("cold", "warm"):
            print(f"Running {len(selected_cases)} frozen {temperature} history cases", flush=True)
            for case in selected_cases:
                asset = case["asset_id"]
                if asset != last_asset:
                    points, source = source_points(checkpoint, asset, signals)
                    require(
                        all(len(rows) == metadata["samples_per_asset"] for rows in points.values()),
                        "Original history volume differs from checkpoint metadata",
                    )
                    last_asset = asset
                context = {
                    "run_id": metadata["run_id"],
                    "asset_id": asset,
                    "as_of": cursors[asset],
                    "mode": "simulation",
                    "time_basis": "simulation seconds",
                    "catalog_version": "1.4.0",
                    "policy_sha256": hashlib.sha256(
                        (checkpoint / "run-config/run_policy.json").read_bytes()
                    ).hexdigest(),
                    "signals": {s: catalog["signals"][s] for s in signals},
                    "timings": {
                        s: {"source_id": source["identity"]["source_id"], **source["signals"][s]}
                        for s in signals
                    },
                }
                row = {"case": case, "passed": False}
                result[temperature].append(row)
                if temperature == "cold":
                    compose("restart", "db", timeout=90)
                    deadline = time.monotonic() + 60
                    while True:
                        try:
                            ready = read("/health/ready")[0]["ready"]
                            if ready:
                                break
                        except (OSError, ValueError):
                            pass
                        require(time.monotonic() < deadline, "PostgreSQL restart did not recover")
                        time.sleep(0.25)
                query = {
                    "asset_id": asset,
                    "as_of": cursors[asset],
                    "signals": signals,
                    "from_s": case["from_s"],
                    "to_s": case["to_s"],
                    "point_limit": 5000,
                }
                path = "/v1/plot?" + urlencode(query, doseq=True)
                if temperature == "warm":
                    prime, prime_raw, prime_duration = read(path)
                    row["priming"] = {
                        "elapsed_s": prime_duration,
                        "response_bytes": len(prime_raw),
                        "response_sha256": hashlib.sha256(prime_raw).hexdigest(),
                        "audit": audit_plot(prime, points, context, case),
                    }
                    (query_dir / ("priming-" + case["id"] + ".json")).write_bytes(prime_raw)
                response, raw, duration = read(path)
                row.update(
                    elapsed_s=duration,
                    response_bytes=len(raw),
                    response_sha256=hashlib.sha256(raw).hexdigest(),
                )
                (query_dir / (temperature + "-" + case["id"] + ".json")).write_bytes(raw)
                row["audit"] = audit_plot(response, points, context, case)
                if temperature == "warm":
                    query.pop("point_limit")
                    summary, raw_summary, _ = read(
                        "/v1/history-summary?" + urlencode({**query, "bucket_s": 60}, doseq=True)
                    )
                    (query_dir / ("summary-" + case["id"] + ".json")).write_bytes(raw_summary)
                    row["summary_audit"] = audit_summary(summary, points, context, case)
                row["passed"] = True
        result["distributions"] = {
            kind: distribution([r["elapsed_s"] for r in result[kind]]) for kind in ("cold", "warm")
        }
        require(
            all(
                len(result[k]) == len(selected_cases) and all(r["passed"] for r in result[k])
                for k in ("cold", "warm")
            ),
            "Query denominator or correctness incomplete",
        )
        result["timing_conditions_met"] = all(
            v["p95_s"] <= 2 for v in result["distributions"].values()
        )
        result["passed"] = result["timing_conditions_met"]
        result["Q05_W2_passed"] = full and result["passed"]
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        (output / "history-queries.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    checkpoint, output = args.checkpoint.resolve(), args.evidence_dir.resolve()
    if (
        os.name == "nt"
        or not checkpoint.is_relative_to(ROOT / ".private/backups")
        or not output.is_relative_to(ROOT / "evidence/p1")
        or output.exists()
    ):
        parser.error(
            "Use the Linux launcher, a private canonical checkpoint and new evidence directory"
        )
    output.mkdir(parents=True)
    inputs = [
        Path(__file__),
        ROOT / "scripts/monitoring/history_query_audit.py",
        ROOT / "scripts/monitoring/history_download_audit.py",
        ROOT / "scripts/monitoring/qualify_history_restore.py",
        ROOT / "benchmarks/history-query-cases.json",
        ROOT / "benchmarks/history.json",
    ]

    def fingerprint():
        return {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in inputs
        }

    before = fingerprint()
    result = restore(
        checkpoint,
        output,
        after_restore=lambda compose, base, token, manifest: qualify(
            checkpoint, output, compose, base, token, manifest
        ),
    )
    if before != fingerprint():
        result.update(passed=False, error="Query qualification source changed")
    result["query_source_sha256"] = before
    (output / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
