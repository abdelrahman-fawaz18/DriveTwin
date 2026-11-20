"""Independent original-value, continuity and extrema audit for bounded history queries."""

import base64
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime

if __package__:
    from .history_download_audit import same_value
    from .qualify_history_restore import equal_query, require
else:
    from history_download_audit import same_value
    from qualify_history_restore import equal_query, require


def source_points(checkpoint, asset, signals):
    directory = checkpoint / "sources" / asset
    summary = json.loads((directory / "source-summary.json").read_bytes())
    manifest_raw = (directory / "plant/run-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    require(
        hashlib.sha256(manifest_raw).hexdigest() == summary["manifest_sha256"],
        "Original source recipe fingerprint differs",
    )
    result, digest, count = defaultdict(list), hashlib.sha256(), 0
    with (directory / "originals.jsonl").open("rb") as stream:
        while line := stream.readline(100000):
            require(
                len(line) < 100000 and line.endswith(b"\n"), "Truncated/unbounded source original"
            )
            record = json.loads(line)
            raw = base64.b64decode(record["raw_base64"], validate=True)
            body = json.loads(raw)
            require(
                hashlib.sha256(raw).hexdigest() == record["wire_sha256"], "Source original changed"
            )
            require(
                all(body[k] == v for k, v in summary["identity"].items())
                and body["event_id"] == record["event_id"]
                and body["stream_seq"] == count
                and abs(body["sim_time_s"] - count - 1) < 1e-8
                and body["manifest_sha256"] == summary["manifest_sha256"],
                "Source sequence/identity/provenance differs",
            )
            for sample in body["samples"]:
                if sample["signal_id"] in signals:
                    result[sample["signal_id"]].append(
                        {
                            **{
                                k: body[k]
                                for k in (
                                    "event_id",
                                    "sim_time_s",
                                    "observed_at",
                                    "source_id",
                                    "source_epoch",
                                    "stream_seq",
                                )
                            },
                            "signal_id": sample["signal_id"],
                            "sample": sample,
                        }
                    )
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
            count += 1
    require(
        count == summary["events"] == summary["expected_events"]
        and digest.hexdigest() == summary["original_packets_sha256"]
        and set(result) == set(signals)
        and all(len(rows) == count for rows in result.values()),
        "Incomplete selected original history",
    )
    return result, manifest


def expected(points, signals, start, end, count, width, periods):
    """Python grouping and ordered selection, independent of the database window SQL."""
    # HTTP window fields and PostgreSQL time columns are declared doubles.
    # Normalize expected statistics, without modifying any original sample value.
    start, end = float(start), float(end)
    groups, buckets = defaultdict(list), {}
    for signal in signals:
        for index in range(count):
            buckets[signal, index] = {
                "signal_id": signal,
                "from_s": start + index * width,
                "to_s": min(end, start + (index + 1) * width),
                "count": 0,
                "good": 0,
                "missing": 0,
                "invalid": 0,
                "minimum": None,
                "maximum": None,
                "mean": None,
                "first_good_s": None,
                "last_good_s": None,
                "continuity_breaks": 0,
                "values": [],
            }
        previous, segment = {}, defaultdict(int)
        rows = sorted(
            (r for r in points[signal] if start <= r["sim_time_s"] <= end),
            key=lambda r: (r["sim_time_s"], r["event_id"]),
        )
        for row in rows:
            owner = (row["source_id"], row["source_epoch"])
            old = previous.get(owner)
            quality, value = row["sample"].get("quality", "good"), row["sample"]["value"]
            require(quality in ("good", "missing", "invalid"), "Unknown original quality")
            good = quality == "good" and value is not None
            require(quality != "good" or good, "Good original sample cannot be null")
            require(
                not good or (type(value) in (int, float) and math.isfinite(value)),
                "Nonfinite original value",
            )
            split = (
                not good
                or old is None
                or old["sample"].get("quality", "good") != "good"
                or old["sample"]["value"] is None
                or row["sim_time_s"] - old["sim_time_s"]
                > periods[signal] + max(1e-9, periods[signal] * 1e-6)
            )
            segment[owner] += int(split)
            previous[owner] = row
            index = min(count - 1, max(0, math.floor((row["sim_time_s"] - start) / width)))
            bucket = buckets[signal, index]
            bucket["count"] += 1
            bucket[quality] += 1
            bucket["continuity_breaks"] += int(split and old is not None)
            if good:
                tagged = {
                    **row,
                    "segment_id": row["source_id"]
                    + ":"
                    + str(row["source_epoch"])
                    + ":"
                    + str(segment[owner]),
                }
                groups[signal, owner, segment[owner], index].append(tagged)
                bucket["values"].append((value, row["sim_time_s"]))
    retained = {}
    for rows in groups.values():
        choices = [
            rows[0],
            rows[-1],
            min(rows, key=lambda r: (r["sample"]["value"], r["sim_time_s"], r["event_id"])),
            min(rows, key=lambda r: (-r["sample"]["value"], r["sim_time_s"], r["event_id"])),
        ]
        for row in choices:
            retained[row["signal_id"], row["event_id"]] = row
    for bucket in buckets.values():
        values = bucket.pop("values")
        if values:
            numbers = [v for v, _ in values]
            bucket.update(
                minimum=float(min(numbers)),
                maximum=float(max(numbers)),
                mean=math.fsum(numbers) / len(numbers),
                first_good_s=float(min(t for _, t in values)),
                last_good_s=float(max(t for _, t in values)),
            )
    return sorted(
        retained.values(), key=lambda r: (r["signal_id"], r["sim_time_s"], r["event_id"])
    ), list(buckets.values())


def audit_plot(response, points, context, case, point_limit=5000):
    metadata = response["metadata"]
    signals = list(context["signals"])
    for key, value in context.items():
        require(metadata.get(key) == value, "Plot scope/catalog/timing metadata differs: " + key)
    start, end = case["from_s"], case["to_s"]
    require(
        metadata["from_s"] == start
        and metadata["to_s"] == end
        and metadata["plot_version"] == "history-plot/1.0.0"
        and metadata["interpolated"] is False
        and metadata["resolution"] == "first/minimum/maximum/last per bucket and continuous segment"
        and metadata["point_limit"] == point_limit,
        "Plot version/window/reduction differs",
    )
    count = min(point_limit // (4 * len(signals)), max(1, math.ceil((end - start) / 1e-6)))
    width = (end - start) / count if end > start else 1.0
    require(metadata["bucket_s"] == width, "Plot bucket width differs")
    rows, buckets = expected(
        points,
        signals,
        start,
        end,
        count,
        width,
        {s: context["timings"][s]["period_s"] for s in signals},
    )
    require(
        len(rows) <= point_limit
        and metadata["point_count"] == len(rows)
        and len(response["items"]) == len(rows),
        "Plot original-point coverage differs",
    )
    for actual, original in zip(response["items"], rows):
        require(set(actual) == set(original) | {"received_at"}, "Unexpected plot point fields")
        for key, value in original.items():
            if key == "observed_at":
                require(
                    datetime.fromisoformat(actual[key].replace("Z", "+00:00"))
                    == datetime.fromisoformat(value.replace("Z", "+00:00")),
                    "Original observed timestamp differs",
                )
            else:
                require(same_value(actual[key], value), "Original plot point differs: " + key)
        require(
            datetime.fromisoformat(actual["received_at"].replace("Z", "+00:00")).utcoffset()
            is not None,
            "Unqualified received timestamp",
        )
    require(
        equal_query({"buckets": response["buckets"]}, {"buckets": buckets}),
        "Independent plot bucket statistics differ",
    )
    totals = {
        key: sum(b[key] for b in buckets)
        for key in ("count", "good", "missing", "invalid", "continuity_breaks")
    }
    require(metadata["quality_counts"] == totals, "Plot quality totals differ")
    return {
        "passed": True,
        "original_points": len(rows),
        "original_scalar_inputs": totals["count"],
        "buckets": len(buckets),
        "all_original_values_and_extrema_and_segments_checked": True,
    }


def audit_summary(response, points, context, case, width=60):
    metadata = response["metadata"]
    for key, value in context.items():
        require(metadata.get(key) == value, "Summary scope/catalog/timing metadata differs")
    require(
        metadata["summary_version"] == "history-summary/1.0.0"
        and metadata["bucket_s"] == width
        and metadata["resolution"] == "database bucket summary"
        and metadata["from_s"] == case["from_s"]
        and metadata["to_s"] == case["to_s"],
        "Summary format/window differs",
    )
    signals = list(context["signals"])
    _, buckets = expected(
        points,
        signals,
        case["from_s"],
        case["to_s"],
        max(1, math.ceil((case["to_s"] - case["from_s"]) / width)),
        width,
        {s: context["timings"][s]["period_s"] for s in signals},
    )
    require(
        equal_query({"items": response["items"]}, {"items": buckets}), "Independent summary differs"
    )
    return {
        "passed": True,
        "buckets": len(buckets),
        "original_scalar_inputs": sum(b["count"] for b in buckets),
    }
