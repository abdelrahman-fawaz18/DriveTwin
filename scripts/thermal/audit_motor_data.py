"""Inventory the original motor CSV without inspecting temperature outcomes."""

import argparse
import csv
import hashlib
import io
import json
import math
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 384 * 1024 * 1024
SIGNALS = {
    "ambient",
    "coolant",
    "u_d",
    "u_q",
    "i_d",
    "i_q",
    "motor_speed",
    "torque",
    "pm",
    "stator_winding",
    "stator_tooth",
    "stator_yoke",
}
CADENCE_S = 0.5  # Publisher declaration. This CSV does not carry a time column.


def sha(stream):
    return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(path, member):
    rows = 0
    counts = Counter()
    invalid = Counter(dict.fromkeys(SIGNALS, 0))
    fingerprints = {}
    closed = set()
    previous = None
    with zipfile.ZipFile(path) as archive:
        matches = [entry for entry in archive.infolist() if entry.filename == member]
        if len(matches) != 1 or not 0 < matches[0].file_size <= MAX_BYTES:
            raise ValueError("Expected one explicitly selected, bounded CSV member")
        with (
            archive.open(matches[0]) as raw,
            io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text,
        ):
            reader = csv.reader(text, strict=True)
            columns = next(reader)
            if len(columns) != len(SIGNALS) + 1 or set(columns) != SIGNALS | {"profile_id"}:
                raise ValueError("Unreviewed CSV columns")
            profile_index = columns.index("profile_id")
            signal_indices = [
                (index, name) for index, name in enumerate(columns) if name != "profile_id"
            ]
            for row in reader:
                rows += 1
                if len(row) != len(columns):
                    raise ValueError(f"Malformed row width at sample {rows}")
                profile = int(row[profile_index])
                if profile != previous:
                    if profile in closed:
                        raise ValueError(
                            f"A profile ID reappeared after its session block at sample {rows}"
                        )
                    if previous is not None:
                        closed.add(previous)
                    fingerprints[profile] = hashlib.sha256()
                    previous = profile
                counts[profile] += 1
                for index, name in signal_indices:
                    try:
                        valid = math.isfinite(float(row[index]))
                    except ValueError:
                        valid = False
                    if not valid:
                        invalid[name] += 1
                # Hash the complete ordered reading sequence, excluding the session ID.
                # Do not report temperature values, ranges, or feature/target statistics.
                readings = "\x1f".join(row[index] for index, _ in signal_indices)
                fingerprints[profile].update((readings + "\n").encode())
        if not counts:
            raise ValueError("No motor samples found")
        with archive.open(matches[0]) as raw:
            member_hash = sha(raw)
    with path.open("rb") as raw:
        archive_hash = sha(raw)
    digests = Counter(item.hexdigest() for item in fingerprints.values())
    return {
        "archive_bytes": path.stat().st_size,
        "archive_sha256": archive_hash,
        "csv_member": member,
        "csv_bytes": matches[0].file_size,
        "csv_sha256": member_hash,
        "columns": columns,
        "samples": rows,
        "profiles": len(counts),
        "publisher_sample_period_s": CADENCE_S,
        "explicit_timestamp_present": False,
        "recorded_hours_at_declared_cadence": rows * CADENCE_S / 3600,
        "minimum_profile_samples": min(counts.values()),
        "maximum_profile_samples": max(counts.values()),
        "invalid_values_by_signal": dict(sorted(invalid.items())),
        "exact_duplicate_session_groups_excluding_ids": sum(n > 1 for n in digests.values()),
        "profile_samples": dict(sorted(counts.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--member", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    path, output = args.archive.resolve(), args.output.resolve()
    if not path.is_relative_to(ROOT / "data/downloads/p5-motor") or not output.is_relative_to(
        ROOT / "evidence/p5"
    ):
        parser.error("Use the ignored Phase 5 download and evidence folders")
    if output.exists() or not 0 < path.stat().st_size <= MAX_BYTES:
        parser.error("Use a fresh report and a bounded original archive")
    result = audit(path, args.member)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(
        f"{result['samples']:,} samples in {result['profiles']} profiles. Audit: {output.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
