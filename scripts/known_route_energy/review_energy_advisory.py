"""Read-only Phase 2 evidence review; no collection, scoring, service or promotion."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_IDS = {
    *(f"P2-W{i:02}" for i in range(1, 7)),
    *(f"P2-{i:02}" for i in range(1, 7)),
    *(f"P2-D{i:02}" for i in range(1, 6)),
}
COLLECTION_DIFFERENCES = {
    "containers/monitoring.Dockerfile",
    "src/evdt/monitoring/api.py",
    "src/evdt/monitoring/database.py",
    "src/evdt/monitoring/http_readers.py",
    "src/evdt/monitoring/repository.py",
    "src/evdt/recovery/database.py",
}
E1_DIFFERENCES = {
    "apps/dashboard/src/EnergyForecasts.tsx",
    "apps/dashboard/src/energyModel.ts",
    "apps/dashboard/src/style.css",
    "containers/monitoring.Dockerfile",
    "scripts/energy/qualify_energy_product.py",
    "scripts/energy/run_monitoring_forecast.py",
    "src/evdt/energy/product.py",
    "src/evdt/monitoring/energy_probe.py",
    "src/evdt/monitoring/energy_views.py",
}
E1_REQUIRED_SOURCES = E1_DIFFERENCES | {
    "apps/dashboard/src/EnergyDetails.tsx",
    "apps/dashboard/src/energyExtensions.ts",
    "qualification/browser/energy-e1.config.mjs",
    "qualification/browser/energy-e1/closeout.spec.mjs",
    "qualification/energy/product-profile-1.1.0.json",
    "qualification/energy/station-navigation.json",
    "scripts/energy/build_station_navigation.py",
    "scripts/energy/energy_e1_fixture.py",
    "scripts/energy/energy_legacy_fixture.py",
    "src/evdt/energy/navigation.py",
}
E1_BROWSER_FLAGS = (
    "passed",
    "input_age_horizon_assumptions_displayed",
    "conditional_range_time_displayed",
    "two_station_assessments_displayed",
    "stale_and_disconnected_values_hidden",
    "immutable_history_visible",
    "missing_required_input_visible",
    "unsupported_cutoff_visible",
    "connection_recovery_passed",
    "new_registered_run_recovery_passed",
    "asset_and_run_state_cleared",
)
E1_GAPS = {"G-01", "G-02", "G-03"}
CHECKLIST = "qualification/energy/readiness-checklist.json"
E1_PROFILE = "qualification/energy/product-profile-1.1.0.json"
E1_RELEASE = "docs/models/stationary-advisory-release.json"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def child(root, name):
    p = (root / name).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError("Evidence path escapes its canonical root")
    return p


def check_files(root, inventory):
    for name, digest in inventory.items():
        if sha(child(root, name)) != digest:
            raise ValueError("Evidence fingerprint mismatch: " + name)
    return len(inventory)


def readiness(checklist, integrity, e1_qualified=False):
    current = checklist["version"] == "phase2-readiness-checklist/1.1.0"
    if not current and checklist["version"] != "phase2-readiness-checklist/1.0.0":
        raise ValueError("Unknown review checklist")
    items = checklist["items"]
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)) or set(ids) != EXPECTED_IDS:
        raise ValueError("Original work, acceptance or deliverable item missing/duplicated")
    gaps = [g["id"] for g in checklist["open_gaps"]]
    if len(gaps) != len(set(gaps)):
        raise ValueError("Duplicate gap")
    references = set()
    for item in items:
        if item["status"] not in ("passed", "partial", "pending", "failed"):
            raise ValueError("Unknown or unapproved deferred status")
        if (item["status"] == "passed") != (not item["gaps"]):
            raise ValueError("Incomplete item needs an explicit gap; passed items cannot hide gaps")
        references.update(item["gaps"])
    if references != set(gaps):
        raise ValueError("Gap references do not match unresolved work")
    if current:
        resolved = [g["id"] for g in checklist["resolved_gaps"]]
        if len(resolved) != len(set(resolved)) or set(resolved) != E1_GAPS or gaps:
            raise ValueError("E1 closure must account for all three original gaps")
    if (
        not checklist["promotion_withheld_until_all_items_pass"]
        or checklist["automatic_dispatch_approved"]
        or checklist["phase3_authorized"]
        or checklist["phase6_deferrals_changed"]
    ):
        raise ValueError(
            "The review cannot approve control, change scope or bypass promotion gates"
        )
    return {
        "phase_2_ready": bool(
            current
            and integrity is True
            and e1_qualified is True
            and not gaps
            and all(i["status"] == "passed" for i in items)
        ),
        "unresolved_gaps": gaps,
        "incomplete_items": [i["id"] for i in items if i["status"] != "passed"],
        "automatic_dispatch_approved": False,
        "runtime_model_flags_changed": False,
    }


def validate_e1(report):
    for section, flags in {
        "browser": E1_BROWSER_FLAGS,
        "e1_cases": (
            "passed",
            "unsupported_cutoff_visible",
            "new_registered_run_recovery",
            "retained_training_used_without_held_out_regeneration",
            "second_setup",
        ),
        "validation_preparation_parity": (
            "passed",
            "candidate_reference_values_and_envelopes_identical",
        ),
        "access": ("passed",),
        "input_credential": ("passed",),
        "database_denial": ("passed",),
        "backup": ("passed", "fresh_cluster", "exact_data_schema_permissions"),
        "legacy_backup": ("passed", "fresh_cluster", "current_read_role_and_browser_parser"),
        "cleanup": ("passed", "owned_application_image_removed"),
        "monitoring_views": ("map_read_passed",),
    }.items():
        if not all(report[section][flag] is True for flag in flags):
            raise ValueError("E1 acceptance failed or incomplete: " + section)
    if (
        report["passed"] is not True
        or not 0 < report["startup_s"] <= 120
        or report["accounting"]
        != {"records": 3, "available": 2, "unavailable": 1, "original_packets_intact": 183}
        or report["browser"]["page_errors"] != 0
        or report["e1_cases"]["second_asset_missing_input"] != 61
        or report["e1_cases"]["original_packets_or_clocks_rewritten"] is not False
        or report["validation_preparation_parity"]["validation_cases"] != 6
        or report["validation_preparation_parity"]["held_out_opened"] is not False
        or report["access"]["checks"] != 6
        or report["input_credential"]["checks"] != 5
        or report["database_denial"]["checks"] != 10
        or report["backup"]["forecast_rows"] != 3
        or report["backup"]["original_packets"] != 183
        or report["legacy_backup"]["exact_original_payloads"] != 2
        or report["legacy_backup"]["original_packets_intact"] != 61
        or report["legacy_backup"]["old_records_rewritten"] is not False
        or report["monitoring_views"]["snapshot_signals"] != 20
        or report["monitoring_views"]["raw_history_readings"] != 61
    ):
        raise ValueError("E1 acceptance counts, scope or authority do not match")


def inspect_e1(root, legacy_profile, legacy_full):
    release = load(root / E1_RELEASE)
    if release["version"] != "phase2-advisory-release/1.0.0" or any(
        release[k] is not False
        for k in (
            "automatic_dispatch_approved",
            "runtime_model_qualified",
            "phase3_authorized",
            "station_endpoint_accuracy_qualified",
        )
    ):
        raise ValueError("Advisory release domain or authority changed")
    check_files(
        root,
        {
            CHECKLIST: release["checklist_sha256"],
            E1_PROFILE: release["product_profile_sha256"],
            "docs/models/energy-held-out-release.json": release["held_out_release_sha256"],
        },
    )
    profile = load(root / E1_PROFILE)
    if profile["version"] != "known-route-energy-product/1.1.0" or any(
        profile[k] != v for k, v in legacy_profile.items() if k not in ("version", "scope")
    ):
        raise ValueError("Active product limits differ from the reviewed legacy profile")
    navigation = child(root, profile["navigation_file"])
    if sha(navigation) != profile["navigation_file_sha256"]:
        raise ValueError("Active navigation file changed")
    nav = load(navigation)
    nav_content = json.dumps(nav, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if (
        hashlib.sha256(nav_content).hexdigest() != profile["navigation_content_sha256"]
        or nav["mission_id"] != profile["mission_id"]
        or nav["physical_steps"] != 0
        or nav["vehicles"] != 0
        or nav["vehicle_class"] != "bus"
        or len(nav["stations"]) != 2
        or len(nav["roads"]) != 105
        or any(
            s["hypothetical"] is not True
            or s["availability"] != "unverified"
            or not 0 <= s["snap_distance_m"] <= nav["maximum_station_snap_m"]
            for s in nav["stations"]
        )
    ):
        raise ValueError("Reviewed static station approach scope changed")
    check_files(
        root,
        {
            "scripts/energy/build_station_navigation.py": nav["generator_sha256"],
            "data/public/montreal-core-v1/roads.osm.xml": nav["input_sha256"]["roads.osm.xml"],
            **{
                f"simulation/montreal/{p}": nav["input_sha256"][p]
                for p in ("mission.json", "stations.json", "import-review.json")
            },
        },
    )
    folder = child(root, release["private_integration_evidence"])
    if not folder.is_relative_to(root / "evidence/p2"):
        raise ValueError("Use the canonical private Phase 2 evidence root")
    expected_artifacts = {
        "qualification.json",
        "browser/result.json",
        "cleanup-inventory.json",
        "database.dump",
        "legacy-read.json",
        "prediction/record.json",
        "recovery-prediction/record.json",
        "unavailable/record.json",
    }
    if set(release["integration_artifact_sha256"]) != expected_artifacts:
        raise ValueError("E1 evidence inventory is incomplete")
    check_files(folder, release["integration_artifact_sha256"])
    report = load(folder / "qualification.json")
    validate_e1(report)
    if not E1_REQUIRED_SOURCES <= set(report["source_sha256"]):
        raise ValueError("E1 report omits current product sources")
    check_files(root, report["source_sha256"])
    cleanup = load(folder / "cleanup-inventory.json")
    if (
        cleanup["before"] != cleanup["after"]
        or report["browser"] != load(folder / "browser/result.json")
        or sha(legacy_full / "database.dump") != report["legacy_backup"]["legacy_archive_sha256"]
    ):
        raise ValueError("E1 recorded browser, cleanup or legacy archive differs")
    return report


def inspect(root):
    reports = {
        name: load(root / "docs/verification" / f"p2-energy-{name}.json")
        for name in ("advisory", "boundary", "calibration", "heldout", "product")
    }
    exact = {}
    for name, value in reports.items():
        if value["passed"] is not True:
            raise ValueError("Prior checkpoint did not pass: " + name)
        if name == "product":
            continue  # D1 is verified against its retained reports below.
        sources = value.get("source_sha256") or {
            item["path"]: item["sha256"] for item in value["source_files"]
        }
        exact[name] = check_files(root, sources)
    current_numeric = load(
        root / reports["advisory"]["private_numerical_regression"] / "qualification.json"
    )
    if (
        not current_numeric["passed"]
        or len(current_numeric["cases"]) != 9
        or current_numeric["model_version"] != "longitudinal-energy-estimate/1.1.0"
    ):
        raise ValueError("Current numerical regression is incomplete")
    exact["current_numerical"] = check_files(root, current_numeric["source_sha256"])
    b2 = load(root / "docs/models/energy-validation-release.json")
    c1 = load(root / "docs/models/energy-held-out-release.json")
    if sha(root / "docs/models/energy-validation-release.json") != c1["validation_release_sha256"]:
        raise ValueError("Historical validation manifest changed")
    check_files(root, b2["source_sha256"])
    check_files(child(root, b2["analysis_path"]), b2["analysis_artifact_sha256"])
    analysis_report = load(child(root, b2["analysis_path"]) / "qualification.json")
    if not analysis_report["passed"]:
        raise ValueError("Actual frozen analysis did not pass")
    check_files(root, analysis_report["source_sha256"])
    check_files(child(root, c1["evaluation_path"]), c1["evaluation_artifact_sha256"])
    evaluation = load(child(root, c1["evaluation_path"]) / "evaluation.json")
    if (
        evaluation["completed"] is not True
        or evaluation["held_out_criteria_met"] is not True
        or evaluation["summary"] != c1["summary"]
        or evaluation["summary"] != reports["heldout"]["evaluation"]
    ):
        raise ValueError("Reviewed metrics do not match the sealed completed evaluation")
    profile = load(root / "qualification/energy/product-profile.json")
    if (
        profile["release_sha256"] != sha(root / "docs/models/energy-held-out-release.json")
        or profile["cutoff_sim_s"] != 120
        or profile["automatic_dispatch_approved"]
        or profile["model_qualified"]
    ):
        raise ValueError("Product domain or authority changed after its qualification")
    claim = load(root / ".runtime/p2-held-out" / (c1["validation_release_sha256"] + ".json"))
    if claim["state"] != "scored" or not claim["passed"]:
        raise ValueError("Original one-time evaluation claim is not the completed pass")
    if not all(reports["heldout"]["evaluation"]["checks"].values()):
        raise ValueError("A frozen held-out criterion did not pass")
    product = reports["product"]
    full = load(root / product["full_integration_private_evidence"] / "qualification.json")
    ui = load(root / product["focused_presentation_private_evidence"] / "qualification.json")
    artifacts = product["artifact_sha256"]
    if (
        sha(root / product["full_integration_private_evidence"] / "qualification.json")
        != artifacts["full_qualification"]
        or sha(root / product["focused_presentation_private_evidence"] / "qualification.json")
        != artifacts["presentation_qualification"]
    ):
        raise ValueError("Product evidence report changed")
    if not all(r["passed"] and r["cleanup"]["passed"] for r in (full, ui)):
        raise ValueError("Product integration or cleanup failed")
    product_sources = {r["path"]: r["sha256"] for r in product["source_files"]}
    if product_sources != ui["source_sha256"] or set(product_sources) != set(full["source_sha256"]):
        raise ValueError("Historical D1 source inventories differ")
    differences = {p for p, h in full["source_sha256"].items() if product_sources[p] != h}
    if differences != {"apps/dashboard/src/style.css", "scripts/energy/qualify_energy_product.py"}:
        raise ValueError("Unexpected full-integration source changes")
    exact["historical_D1_source_inventory"] = len(product_sources)
    e1 = None
    if (root / E1_RELEASE).is_file():
        e1 = inspect_e1(root, profile, root / product["full_integration_private_evidence"])
        current_sources = e1["source_sha256"]
        for historical in (full, ui):
            old_sources = historical["source_sha256"]
            current_diff = {p for p, h in old_sources.items() if sha(child(root, p)) != h}
            if current_diff != E1_DIFFERENCES or not current_diff <= set(current_sources):
                raise ValueError("Unreviewed D1-to-E1 source changes")
        exact["current_E1"] = len(current_sources)
    else:
        check_files(root, product_sources)
        current_sources = product_sources
    collection_counts = {"train": 0, "validation": 0, "held_out": 0}
    file_count = byte_count = 0
    collection_compatibility = []
    for base, held in (
        (root / reports["calibration"]["private_collection"], False),
        (root / reports["heldout"]["private_evidence"], True),
    ):
        q = load(base / "qualification.json")
        if not q["passed"]:
            raise ValueError("Original mission collection did not pass")
        changes = {p for p, h in q["source_sha256"].items() if sha(child(root, p)) != h}
        if changes != COLLECTION_DIFFERENCES:
            raise ValueError("Unreviewed collection source differences")
        check_files(root, {p: current_sources[p] for p in changes})
        collection_compatibility.append(
            {
                "exact": len(q["source_sha256"]) - len(changes),
                "reviewed_changed_files": sorted(changes),
                "basis": "Product/database/auth/runtime-stage changes; simulator and collector algorithm bytes unchanged. Current changed bytes match the applicable product qualification. Recorded packets are not regenerated.",
            }
        )
        folder = base / "collection"
        data = load(folder / "dataset.json")
        if not data["passed"]:
            raise ValueError("Dataset inventory not accepted")
        for m in data["missions"]:
            report_path = child(folder, m["mission_id"] + "/collection.json")
            if sha(report_path) != m["collection_sha256"]:
                raise ValueError("Mission report digest changed")
            report = load(report_path)
            if not report["passed"]:
                raise ValueError("Mission incomplete")
            check_files(report_path.parent, report["files_sha256"])
            for name in report["files_sha256"]:
                file_count += 1
                byte_count += child(report_path.parent, name).stat().st_size
            partition = "held_out" if held else m["partition"]
            collection_counts[partition] += 1
    if collection_counts != {"train": 12, "validation": 6, "held_out": 6} or file_count != 288:
        raise ValueError("Expected complete 24-mission inventory")
    repeat = (
        root
        / reports["calibration"]["private_collection"]
        / "collection/repeatability/collection.json"
    )
    check_files(repeat.parent, load(repeat)["files_sha256"])
    return {
        "evidence_integrity_passed": True,
        "exact_checkpoint_sources": exact,
        "current_numerical_profiles": 9,
        "original_numerical_1_0_0_is_historical": True,
        "collection_counts": collection_counts,
        "primary_collection_files_verified": file_count,
        "primary_collection_bytes_verified": byte_count,
        "repeatability_files_verified": len(load(repeat)["files_sha256"]),
        "collection_compatibility": collection_compatibility,
        "full_product_report_preserved_with_two_reviewed_differences": True,
        "D1_presentation_source_inventory_verified": len(ui["source_sha256"]),
        "current_e1_qualified": e1 is not None,
        "D1_files_requalified_by_E1": sorted(E1_DIFFERENCES) if e1 else [],
        "frozen_B2_C1_artifacts_and_consumed_claim_unchanged": True,
        "new_collection_scoring_or_workload_started": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if not output.is_relative_to(ROOT / "evidence/p2") or output.exists():
        parser.error("Use a new directory under evidence/p2")
    output.mkdir(parents=True)
    checklist_path = ROOT / CHECKLIST
    before = {
        "scripts/energy/review_energy_advisory.py": sha(Path(__file__)),
        CHECKLIST: sha(checklist_path),
    }
    if (ROOT / E1_RELEASE).is_file():
        before[E1_RELEASE] = sha(ROOT / E1_RELEASE)
    report = {"audit_completed": False, "evidence_integrity_passed": False, "phase_2_ready": False}
    try:
        report.update(inspect(ROOT))
        report.update(
            readiness(
                load(checklist_path),
                report["evidence_integrity_passed"],
                report["current_e1_qualified"],
            )
        )
        report["audit_completed"] = True
        check_files(ROOT, before)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report.update(
            audit_completed=False,
            evidence_integrity_passed=False,
            phase_2_ready=False,
            error=str(exc),
        )
    report["review_source_sha256"] = before
    (output / "review.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"Review completed: {report['audit_completed']}; evidence integrity: {report['evidence_integrity_passed']}; Phase 2 ready: {report['phase_2_ready']}"
    )
    print("Report: " + output.relative_to(ROOT).as_posix())
    return 0 if report["audit_completed"] and report["evidence_integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
