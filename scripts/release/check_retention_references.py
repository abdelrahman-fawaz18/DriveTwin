"""Refuse a cleanup plan that removes existing files explicitly cited by source."""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = re.compile(r"(?:\.runtime|evidence|\.private|data/downloads)/[A-Za-z0-9_./-]+")


def conflicts(root, plan, sources):
    root = root.resolve()
    removed = {}
    for item in plan["remove"]:
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("A cleanup target escapes the project")
        removed[path.relative_to(root).as_posix().casefold()] = item["path"]
    found = []
    for name in sources:
        source = root / name
        if not source.is_file() or source.stat().st_size > 2_000_000:
            continue
        raw = source.read_bytes()
        if b"\0" in raw:
            continue
        try:
            lines = raw.decode("utf-8-sig").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, 1):
            for match in REFERENCE.finditer(line):
                path = (root / match[0].rstrip("./")).resolve()
                if not path.is_relative_to(root):
                    continue
                target = removed.get(path.relative_to(root).as_posix().casefold())
                if target is not None and path.is_file():
                    found.append({"path": target, "source": name, "line": line_number})
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args()
    plan = (ROOT / args.plan).resolve(strict=True)
    if not plan.is_relative_to(ROOT) or plan.stat().st_size > 16 * 1024**2:
        parser.error("Use a bounded cleanup inventory inside the project")
    sources = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    matches = conflicts(ROOT, json.loads(plan.read_bytes()), [name for name in sources if name])
    print(json.dumps({"passed": not matches, "referenced_removals": matches}, indent=2))
    return 1 if matches else 0


if __name__ == "__main__":
    raise SystemExit(main())
