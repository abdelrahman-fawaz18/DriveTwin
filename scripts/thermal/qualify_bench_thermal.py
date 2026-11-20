"""Develop, freeze and evaluate the offline Paderborn winding model once."""

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from bench_observability import observability_study
from bench_thermal import (
    CANDIDATES,
    FEATURES,
    ROOT,
    VERSION,
    assess,
    digest,
    fit,
    inputs,
    load_profiles,
    read,
    runtime,
    source_hashes,
    write,
)

RELEASE = ROOT / "qualification/thermal/bench-winding-release-1.0.0.json"
CLAIM = ROOT / "evidence/p5/bench-winding-1.0.0-reserved-claim.json"


def limit(value, rule):
    return min(rule["cap"], max(rule["floor"], value * rule["validation_multiplier"]))


def develop(output):
    before = source_hashes()
    policy, split = inputs()
    dt = split["source"]["sample_period_s"]
    parts = load_profiles(split, ("train", "validation"))
    train, validation = parts["train"], parts["validation"]
    candidates = {}
    for name in CANDIDATES:
        candidate = fit(name, train, policy, dt)
        metrics = assess(candidate, validation, policy, dt)
        candidates[name] = {"fit": candidate, "validation": metrics}
        print(
            f"{name}: validation profile-mean MAE {metrics['mean_profile_mae_k']:.3f} K", flush=True
        )
    simple = candidates["single"]["validation"]["mean_profile_mae_k"]
    complex_mae = candidates["coupled"]["validation"]["mean_profile_mae_k"]
    selected = "coupled" if complex_mae < simple - 0.15 else "single"
    candidate = candidates[selected]["fit"]
    metrics = candidates[selected]["validation"]
    interval = policy["interval"]
    radius = min(
        interval["maximum_radius_k"],
        max(
            interval["minimum_radius_k"],
            interval["validation_multiplier"]
            * max(row["error_quantile_k"] for row in metrics["profiles_detail"]),
        ),
    )
    validation_warm = assess(candidate, validation, policy, dt, radius)
    validation_cold = assess(candidate, validation, policy, dt, radius, ambient_start=True)
    rules = policy["release_limit_rules"]
    limits = {
        key: limit(validation_warm[key], rules[key])
        for key in ("mean_profile_mae_k", "worst_profile_mae_k")
    }
    limits["minimum_mean_profile_interval_coverage"] = rules[
        "minimum_mean_profile_interval_coverage"
    ]
    sensitivity = []
    for offset in (0, 2):
        subset = [p for index, p in enumerate(train) if index % 5 != offset]
        refit = fit(selected, subset, policy, dt)
        score = assess(refit, validation, policy, dt)
        sensitivity.append(
            {
                "omitted_profiles": [p.identity for p in train[offset::5]],
                "fit": refit,
                "validation_mean_profile_mae_k": score["mean_profile_mae_k"],
            }
        )
    domain = {
        key: [
            min(float(p.features[:, i].min()) for p in train),
            max(float(p.features[:, i].max()) for p in train),
        ]
        for i, key in enumerate(FEATURES)
    }
    observer = observability_study()
    report = {
        "candidate": candidate,
        "candidates": candidates,
        "validation_warm": validation_warm,
        "validation_ambient_start": validation_cold,
        "stability_checks": sensitivity,
        "training_input_ranges": domain,
        "interval_radius_k": radius,
        "limits": limits,
        "validation_passed": all(acceptance(validation_warm, limits).values()),
        "observer": observer,
        "source_sha256": before,
        "runtime": runtime(),
        "partitions_decoded": {
            name: [p.identity for p in profiles] for name, profiles in parts.items()
        },
    }
    if source_hashes() != before:
        raise ValueError("Development sources changed during fitting")
    write(output, report)
    print(
        f"Selected {selected}. Validation pass: {report['validation_passed']}. Observer pass: {observer['passed']}."
    )


def acceptance(score, limits):
    return {
        "mean_error": score["mean_profile_mae_k"] <= limits["mean_profile_mae_k"],
        "worst_profile": score["worst_profile_mae_k"] <= limits["worst_profile_mae_k"],
        "interval_coverage": score["mean_profile_interval_coverage"]
        >= limits["minimum_mean_profile_interval_coverage"],
        "beats_persistence": score["mean_profile_mae_k"] < score["mean_persistence_mae_k"],
    }


def freeze(development):
    report = read(development)
    _, split = inputs()
    if (
        report["source_sha256"] != source_hashes()
        or report["runtime"] != runtime()
        or not report["validation_passed"]
        or not report["observer"]["passed"]
        or report["partitions_decoded"]
        != {name: split["partitions"][name]["profile_ids"] for name in ("train", "validation")}
    ):
        raise ValueError("Development evidence or required acceptance differs")
    release = {
        "version": VERSION,
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Offline Paderborn winding prediction with one measured startup value and observed forcing",
        "candidate": report["candidate"],
        "interval_radius_k": report["interval_radius_k"],
        "limits": report["limits"],
        "training_input_ranges": report["training_input_ranges"],
        "validation_mean_profile_mae_k": report["validation_warm"]["mean_profile_mae_k"],
        "validation_mean_profile_interval_coverage": report["validation_warm"][
            "mean_profile_interval_coverage"
        ],
        "development_report_sha256": digest(development),
        "source_sha256": report["source_sha256"],
        "runtime": runtime(),
        "test_profile_ids": split["partitions"]["test"]["profile_ids"],
    }
    write(RELEASE, release)
    print(f"Frozen {RELEASE.relative_to(ROOT)}. Commit it before reserved evaluation.")


def reserved(output):
    release = read(RELEASE)
    committed = subprocess.check_output(
        ["git", "show", f"HEAD:{RELEASE.relative_to(ROOT).as_posix()}"], cwd=ROOT
    )
    policy, split = inputs()
    if (
        committed != RELEASE.read_bytes()
        or release["source_sha256"] != source_hashes()
        or release["runtime"] != runtime()
        or release["test_profile_ids"] != split["partitions"]["test"]["profile_ids"]
    ):
        raise ValueError("Frozen release or source bytes differ")
    write(
        CLAIM,
        {
            "state": "reserved_attempt_consumed",
            "release_sha256": digest(RELEASE),
            "output": output.relative_to(ROOT).as_posix(),
        },
    )
    profiles = load_profiles(split, ("test",))["test"]
    dt = split["source"]["sample_period_s"]
    candidate, radius = release["candidate"], release["interval_radius_k"]
    warm = assess(candidate, profiles, policy, dt, radius)
    cold = assess(candidate, profiles, policy, dt, radius, ambient_start=True)
    checks = acceptance(warm, release["limits"])
    per_profile_outside = {}
    for p in profiles:
        per_feature = {}
        for i, key in enumerate(FEATURES):
            lo, hi = release["training_input_ranges"][key]
            per_feature[key] = int(np.sum((p.features[:-1, i] < lo) | (p.features[:-1, i] > hi)))
        per_profile_outside[p.identity] = per_feature
    if source_hashes() != release["source_sha256"]:
        raise ValueError("Evaluation sources changed while the reserved test ran")
    result = {
        "evaluation_completed": True,
        "passed": all(checks.values()),
        "checks": checks,
        "release_sha256": digest(RELEASE),
        "startup_measured": warm,
        "startup_ambient": cold,
        "outside_training_marginals": per_profile_outside,
    }
    write(output, result)
    print(f"Reserved winding evaluation completed. Passed: {result['passed']}.")
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("develop", "freeze", "reserved", "inspect"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.mode == "inspect":
        if not CLAIM.exists():
            print("No reserved evaluation has been claimed.")
            return 0
        claim = read(CLAIM)
        path = ROOT / claim["output"]
        print(f"Reserved claim: {CLAIM.relative_to(ROOT)}")
        if path.exists():
            print(f"Passed: {read(path)['passed']}. Full report: {claim['output']}")
        else:
            print(f"Evaluation incomplete. Preserve the claim and report path: {claim['output']}")
        return 0
    if args.mode in ("freeze", "reserved") and CLAIM.exists():
        parser.error("The reserved evaluation has been claimed. Use inspect.")
    if args.report is None:
        parser.error("Choose a report path under evidence/p5")
    path = args.report.resolve()
    if not path.is_relative_to(ROOT / "evidence/p5") or path.exists() != (args.mode == "freeze"):
        parser.error(
            "Use an existing development report for freeze or a new private path for a run"
        )
    if args.mode == "develop":
        develop(path)
    elif args.mode == "freeze":
        freeze(path)
    else:
        return reserved(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
