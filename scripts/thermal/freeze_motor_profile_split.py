"""Build or verify the reviewed whole-session partition using mechanical inputs only."""

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict

from audit_motor_data import CADENCE_S, ROOT, sha

AUDIT = ROOT / "docs/verification/motor-data-audit.json"
ARCHIVE = ROOT / "data/downloads/p5-motor/paderborn.zip"
MANIFEST = ROOT / "qualification/thermal/motor-profile-split-1.0.0.json"
BUCKET_ROWS = 10
WEIGHTS = {"train": 3, "validation": 1, "test": 1}


def collect_profiles(audit):
    with ARCHIVE.open("rb") as stream:
        if sha(stream) != audit["archive_sha256"]:
            raise ValueError("Original archive differs from the Phase 5 audit")
    counts = defaultdict(int)
    summaries = defaultdict(list)
    profile = None
    speeds, torques = [], []
    with (
        zipfile.ZipFile(ARCHIVE) as archive,
        archive.open(audit["csv_member"]) as raw,
        io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream,
    ):
        reader = csv.reader(stream, strict=True)
        header = next(reader)
        profile_col, speed_col, torque_col = map(
            header.index, ("profile_id", "motor_speed", "torque")
        )
        for row in reader:
            current = int(row[profile_col])
            if current != profile:
                if current in counts:
                    raise ValueError("A profile is split into multiple source blocks")
                profile = current
                speeds, torques = [], []
            counts[profile] += 1
            speeds.append(float(row[speed_col]))
            torques.append(float(row[torque_col]))
            if len(speeds) == BUCKET_ROWS:
                summaries[profile].append((sum(speeds) / BUCKET_ROWS, sum(torques) / BUCKET_ROWS))
                speeds, torques = [], []
    if len(counts) != audit["profiles"] or sum(counts.values()) != audit["samples"]:
        raise ValueError("Whole-profile inventory differs from the Phase 5 audit")
    return counts, summaries


def similar(a, b):
    """Screen for aligned near-repeats of the full speed and torque trajectory."""
    for shift in range(-2, 3):
        start_a, start_b = max(0, shift), max(0, -shift)
        overlap = min(len(a) - start_a, len(b) - start_b)
        if overlap < 0.95 * max(len(a), len(b)):
            continue
        speed_error, torque_error = 0.0, 0.0
        for i in range(overlap):
            speed_a, torque_a = a[start_a + i]
            speed_b, torque_b = b[start_b + i]
            speed_error += abs(speed_a - speed_b)
            torque_error += abs(torque_a - torque_b)
        if speed_error <= 75 * overlap and torque_error <= 5 * overlap:
            return True
    return False


def group_profiles(counts, summaries):
    group_of = {profile: {profile} for profile in counts}
    screened = 0
    matches = []
    for a in sorted(counts):
        for b in sorted(counts):
            if b <= a or abs(counts[a] - counts[b]) > 0.02 * max(counts[a], counts[b]):
                continue
            screened += 1
            if similar(summaries[a], summaries[b]):
                matches.append([a, b])
                group = group_of[a] | group_of[b]
                for profile in group:
                    group_of[profile] = group
    groups = sorted({tuple(sorted(group)) for group in group_of.values()})
    return groups, screened, matches


def build():
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    counts, summaries = collect_profiles(audit)
    groups, screened, matches = group_profiles(counts, summaries)
    partitions = {name: {"profile_ids": [], "samples": 0} for name in WEIGHTS}
    for group in sorted(groups, key=lambda ids: (-sum(counts[i] for i in ids), ids)):
        name = min(WEIGHTS, key=lambda name: partitions[name]["samples"] / WEIGHTS[name])
        partitions[name]["profile_ids"].extend(group)
        partitions[name]["samples"] += sum(counts[i] for i in group)
    assigned = []
    for partition in partitions.values():
        partition["profile_ids"].sort()
        partition["recorded_hours"] = round(partition["samples"] * CADENCE_S / 3600, 4)
        assigned.extend(partition["profile_ids"])
    if sorted(assigned) != sorted(counts) or any(not p["profile_ids"] for p in partitions.values()):
        raise ValueError("Every complete session must belong to exactly one nonempty partition")
    return {
        "version": "motor-profile-split/1.0.0",
        "scope": "Paderborn testbench temperature prediction",
        "source": {
            "archive_sha256": audit["archive_sha256"],
            "csv_sha256": audit["csv_sha256"],
            "csv_member": audit["csv_member"],
            "sample_period_s": CADENCE_S,
        },
        "assignment": "descending group duration, least samples per target weight, ties in partition order",
        "target_weights": WEIGHTS,
        "related_session_screen": {
            "inputs": ["motor_speed", "torque"],
            "mean_bucket_samples": BUCKET_ROWS,
            "maximum_duration_difference_fraction": 0.02,
            "minimum_aligned_overlap_fraction": 0.95,
            "maximum_alignment_shift_buckets": 2,
            "maximum_speed_mean_absolute_difference_rpm": 75,
            "maximum_torque_mean_absolute_difference_nm": 5,
            "comparable_pairs_screened": screened,
            "matching_profile_pairs": matches,
            "groups_kept_together": [group for group in groups if len(group) > 1],
        },
        "partitions": partitions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true", help="Write the first manifest once")
    args = parser.parse_args()
    result = build()
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.freeze:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with MANIFEST.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
    elif MANIFEST.read_text(encoding="utf-8") != encoded:
        raise ValueError("Rebuilt profile assignment differs from the frozen manifest")
    for name, partition in result["partitions"].items():
        print(
            f"{name}: {len(partition['profile_ids'])} profiles, {partition['recorded_hours']:.2f} h"
        )
    screen = result["related_session_screen"]
    print(
        f"Screened {screen['comparable_pairs_screened']} pairs and kept {len(screen['groups_kept_together'])} groups together."
    )


if __name__ == "__main__":
    main()
