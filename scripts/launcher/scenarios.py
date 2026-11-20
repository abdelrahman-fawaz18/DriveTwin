"""Verify, export, or install the separately supplied geographic scenario inputs.

No simulation, download, model evaluation, or publication is performed. Export
and install preserve byte identity and use local hard links where supported.
"""

import argparse
import json
import os
import shutil
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp

from catalog import ROOT, check, digest, load, save_catalog

VERSION = "geographic-scenario-bundle/1.0.0"


def inside(root, name):
    candidate = root / name
    path = candidate.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Scenario paths must stay inside the project")
    for entry in (candidate, *candidate.parents):
        if entry == root:
            break
        if entry.is_symlink() or entry.is_junction():
            raise ValueError("Scenario paths cannot contain redirects")
    return path


def plain_file(root, name):
    relative = PurePosixPath(str(name).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or ":" in str(name):
        raise ValueError("Scenario inputs require safe relative paths")
    path = root / name
    current = path
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise ValueError("Scenario inputs cannot contain redirected paths")
        current = current.parent
    if not path.is_file():
        raise ValueError("Required scenario input is unavailable: " + str(name))
    return path


def specification(root=ROOT):
    directory = root / "simulation/geographic"
    manifest = json.loads((directory / "manifest.json").read_bytes())
    if (
        manifest.get("version") != VERSION
        or manifest.get("id") != "geographic-v1"
        or manifest.get("destination") != ".private/operator/scenarios/geographic-v1"
        or manifest.get("catalog_sha256") != digest(directory / "catalog.json")
    ):
        raise ValueError("Unsupported or changed scenario manifest")
    for name in manifest["files"]:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Invalid scenario input name")
    return manifest


def verify(bundle, *, root=ROOT):
    bundle = inside(root, bundle)
    manifest = specification(root)
    total = 0
    for name, expected in manifest["files"].items():
        path = plain_file(bundle, name)
        if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise ValueError("Scenario input differs from its manifest: " + name)
        total += expected["bytes"]
    return {"files": len(manifest["files"]), "bytes": total, "verified": True}


def link_or_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)


def export_bundle(output, source_catalog, *, root=ROOT):
    """Match the installed catalog to portable roles, then preserve exact bytes."""
    output = inside(root, output)
    if not output.is_relative_to(root / ".private/operator") or output.exists():
        raise ValueError("Choose a new bundle directory under .private/operator")
    manifest = specification(root)
    portable = load(root / "simulation/geographic/catalog.json")
    source = {trip["id"]: trip for trip in load(inside(root, source_catalog))}
    sources = {}
    prefix = manifest["destination"] + "/"
    for trip in portable:
        if trip["id"] not in source:
            raise ValueError("The installed catalog lacks a required trip")
        for kind in ("profile", "charging_variant"):
            if kind not in trip:
                continue
            source_profile = source[trip["id"]].get(kind, {})
            original = source_profile.get("inputs", {})
            # Each profile has unique basenames; reject ambiguity instead of
            # selecting an arbitrary matching digest or a caller-supplied path.
            by_name = {Path(name).name: name for name in original}
            if len(by_name) != len(original):
                raise ValueError("Ambiguous source input inventory")
            for name, sha in trip[kind]["inputs"].items():
                old_name = by_name.get(Path(name).name)
                if old_name is None or original[old_name] != sha:
                    raise ValueError("Installed inputs differ from the portable scenario")
                path = plain_file(root, old_name)
                if digest(path) != sha:
                    raise ValueError("Installed scenario input changed")
                sources[name.removeprefix(prefix)] = path
    if set(sources) != set(manifest["files"]):
        raise ValueError("The portable input inventory is incomplete")
    output.mkdir(parents=True)
    for name, source_path in sources.items():
        link_or_copy(source_path, output / name)
    for name in ("manifest.json", "README.md"):
        shutil.copyfile(root / "simulation/geographic" / name, output / name)
    return verify(output, root=root)


def install(bundle, *, root=ROOT):
    bundle = inside(root, bundle)
    result = verify(bundle, root=root)
    manifest = specification(root)
    destination = inside(root, manifest["destination"])
    if destination.exists():
        verify(destination, root=root)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(mkdtemp(prefix=destination.name + ".partial-", dir=destination.parent))
        try:
            for name in manifest["files"]:
                link_or_copy(plain_file(bundle, name), staging / name)
            verify(staging, root=root)
            staging.rename(destination)
        except (OSError, ValueError) as error:
            raise ValueError(
                "Scenario installation stopped; partial inputs preserved at "
                + staging.relative_to(root).as_posix()
                + ". Retry with the verified bundle; the installed catalog was not replaced."
            ) from error
    trips = load(root / "simulation/geographic/catalog.json")
    for trip in trips:
        check(trip["profile"])
        if "charging_variant" in trip:
            check(trip["charging_variant"])
    save_catalog(root / ".private/operator/live-trips.json", trips)
    return {**result, "presets": len(trips), "services_started": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("verify", "install"):
        action = actions.add_parser(name)
        action.add_argument("--bundle", required=True, type=Path)
    exporting = actions.add_parser("export")
    exporting.add_argument("--output", required=True, type=Path)
    exporting.add_argument(
        "--source-catalog", type=Path, default=Path(".private/operator/live-trips.json")
    )
    args = parser.parse_args()
    try:
        if args.action == "export":
            result = export_bundle(args.output, args.source_catalog)
        elif args.action == "install":
            result = install(args.bundle)
        else:
            result = verify(args.bundle)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
