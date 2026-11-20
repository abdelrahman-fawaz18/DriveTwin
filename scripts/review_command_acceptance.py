"""Audit Phase 3 evidence and record the Phase 4 entry gates."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFICATION = ROOT / "docs/verification"


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_sources(sources):
    require(bool(sources), "Missing source inventory")
    for path, expected in sources.items():
        require(sha(ROOT / path) == expected, f"Source changed after verification: {path}")


def retained(row):
    path = ROOT / (row.get("private_report") or row["private_evidence"])
    if path.is_dir():
        path /= "qualification.json"
    require(
        path.resolve().is_relative_to(ROOT / "evidence/p3"), "Unexpected retained evidence path"
    )
    require(
        sha(path) == (row.get("qualification_sha256") or row["sha256"]),
        f"Retained evidence changed: {path.name}",
    )
    report = load(path)
    require(
        report["passed"] and report["cleanup_passed"], f"Retained qualification failed: {path.name}"
    )
    return row


def assess(browser_dir, shared_path):
    browser = load(browser_dir / "qualification.json")
    shared = load(shared_path)
    require(browser["passed"] and shared["passed"], "Current browser or shared checks failed")
    check_sources(browser["frontend_sources"])
    check_sources(shared["source_sha256"])
    require(
        set(browser["cases"]) == {"confirmed", "unknown", "sealed"}, "Missing operator browser case"
    )
    for case, row in browser["cases"].items():
        folder = browser_dir / case
        service = folder / "services/qualification.json"
        playwright = folder / "results.json"
        require(
            row["passed"]
            and sha(service) == row["service_sha256"]
            and sha(playwright) == row["playwright_sha256"],
            f"Browser evidence differs: {case}",
        )
        result = load(service)
        stats = load(playwright)["stats"]
        require(
            result["passed"]
            and result["cleanup_passed"]
            and stats["expected"] == 1
            and stats["unexpected"] == 0,
            f"Browser or service failure: {case}",
        )
        check_sources({entry["path"]: entry["sha256"] for entry in result["source_files"]})

    def summary(name):
        report = load(VERIFICATION / f"p3-{name}.json")
        require(report["passed"], f"Historical checkpoint failed: {name}")
        return report

    speed = summary("live-speed-command")
    retained(speed["cases"]["commanded"])
    require(
        speed["independent_speed_effect"]["confirmed"], "No independently measured speed effect"
    )
    recovery = summary("command-recovery")["cases"]
    for case in ("before", "after", "ack"):
        row = retained(recovery[case])
        require(
            row["actuation_reservations"] == 1 and row["command_retry_and_second_handoff_checked"],
            "Interrupted command could be handed off twice",
        )
    restart = summary("live-source-restart")["cases"]
    for case in ("before_actuation", "after_actuation"):
        row = retained(restart[case])
        require(
            row["same_run_restart_refused"] and row["old_command_refused_in_fresh_run"],
            "Restart isolation failed",
        )
    require(
        recovery["ack"]["execution_state_exact_after_api_replacement_and_database_restart"],
        "Result recovery was not verified",
    )
    timing = summary("command-timing")
    for case in ("pause-02", "accelerated-01", "delivery-01"):
        require(
            retained(timing["cases"][case])["no_actuation"], "Expired command reached the actuator"
        )
    require(
        timing["rejection_audit"]["immutable_for_command_role"]
        and recovery["ack"]["payload_and_expiry_checks_passed"],
        "Admission or rejection audit failed",
    )
    arbitration = summary("live-command-arbitration")["cases"]
    for case in ("operator_wins", "thermal_fallback", "stale_fence"):
        row = retained(arbitration[case])
        require(
            row["journal_counts"]["proposals"] == 3 and row["journal_counts"]["attempts"] == 1,
            "Competing proposals produced an unexpected handoff count",
        )
    require(arbitration["stale_fence"]["unchanged_speed_trace"], "An older authority changed speed")
    for row in summary("speed-actuator-failure")["cases"].values():
        require(
            retained(row)["independent_speed_effect"]["status"] == "not_observed",
            "Actuator fault falsely confirmed",
        )
    operations = summary("command-operations")
    for group in ("pending_commands_and_mission_intent", "backup_and_restore"):
        for row in operations[group].values():
            retained(row)

    release_path = ROOT / "docs/models/stationary-advisory-release.json"
    closeout = load(VERIFICATION / "energy-advisory-closeout.json")
    release = load(release_path)
    require(
        closeout["all_original_items_passed"]
        and sha(release_path) == closeout["advisory_release_sha256"],
        "Phase 2 advisory release is unverified",
    )
    archived = ROOT / release["private_integration_evidence"]
    for name, expected in release["integration_artifact_sha256"].items():
        require(sha(archived / name) == expected, f"Phase 2 release artifact changed: {name}")
    return {
        "passed": True,
        "phase_3_complete": True,
        "browser_sha256": sha(browser_dir / "qualification.json"),
        "shared_checks_sha256": sha(shared_path),
        "criteria": {
            "P3-01": "Measured speed effect and scoped command admission",
            "P3-02": "Single reservation with restart recovery by command ID",
            "P3-03": "Interrupted handoff, uncertain outcomes and restart isolation",
            "P3-04": "Deadline and scope rejection with audit records",
            "P3-05": "Competing authority and stale token rejection",
            "P3-06": "Actuator fault with no observed effect",
        },
        "phase_4": {
            "command_integrity_passed": True,
            "advisory_release_verified": True,
            "runtime_model_qualified": release["runtime_model_qualified"],
            "station_endpoint_accuracy_qualified": release["station_endpoint_accuracy_qualified"],
            "automatic_dispatch_approved": release["automatic_dispatch_approved"],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--shared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    browser, shared, output = (p.resolve() for p in (args.browser, args.shared, args.output))
    if not (
        browser.is_relative_to(ROOT / "evidence/p3")
        and shared.is_relative_to(ROOT / ".runtime/dev-checks")
        and output.is_relative_to(ROOT / "evidence/p3")
        and not output.exists()
    ):
        parser.error("Use private evidence and a new output path under evidence/p3")
    result = assess(browser, shared)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print("Phase 3 command evidence passed the closeout review.")


if __name__ == "__main__":
    main()
