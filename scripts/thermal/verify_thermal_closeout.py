"""Verify retained Phase 5 evidence without generating a new reserved evaluation."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence/p5"
THERMAL = ROOT / "qualification/thermal"
SUMMARIES = ROOT / "docs/verification"
STUDIES = (
    (
        "bench-winding",
        "bench-thermal",
        "bench-winding-1.0.0-reserved-claim.json",
        "bench-development-03.json",
    ),
    (
        "synthetic-advisory",
        "synthetic-advisory",
        "synthetic-advisory-1.0.0-reserved-claim.json",
        "synthetic-advisory-development-01.json",
    ),
    (
        "synthetic-intervention",
        "thermal-intervention",
        "thermal-intervention-reserved-1.0.0.claim.json",
        "thermal-intervention-dev-final/result.json",
    ),
)


def read(path):
    return json.loads(path.read_bytes())


def digest(content):
    return hashlib.sha256(content).hexdigest()


def verify_bytes(path, expected):
    if digest(path.read_bytes()) != expected:
        raise ValueError(f"Fingerprint mismatch for {path.relative_to(ROOT)}")


def verify_study(study, summary_name, claim_name, development_name):
    release_path = THERMAL / f"{study}-release-1.0.0.json"
    summary = read(SUMMARIES / f"p5-{summary_name}.json")
    verify_bytes(release_path, summary["release_sha256"])
    release = read(release_path)
    source_files = release["source_sha256"]
    for path, expected in {
        release_path.relative_to(ROOT).as_posix(): summary["release_sha256"],
        **source_files,
    }.items():
        verify_bytes(ROOT / path, expected)
        committed = subprocess.check_output(
            ["git", "show", f"{summary['freeze_commit']}:{path}"], cwd=ROOT
        )
        if digest(committed) != expected:
            raise ValueError(f"{study} freeze commit differs for {path}")
    verify_bytes(EVIDENCE / development_name, release["development_report_sha256"])
    claim_path = EVIDENCE / claim_name
    verify_bytes(claim_path, summary["reserved_claim_sha256"])
    claim = read(claim_path)
    output = (ROOT / claim["output"]).resolve()
    if not output.is_relative_to(EVIDENCE):
        raise ValueError(f"{study} claim points outside the evidence folder")
    report_path = output / "result.json" if output.is_dir() else output
    verify_bytes(report_path, summary["reserved_report_sha256"])
    report = read(report_path)
    if (
        claim["state"] != "reserved_attempt_consumed"
        or claim["release_sha256"] != summary["release_sha256"]
        or report["release_sha256"] != summary["release_sha256"]
        or report["passed"] is not True
        or summary["passed"] is not True
        or report["checks"] != summary["checks"]
        or not all(passed is True for passed in report["checks"].values())
    ):
        raise ValueError(f"{study} claim, report, or summary disagrees")
    return release, report


def main():
    (bench_release, bench), (advisory_release, advisory), (intervention_release, intervention) = (
        verify_study(*study) for study in STUDIES
    )
    timing_path = EVIDENCE / "synthetic-advisory-rtamt-01.json"
    verify_bytes(timing_path, advisory_release["temporal_report_sha256"])
    timing = read(timing_path)
    if (
        timing["passed"] is not True
        or timing["development_report_sha256"] != advisory_release["development_report_sha256"]
    ):
        raise ValueError("The independent advisory timing result disagrees")
    profiles = bench["startup_measured"]["profiles"]
    cases = advisory["metrics"]["case_count"]
    trace_hashes = intervention["trace_sha256"]
    if (
        profiles != len(bench_release["test_profile_ids"])
        or cases != advisory_release["reserved_case_count"]
        or intervention["scenarios"] != intervention_release["reserved_case_count"]
        or len(trace_hashes) != intervention["scenarios"] * len(intervention["arms"])
    ):
        raise ValueError("A reserved inventory disagrees with its freeze")
    for filename, expected in trace_hashes.items():
        verify_bytes(EVIDENCE / "thermal-intervention-reserved-1.0.0" / filename, expected)
    print(
        f"Verified 3 Phase 5 freezes and retained evaluations: {profiles} bench profiles, "
        f"{cases} advisory cases, {len(trace_hashes)} intervention traces."
    )


if __name__ == "__main__":
    main()
