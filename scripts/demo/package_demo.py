"""Assemble a local static release candidate after actual-case browser checks.

No files are uploaded, no remote is changed, and no hosting is configured.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory):
    return {
        p.relative_to(directory).as_posix(): sha(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def review_site(directory):
    sys.path.insert(0, str(ROOT / "scripts"))
    from privacy_guard import inspect_content, local_identities

    review_directory = ROOT / ".private/browser-demo/site-reviews" / uuid4().hex
    review_directory.mkdir(parents=True)
    findings = []
    identities = local_identities()
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("A static candidate cannot contain redirected files")
        if not path.is_file():
            continue
        name = path.relative_to(directory).as_posix()
        reviewed_path = (
            "apps/dashboard/src/ui/assets/InterVariable.woff2"
            if name.startswith("assets/InterVariable-") and name.endswith(".woff2")
            else name
        )
        findings.extend(
            {"file": name, "rule": rule}
            for rule in inspect_content(path.read_bytes(), identities, path=reviewed_path)
        )
    report_path = review_directory / "privacy.json"
    report_path.write_text(json.dumps({"findings": findings}, indent=2) + "\n", encoding="utf-8")
    if findings:
        raise ValueError(
            "Static candidate privacy review failed; inspect the private review report"
        )
    # Reuse the pinned installer/verifier, not an arbitrary PATH executable.
    subprocess.run(
        [sys.executable, "scripts/secrets.py", "install"], cwd=ROOT, check=True, capture_output=True
    )
    lock = json.loads((ROOT / "environments/secret-scanner.json").read_bytes())
    scanner = (
        ROOT
        / ".runtime/tools/gitleaks"
        / lock["version"]
        / ("gitleaks.exe" if os.name == "nt" else "gitleaks")
    )
    secret_report = review_directory / "secrets.json"
    result = subprocess.run(
        [
            str(scanner),
            "--redact=100",
            "--no-banner",
            "--exit-code=1",
            "--report-format=json",
            f"--report-path={secret_report}",
            "dir",
            str(directory),
        ],
        cwd=ROOT,
        capture_output=True,
    )
    if result.returncode:
        (review_directory / "secrets.log").write_bytes(result.stdout + result.stderr)
        raise ValueError("Static candidate secret review failed; inspect the private review report")
    return {
        "privacy": "passed",
        "secret_scan": "passed",
        "scanner_version": lock["version"],
        "review_id": review_directory.name,
    }


def package(case, verification, output):
    report = json.loads(verification.read_bytes())
    if (
        report.get("version") != "browser-demo-browser-check/1.0.0"
        or report.get("fixture") is not False
        or not report.get("passed")
    ):
        raise ValueError(
            "A successful actual-case browser check is required; fixtures cannot be released"
        )
    manifest = json.loads((case / "manifest.json").read_bytes())
    if report["manifest_sha256"] != sha(case / "manifest.json"):
        raise ValueError("Case changed after browser verification")
    if report.get("build_sha256") != inventory(ROOT / "apps/dashboard/dist-demo"):
        raise ValueError("The browser-tested application build changed")
    if output.exists():
        raise ValueError("Use a new private release-candidate directory")
    if any(p.is_symlink() for p in case.rglob("*")):
        raise ValueError("Case cannot contain redirected files")
    files = {"manifest.json"}
    for descriptor in manifest["files"].values():
        name = descriptor["path"]
        if Path(name).name != name or name in files:
            raise ValueError("Invalid case asset name")
        path = case / name
        if path.stat().st_size != descriptor["bytes"] or sha(path) != descriptor["sha256"]:
            raise ValueError("Case asset changed after verification")
        files.add(name)
    shutil.copytree(ROOT / "apps/dashboard/dist-demo", output)
    destination = output / "cases/montreal"
    destination.mkdir(parents=True)
    for name in sorted(files):
        shutil.copyfile(case / name, destination / name)
    notices = output / "notices"
    notices.mkdir()
    for name, source in {
        "DriveTwin": "LICENSE",
        "React": "apps/dashboard/node_modules/react/LICENSE",
        "Leaflet": "apps/dashboard/node_modules/leaflet/LICENSE",
        "Inter": "apps/dashboard/src/ui/assets/Inter-LICENSE.txt",
    }.items():
        shutil.copyfile(ROOT / source, notices / (name + ".txt"))
    # A directory URL works on static hosts without a rewrite rule.
    shutil.copyfile(output / "demo.html", output / "index.html")
    review = review_site(output)
    receipt = {
        "version": "browser-demo-publication-candidate/1.0.0",
        "case_id": manifest["id"],
        "manifest_sha256": sha(case / "manifest.json"),
        "files_sha256": inventory(output),
        "status": "Local candidate; public destination and upload await explicit review",
        "verification_sha256": sha(verification),
        "review": review,
    }
    (output.parent / (output.name + "-publication.json")).write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".private"):
        parser.error("Release candidates stay in ignored .private storage until publication review")
    package(args.case.resolve(strict=True), args.verification.resolve(strict=True), output)
    print("Local static candidate prepared. No publication was performed.")


if __name__ == "__main__":
    main()
