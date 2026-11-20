"""Select a fingerprinted, browser-verified local demo without publishing it."""

import argparse
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from package_demo import inventory, sha

ROOT = Path(__file__).resolve().parents[2]
VERSION = "browser-demo-current/1.0.0"


def private_path(root, name, *, scope=".private/browser-demo", must_exist=True):
    candidate = root / name
    for entry in (candidate, *candidate.parents):
        if entry == root:
            break
        if entry.is_symlink() or entry.is_junction():
            raise ValueError("A selected demo cannot use redirected paths")
    path = candidate.resolve(strict=must_exist)
    if not path.is_relative_to(root / scope):
        raise ValueError("Use a local private path inside the expected project directory")
    return path


def read_record(path):
    if path.stat().st_size > 1_048_576:
        raise ValueError("A demo receipt exceeds its metadata limit")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("A demo receipt must be a JSON object")
    return value


def site_inventory(site):
    if any(path.is_symlink() or path.is_junction() for path in site.rglob("*")):
        raise ValueError("A selected demo cannot contain redirected files")
    return inventory(site)


def select(site, verification, *, root=ROOT):
    site = private_path(root, site)
    verification = private_path(root, verification, scope=".private")
    publication = private_path(root, site.parent / (site.name + "-publication.json"))
    receipt = read_record(publication)
    report = read_record(verification)
    files = site_inventory(site)
    review = receipt.get("review")
    if (
        receipt.get("version") != "browser-demo-publication-candidate/1.0.0"
        or receipt.get("files_sha256") != files
        or receipt.get("verification_sha256") != sha(verification)
        or report.get("version") != "browser-demo-browser-check/1.0.0"
        or report.get("passed") is not True
        or report.get("fixture") is not False
        or report.get("manifest_sha256") != sha(site / "cases/montreal/manifest.json")
        or not isinstance(review, dict)
        or review.get("privacy") != "passed"
        or review.get("secret_scan") != "passed"
    ):
        raise ValueError("The site must match its successful actual-case and privacy reviews")
    current = {
        "version": VERSION,
        "site": site.relative_to(root).as_posix(),
        "verification": verification.relative_to(root).as_posix(),
        "verification_sha256": sha(verification),
        "publication_sha256": sha(publication),
        "files_sha256": files,
    }
    destination = private_path(root, ".private/browser-demo/current.json", must_exist=False)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="current-",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(current, indent=2) + "\n")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return site


def resolve_current(*, root=ROOT):
    pointer = private_path(root, ".private/browser-demo/current.json", must_exist=False)
    if pointer.exists():
        current = read_record(pointer)
        if current.get("version") != VERSION:
            raise ValueError("Unsupported current demo receipt")
        site = private_path(root, current["site"])
        verification = private_path(root, current["verification"], scope=".private")
        publication = private_path(root, site.parent / (site.name + "-publication.json"))
        if (
            current.get("files_sha256") != site_inventory(site)
            or current.get("verification_sha256") != sha(verification)
            or current.get("publication_sha256") != sha(publication)
        ):
            raise ValueError("The selected demo changed; review and select it again")
        return site
    # Older workspaces remain usable until a reviewed site is selected explicitly.
    ready = sorted((root / ".private/browser-demo").glob("preparation-*/READY.json"))
    if not ready:
        raise ValueError("No qualified demo is ready. Finish prepare_demo.py first")
    return private_path(root, read_record(private_path(root, ready[-1]))["site"])


def verification_for_site(site, *, root=ROOT):
    """Resolve the selected receipt or the original preparation's report layout."""
    site = private_path(root, site)
    pointer = private_path(root, ".private/browser-demo/current.json", must_exist=False)
    if pointer.exists():
        current = read_record(pointer)
        if current.get("version") != VERSION:
            raise ValueError("Unsupported current demo receipt")
        selected = private_path(root, current["site"])
        if selected == site:
            resolve_current(root=root)
            return private_path(root, current["verification"], scope=".private")
    return private_path(root, site.parent / "browser/browser-report.json", scope=".private")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--verification", required=True, type=Path)
    args = parser.parse_args()
    try:
        site = select(args.site, args.verification)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print("Current local demo selected:", site.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
