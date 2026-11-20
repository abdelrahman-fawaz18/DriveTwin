"""Prepare the two authorized recordings, verified export, and local demo bundle.

Run from Windows PowerShell. Stops after any failed or blocked stage, preserving
all evidence. This command does not retry a bus run or publish any files.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    subprocess.run(list(args), cwd=ROOT, check=True)


def finish(protocol, output):
    from current_demo import select
    from export_case import export_case
    from package_demo import package

    candidate = output / "case"
    export_case(protocol, candidate)
    run("npm.cmd", "--prefix", "apps/dashboard", "run", "demo:build")
    checks = output / "browser"
    run(
        "node",
        "--experimental-strip-types",
        "qualification/browser/demo-check.mjs",
        "--case",
        str(candidate),
        "--output",
        str(checks),
    )
    release = output / "site"
    package(candidate, checks / "browser-report.json", release)
    select(release, checks / "browser-report.json")
    (output / "READY.json").write_text(
        json.dumps(
            {
                "ready": True,
                "site": release.relative_to(ROOT).as_posix(),
                "protocol": protocol.relative_to(ROOT).as_posix(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("\nDEMO READY: both recorded decisions verified; static site prepared.", flush=True)
    print("Next, run this from C:\\Projects\\EV-DT to view it:", flush=True)
    print(
        'python scripts/demo/serve.py --site "' + release.relative_to(ROOT).as_posix() + '"',
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol", type=Path, help="Finish an already captured successful pair; starts no bus"
    )
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Start preparation in Windows PowerShell")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    output = ROOT / (".private/browser-demo/preparation-" + stamp)
    output.mkdir(parents=True)
    if args.protocol:
        protocol = args.protocol.resolve(strict=True)
        if not protocol.is_relative_to(ROOT / "evidence/energy-v2"):
            parser.error("Use the retained private pair protocol")
    else:
        locator = output / "pair.json"
        print(
            "Two sequential one-bus recordings at 1×, followed by offline checks. Allow about 35–45 real-life minutes.",
            flush=True,
        )
        print(
            "Existing host safeguards remain enabled. Keep this terminal open; no login or charging action is needed.",
            flush=True,
        )
        run(sys.executable, "scripts/demo/record_pair.py", "--run", "--receipt", str(locator))
        protocol = ROOT / json.loads(locator.read_bytes())["protocol"]
    finish(protocol, output)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, ValueError, OSError) as error:
        print(
            "Preparation stopped. Existing recordings and partial results were preserved.",
            flush=True,
        )
        print(str(error), flush=True)
        raise SystemExit(1) from error
