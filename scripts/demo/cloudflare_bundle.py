"""Prepare a fingerprint-checked Cloudflare Pages upload; never publish or start a bus."""

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from current_demo import (  # noqa: E402
    private_path,
    read_record,
    resolve_current,
    verification_for_site,
)
from package_demo import ROOT, inventory, review_site, sha  # noqa: E402

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
    "media-src 'self' blob:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)
HEADERS = f"""/*
  Content-Security-Policy: {CSP}
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: no-referrer
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()

/
  Cache-Control: no-cache
/index.html
  Cache-Control: no-cache
/demo
  Cache-Control: no-cache
/demo.html
  Cache-Control: no-cache
/cases/*
  Cache-Control: no-cache
/assets/*
  Cache-Control: public, max-age=31536000, immutable
"""
NOT_FOUND = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Page not found · DriveTwin</title>
<style>body{font:18px system-ui;color:#243044;background:#f5f6fa;margin:10vh auto;
max-width:38rem;padding:2rem}a{color:#096f78}</style>
<h1>Page not found</h1><p>This address does not point to a demo page.</p>
<a href="/">Open the recorded demo</a></html>
"""


def validate_candidate(site, *, root=ROOT):
    """Require the original actual-case receipt and the entire unchanged file set."""
    site = private_path(root, site)
    if any(p.is_symlink() or p.is_junction() for p in [site, *site.rglob("*")]):
        raise ValueError("An upload candidate cannot contain redirected paths")
    publication = private_path(root, site.parent / (site.name + "-publication.json"))
    receipt = read_record(publication)
    review = receipt.get("review")
    if (
        receipt.get("version") != "browser-demo-publication-candidate/1.0.0"
        or not isinstance(review, dict)
        or review.get("privacy") != "passed"
        or review.get("secret_scan") != "passed"
    ):
        raise ValueError("A reviewed publication candidate is required")
    if inventory(site) != receipt.get("files_sha256"):
        raise ValueError("Upload candidate changed: unexpected, missing, or modified files")
    report_path = verification_for_site(site, root=root)
    report = read_record(report_path)
    if (
        sha(report_path) != receipt.get("verification_sha256")
        or report.get("version") != "browser-demo-browser-check/1.0.0"
        or report.get("fixture") is not False
        or report.get("passed") is not True
        or report.get("manifest_sha256") != receipt.get("manifest_sha256")
        or sha(site / "cases/montreal/manifest.json") != receipt.get("manifest_sha256")
    ):
        raise ValueError("The successful actual-case browser verification must match")
    return receipt


def prepare(site, output, *, root=ROOT):
    site = private_path(root, site)
    output = private_path(root, output, must_exist=False)
    receipt = validate_candidate(site, root=root)
    if output.is_relative_to(site) or site.is_relative_to(output):
        raise ValueError("Source and upload directories must not overlap")
    if output.exists():
        raise ValueError("Use a new upload directory; existing candidates are preserved")
    output.mkdir(parents=True)
    upload = output / "site"
    shutil.copytree(site, upload)
    if inventory(upload) != receipt["files_sha256"]:
        raise ValueError("Upload candidate changed during copying")
    (upload / "_headers").write_text(HEADERS, encoding="utf-8", newline="\n")
    # Pages must return a genuine 404 for missing case files, not the SPA index.
    (upload / "404.html").write_text(NOT_FOUND, encoding="utf-8", newline="\n")
    files = inventory(upload)
    if len(files) > 1000 or any(
        p.stat().st_size > 25 * 1024**2 for p in upload.rglob("*") if p.is_file()
    ):
        raise ValueError("Upload exceeds Cloudflare Pages drag-and-drop limits")
    review = review_site(upload)
    archive = output / "drivetwin-demo.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in files:
            bundle.write(upload / name, name)
    # Verify the archive itself, including root layout and absence of extra files.
    with zipfile.ZipFile(archive) as bundle:
        archived = {
            name: hashlib.sha256(bundle.read(name)).hexdigest() for name in bundle.namelist()
        }
        if archived != files:
            raise ValueError("Upload archive differs from its reviewed file inventory")
    result = {
        "version": "browser-demo-cloudflare-upload/1.0.0",
        "provider": "Cloudflare Pages Direct Upload",
        "case_id": receipt["case_id"],
        "files_sha256": files,
        "source_publication_sha256": sha(site.parent / (site.name + "-publication.json")),
        "archive_sha256": sha(archive),
        "archive_bytes": archive.stat().st_size,
        "review": review,
        "status": "Prepared locally; not uploaded or publicly verified",
    }
    (output / "upload-receipt.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site", type=Path, help="Reviewed private site; defaults to the selected current demo"
    )
    parser.add_argument("--output", type=Path, help="New ignored upload directory")
    args = parser.parse_args()
    try:
        site = private_path(ROOT, args.site) if args.site else resolve_current(root=ROOT)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    try:
        output = private_path(
            ROOT,
            args.output or ROOT / (".private/browser-demo/cloudflare-" + stamp),
            must_exist=False,
        )
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    private = ROOT / ".private/browser-demo"
    if not site.is_relative_to(private) or not output.is_relative_to(private) or output == private:
        parser.error("Keep preparation and upload candidates in ignored browser-demo storage")
    try:
        result = prepare(site, output)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(f"Cloudflare upload prepared: {output / 'drivetwin-demo.zip'}")
    print(
        f"{len(result['files_sha256'])} files; {result['archive_bytes']:,} ZIP bytes. "
        "No upload performed."
    )
    print(
        "Verify locally: node qualification/browser/demo-hosting-check.mjs --bundle " + str(output)
    )


if __name__ == "__main__":
    main()
