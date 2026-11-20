"""Validate/export existing browser evidence; does not rerun or rewrite test results."""

import argparse
import base64
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = {"windows-edge-visible": "edge", "chromium-automated": "chromium"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve_bytes(path, data):
    if path.exists():
        if path.read_bytes() != data:
            if path.suffix != ".json" or json.loads(path.read_bytes()) != json.loads(data):
                raise RuntimeError(f"Existing evidence differs: {path}")
    else:
        with path.open("xb") as output:
            output.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--prerequisite",
        type=Path,
        default=Path("evidence/p0/2026-11-03-p0-02-acceptance-p004.json"),
    )
    args = parser.parse_args()
    folder, destination = args.evidence_dir.resolve(), args.report.resolve()
    if (
        not folder.is_relative_to(ROOT / "evidence/p0")
        or not destination.is_relative_to(ROOT / "evidence/p0")
        or destination.exists()
    ):
        parser.error("Use project evidence and a new acceptance report filename")
    record = json.loads((folder / "qualification.json").read_text())
    if not record["passed"] or record["test_summary"]["expected"] != 2:
        raise RuntimeError("Browser qualification did not pass")
    for name, expected in record["source_sha256"].items():
        if digest(ROOT / name) != expected:
            raise RuntimeError(f"Qualified source changed: {name}")
    prerequisite = (ROOT / args.prerequisite).resolve()
    if not prerequisite.is_relative_to(ROOT / "evidence/p0"):
        raise RuntimeError("Prerequisite must be project-local evidence")
    prior = json.loads(prerequisite.read_text())
    if not prior["passed"]:
        raise RuntimeError("P0-02 prerequisite not passed")
    for name, expected in prior["source_sha256"].items():
        if digest(ROOT / name) != expected:
            raise RuntimeError(f"Prerequisite source changed: {name}")
    results = json.loads((folder / "browser/results.json").read_text())
    versions = []
    seen = set()
    for suite in results["suites"]:
        for spec in suite.get("specs", []):
            for test in spec["tests"]:
                name = test["projectName"]
                if (
                    name not in PROJECTS
                    or test["status"] != "expected"
                    or len(test["results"]) != 1
                ):
                    raise RuntimeError("Unexpected browser test, retry, or outcome")
                seen.add(name)
                prefix = PROJECTS[name]
                for attachment in test["results"][0]["attachments"]:
                    if attachment["name"] not in (
                        "browser-environment-and-network",
                        "browser-read-history",
                    ):
                        continue
                    data = json.loads(base64.b64decode(attachment["body"]))
                    suffix = (
                        "network"
                        if attachment["name"] == "browser-environment-and-network"
                        else "history"
                    )
                    if suffix == "network":
                        if data["platform"] != "win32" or any(
                            data[key] for key in ("unexpected", "failures", "consoleErrors")
                        ):
                            raise RuntimeError("Browser environment/network errors")
                        versions.append(
                            {
                                key: data[key]
                                for key in ("project", "platform", "node", "browserVersion")
                            }
                        )
                    elif data["run_id"] != record["run_id"] or len(data["items"]) != 3:
                        raise RuntimeError("Browser history/run mismatch")
                    preserve_bytes(
                        folder / f"{prefix}-{suffix}.json",
                        (json.dumps(data, indent=2) + "\n").encode(),
                    )
                directory = next((folder / "browser/test-artifacts").glob("*" + name))
                for source, target in (
                    ("stored-readings.png", f"{prefix}-stored-readings.png"),
                    ("trace.zip", f"{prefix}-trace.zip"),
                ):
                    preserve_bytes(folder / target, (directory / source).read_bytes())
                if (
                    not (folder / f"{prefix}-stored-readings.png")
                    .read_bytes()
                    .startswith(b"\x89PNG\r\n\x1a\n")
                ):
                    raise RuntimeError("Invalid screenshot export")
                with zipfile.ZipFile(folder / f"{prefix}-trace.zip") as trace:
                    if trace.testzip() or not any(
                        name.endswith(".trace") for name in trace.namelist()
                    ):
                        raise RuntimeError("Invalid trace export")
    if seen != set(PROJECTS):
        raise RuntimeError("A required browser project is missing")
    if any(record["fixture"]["cleanup"].values()):
        raise RuntimeError("Owned resources remain")
    artifact_paths = [
        folder / "qualification.json",
        folder / "browser/results.json",
        prerequisite,
        *[
            folder / f"{prefix}-{suffix}"
            for prefix in PROJECTS.values()
            for suffix in ("network.json", "history.json", "stored-readings.png", "trace.zip")
        ],
    ]
    accepted = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "gate": "P0-03",
        "passed": True,
        "status": "passed",
        "run_id": record["run_id"],
        "browsers": versions,
        "test_summary": record["test_summary"],
        "checks": {
            "ubuntu_host_http": True,
            "windows_host_http": True,
            "visible_windows_edge": True,
            "pinned_automated_chromium": True,
            "real_database_values_and_run_identity": True,
            "refresh_and_empty_query": True,
            "screenshots_and_traces_exported": True,
            "owned_containers_volumes_networks_removed": True,
        },
        "source_sha256": {
            **prior["source_sha256"],
            **record["source_sha256"],
            "scripts/platform/accept_browser.py": digest(Path(__file__).resolve()),
        },
        "artifacts": [
            {"path": str(p.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(p)}
            for p in artifact_paths
        ],
        "scope_limits": [
            "Diagnostic synthetic-history page; not a live monitoring dashboard.",
            "Browser tests ran on Windows. Linux browser execution and other browser families remain unqualified.",
            "No SUMO-to-dashboard integration, live streaming, complete UI coverage, operational security, or workload/resource-target claim.",
            "Normal cleanup passed; interrupted runs and host power loss remain unqualified.",
        ],
    }
    with destination.open("x", encoding="utf-8") as output:
        json.dump(accepted, output, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                "gate": "P0-03",
                "passed": True,
                "browsers": versions,
                "source_files_verified": len(accepted["source_sha256"]),
                "artifacts_verified": len(accepted["artifacts"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
