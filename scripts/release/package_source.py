"""Create a private, byte-verified source snapshot from a clean Git commit."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from privacy_guard import inspect_content, local_identities, private_path  # noqa: E402

PREFIX = "EV-DT/"
ACCEPTANCE_REVIEW = "docs/verification/release-safety-review.json"
RELEASE_INPUTS = (
    "uv.lock",
    "apps/dashboard/package-lock.json",
    "packages/contracts-ts/package-lock.json",
    "qualification/browser/package-lock.json",
    "environments/dataflow-images.json",
    "environments/engineering-images.json",
    "environments/secret-scanner.json",
    "data/public/montreal-core-v1/source.json",
    "qualification/release/simulation.json",
    ACCEPTANCE_REVIEW,
)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, timeout=60)


def committed_files(commit):
    result = {}
    for entry in git("ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, identity = metadata.decode().split()
        name = path.decode("utf-8")
        if kind != "blob" or mode not in {"100644", "100755"} or private_path(name):
            raise ValueError(f"Unapproved Git entry: {name}")
        result[name] = identity
    return result


def verify_archive(path, files, object_format):
    identities = local_identities()
    remaining = set(files)
    inputs = {}
    with ZipFile(path) as archive:
        for entry in archive.infolist():
            relative = entry.filename.removeprefix(PREFIX)
            if (
                not entry.filename.startswith(PREFIX)
                or ".." in PurePosixPath(relative).parts
                or "\\" in relative
            ):
                raise ValueError("Unexpected archive path")
            if entry.is_dir():
                continue
            if relative not in remaining or entry.file_size > 2_000_000:
                raise ValueError(f"Unexpected or oversized archive file: {relative}")
            data = archive.read(entry)
            if inspect_content(data, identities, path=relative):
                raise ValueError(f"Private or unreviewed archive content: {relative}")
            digest = hashlib.new(object_format, f"blob {len(data)}\0".encode() + data)
            if digest.hexdigest() != files[relative]:
                raise ValueError(f"Archive bytes differ from Git: {relative}")
            remaining.remove(relative)
            if relative in RELEASE_INPUTS:
                inputs[relative] = hashlib.sha256(data).hexdigest()
    if remaining:
        raise ValueError("Archive omitted committed files")
    if set(inputs) != set(RELEASE_INPUTS):
        raise ValueError("Archive omitted a release lock or source record")
    return dict(sorted(inputs.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="New label in the private releases folder")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", args.name):
        parser.error("Use lowercase letters, digits, and hyphens")
    if git("status", "--porcelain", "-z"):
        raise SystemExit("Commit or otherwise resolve working-tree changes before packaging")
    parent = ROOT / ".private/releases"
    output = parent / args.name
    if output.resolve() != output or output.exists():
        raise SystemExit("Choose a new private release folder")
    commit = git("rev-parse", "HEAD").decode().strip()
    object_format = git("rev-parse", "--show-object-format").decode().strip()
    files = committed_files(commit)
    acceptance = json.loads(git("show", f"{commit}:{ACCEPTANCE_REVIEW}"))["release_acceptance"]
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=parent) as tmp:
        work = Path(tmp)
        archive = work / "EV-DT-source.zip"
        git("archive", "--format=zip", "--prefix=" + PREFIX, "--output=" + str(archive), commit)
        inputs = verify_archive(archive, files, object_format)
        manifest = {
            "manifest_version": "evdt-source-package/1.0.0",
            "kind": "source snapshot",
            "commit": commit,
            "scope_profile": "qualification/release/simulation.json",
            "release_evidence": "docs/release/verification.md",
            "acceptance_review": ACCEPTANCE_REVIEW,
            "release_acceptance": acceptance,
            "rights_inventory": "docs/rights/README.md",
            "archive": archive.name,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "verified_files": len(files),
            "reproduction_inputs_sha256": inputs,
        }
        (work / "source-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        work.rename(output)
    print(f"Verified {len(files)} source files in {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
