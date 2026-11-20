"""Standard-library audit of actual browser JSON/CSV downloads against source bytes."""

import base64
import csv
import hashlib
import json
import math
from datetime import datetime


def require(condition, message):
    if not condition:
        raise ValueError(message)


def same_value(actual, expected):
    if type(expected) in (int, float):
        return type(actual) in (int, float) and actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            same_value(actual[k], v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            same_value(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def originals(checkpoint, asset, signals, start, end):
    directory = checkpoint / "sources" / asset
    summary = json.loads((directory / "source-summary.json").read_bytes())
    records, digest, count = [], hashlib.sha256(), 0
    with (directory / "originals.jsonl").open("rb") as stream:
        while line := stream.readline(100000):
            require(len(line) < 100000 and line.endswith(b"\n"), "Truncated original history")
            encoded = json.loads(line)
            raw = base64.b64decode(encoded["raw_base64"], validate=True)
            body = json.loads(raw)
            require(
                hashlib.sha256(raw).hexdigest() == encoded["wire_sha256"]
                and all(body[k] == v for k, v in summary["identity"].items())
                and body["stream_seq"] == count
                and body["event_id"] == encoded["event_id"],
                "Original download reference differs",
            )
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
            count += 1
            if start <= body["sim_time_s"] <= end:
                for sample in body["samples"]:
                    if sample["signal_id"] in signals:
                        records.append(
                            {
                                **{
                                    k: body[k]
                                    for k in (
                                        "run_id",
                                        "asset_id",
                                        "event_id",
                                        "source_id",
                                        "source_epoch",
                                        "stream_seq",
                                        "sim_time_s",
                                        "observed_at",
                                        "schema_version",
                                        "catalog_version",
                                        "manifest_sha256",
                                    )
                                },
                                "signal_id": sample["signal_id"],
                                "sample": sample,
                            }
                        )
    require(
        count == summary["events"] == summary["expected_events"]
        and digest.hexdigest() == summary["original_packets_sha256"],
        "Incomplete original download reference",
    )
    return sorted(records, key=lambda r: (r["sim_time_s"], r["event_id"], r["signal_id"]))


def instant(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    require(value.utcoffset() is not None, "Unqualified download timestamp")
    return value


def validate_metadata(metadata, expected, fixture, asset):
    require(
        all(
            type(metadata.get(k)) is int
            for k in ("row_count", "page_limit", "page_count", "download_row_limit")
        ),
        "Download integer metadata has invalid types",
    )
    require(
        expected
        and metadata["run_id"] == fixture["run_id"]
        and metadata["asset_id"] == asset
        and metadata["catalog_version"] == "1.4.0"
        and metadata["export_version"] == "1.1.0"
        and metadata["resolution"] == "raw"
        and metadata["from_s"] == fixture["from_s"]
        and metadata["to_s"] == fixture["to_s"]
        and metadata["download_complete"] is True
        and metadata["row_count"] == len(expected) == fixture["expected_rows"]
        and metadata["page_limit"] == 1000
        and metadata["page_count"] == math.ceil(len(expected) / 1000)
        and metadata["download_row_limit"] == 100000
        and set(metadata["signals"]) == set(fixture["signals"]),
        "Downloaded scope/version/count/page metadata differs",
    )
    return validate_snapshot(metadata, expected, fixture, asset)


def validate_raw_metadata(metadata, expected, fixture, asset):
    require(
        expected
        and len(expected) == fixture["expected_rows"]
        and type(metadata.get("row_limit")) is int
        and same_value(
            metadata,
            {
                "export_version": "1.0.0",
                "mode": "simulation",
                "run_id": fixture["run_id"],
                "asset_id": asset,
                "as_of": fixture["as_of"],
                "from_s": fixture["from_s"],
                "to_s": fixture["to_s"],
                "time_basis": "simulation seconds",
                "policy_sha256": fixture["policy_sha256"],
                "resolution": "raw",
                "signals": fixture["definitions"],
                "row_limit": 10000,
            },
        ),
        "Raw export scope/version/limit metadata differs",
    )
    return validate_snapshot(metadata, expected, fixture, asset)


def validate_snapshot(metadata, expected, fixture, asset):
    cursor = metadata["as_of"].split(":")
    require(
        len(cursor) == 3
        and cursor[:2] == [fixture["run_id"], asset]
        and cursor[2].isdigit()
        and int(cursor[2]) > 0,
        "Download snapshot differs",
    )
    units = {r["signal_id"]: r["sample"]["unit"] for r in expected}
    require(
        all(metadata["signals"][s]["unit"] == unit for s, unit in units.items()),
        "Downloaded unit definitions differ",
    )
    if "definitions" in fixture:
        require(
            same_value(metadata["signals"], fixture["definitions"]),
            "Downloaded complete signal definitions differ",
        )
    return int(cursor[2])


def validate_json(data, expected, fixture, asset, *, metadata_validator=validate_metadata):
    metadata = data["metadata"]
    revision = metadata_validator(metadata, expected, fixture, asset)
    require(len(data["items"]) == len(expected), "Downloaded JSON scalar count differs")
    for actual, original in zip(data["items"], expected):
        for key, value in original.items():
            require(
                (instant(actual[key]) == instant(value))
                if key == "observed_at"
                else same_value(actual[key], value),
                "Downloaded JSON original differs: " + key,
            )
        instant(actual["received_at"])
        require(
            type(actual["dashboard_revision"]) is int
            and 0 < actual["dashboard_revision"] <= revision,
            "Downloaded JSON revision differs",
        )
    return metadata


def validate_csv(stream, expected, fixture, asset, *, metadata_validator=validate_metadata):
    reader = csv.DictReader(stream)
    fields = [
        "record_type",
        "metadata_json",
        "event_id",
        "signal_id",
        "unit",
        "sim_time_s",
        "observed_at",
        "received_at",
        "source_id",
        "source_epoch",
        "stream_seq",
        "quality",
        "value",
        "raw_value",
        "reason",
        "provenance",
        "manifest_sha256",
        "schema_version",
        "catalog_version",
    ]
    require(reader.fieldnames == fields, "Downloaded CSV header differs")
    row = next(reader)
    require(
        row["record_type"] == "metadata" and all(row[k] == "" for k in fields[2:]),
        "CSV metadata record differs",
    )
    metadata = json.loads(row["metadata_json"])
    metadata_validator(metadata, expected, fixture, asset)
    count = 0
    for row, original in zip(reader, expected, strict=True):
        sample = original["sample"]
        require(
            row["record_type"] == "sample" and row["metadata_json"] == "",
            "Unexpected CSV record type",
        )
        for key in (
            "event_id",
            "signal_id",
            "source_id",
            "manifest_sha256",
            "schema_version",
            "catalog_version",
        ):
            require(
                row[key] == str(original[key]), "Downloaded CSV identity/provenance differs: " + key
            )
        for key in ("source_epoch", "stream_seq"):
            require(int(row[key]) == original[key], "Downloaded CSV sequence differs")
        require(
            float(row["sim_time_s"]) == original["sim_time_s"]
            and instant(row["observed_at"]) == instant(original["observed_at"]),
            "Downloaded CSV time differs",
        )
        instant(row["received_at"])
        for key in ("unit", "quality", "reason", "provenance"):
            require(
                row[key] == (sample.get(key, "good" if key == "quality" else "") or ""),
                "Downloaded CSV sample metadata differs: " + key,
            )
        for key in ("value", "raw_value"):
            value = sample.get(key)
            require(
                row[key] == ""
                if value is None
                else math.isfinite(float(row[key])) and float(row[key]) == value,
                "Downloaded CSV original numeric value differs",
            )
        count += 1
    require(count == len(expected), "Downloaded CSV count differs")
    return metadata


def audit(checkpoint, downloads, fixture, browser):
    profile = json.loads((checkpoint / "artifacts/contracts/monitoring.profile.json").read_bytes())
    catalog = json.loads((checkpoint / "artifacts" / profile["catalogs"]["1.4.0"]).read_bytes())
    fixture = {**fixture, "definitions": {s: catalog["signals"][s] for s in fixture["signals"]}}
    result = {}
    for asset in fixture["assets"]:
        expected = originals(
            checkpoint, asset, fixture["signals"], fixture["from_s"], fixture["to_s"]
        )
        metadata = []
        for extension in ("json", "csv"):
            path = downloads / f"{browser}-{asset}.{extension}"
            require(
                path.is_file() and 0 < path.stat().st_size <= 64 * 1024**2,
                "Browser download absent or too large",
            )
            if extension == "json":
                metadata.append(
                    validate_json(json.loads(path.read_bytes()), expected, fixture, asset)
                )
            else:
                with path.open("r", encoding="utf-8", newline="") as stream:
                    metadata.append(validate_csv(stream, expected, fixture, asset))
        require(metadata[0] == metadata[1], "JSON and CSV frozen metadata differ")
        result[asset] = {
            "passed": True,
            "rows_per_format": len(expected),
            "pages_per_format": metadata[0]["page_count"],
            "all_original_values_units_identity_quality_and_provenance_checked": True,
        }
    return {"passed": True, "assets": result, "browser": browser}
