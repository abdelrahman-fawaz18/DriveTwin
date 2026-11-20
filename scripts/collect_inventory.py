"""Export declared dependency terms without author/account data or full license text."""

import argparse
import importlib.metadata
import json
from pathlib import Path


def python_packages():
    entries = []
    for distribution in importlib.metadata.distributions():
        metadata = distribution.metadata
        declared = metadata.get("License-Expression")
        if not declared:
            classifiers = [
                c.rsplit(" :: ", 1)[-1]
                for c in metadata.get_all("Classifier", [])
                if c.startswith("License ::")
            ]
            license_field = metadata.get("License", "")
            declared = " / ".join(classifiers) or (
                license_field if len(license_field) < 200 else "See installed license files"
            )
        notices = [
            str(p)
            for p in distribution.files or []
            if any(word in str(p).upper() for word in ("LICENSE", "COPYING", "NOTICE"))
        ]
        entries.append(
            {
                "name": metadata["Name"],
                "version": metadata["Version"],
                "declared_terms": declared or "NOT-DECLARED",
                "license_expression": metadata.get("License-Expression"),
                "installed_notice_paths": notices,
                "metadata_source": f"https://pypi.org/project/{metadata['Name']}/{metadata['Version']}/",
            }
        )
    return sorted(entries, key=lambda item: item["name"].lower())


def npm_packages(folder):
    lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
    entries = []
    for name, entry in lock["packages"].items():
        if not name:
            continue
        package = name.split("node_modules/")[-1]
        notices = []
        location = folder / name
        if location.resolve().is_relative_to(folder.resolve()) and location.is_dir():
            notices = [
                p.name
                for p in location.iterdir()
                if any(word in p.name.upper() for word in ("LICENSE", "COPYING", "NOTICE"))
            ]
        entries.append(
            {
                "name": package,
                "version": entry["version"],
                "declared_terms": entry.get("license", "NOT-DECLARED"),
                "installed_notice_files": sorted(notices),
                "metadata_source": f"https://www.npmjs.com/package/{package}/v/{entry['version']}",
            }
        )
    return sorted(entries, key=lambda item: item["name"].lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    entries = npm_packages(args.npm) if args.npm else python_packages()
    result = {
        "scope": "Installed metadata or exact npm lock; not complete binary redistribution clearance",
        "packages": entries,
    }
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
