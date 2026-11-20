"""One bounded forecast from authenticated monitoring observations; no vehicle control."""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from energy_holdout import sha, verify_release
from energy_runtime import ROOT, build_image, run_isolated

from evdt.energy.product import make_record, prepare


def compute(value, release, output, image):
    expected = sha(ROOT / "docs/models/energy-held-out-release.json")
    profile = json.loads((ROOT / "qualification/energy/product-profile-1.1.0.json").read_bytes())
    navigation_file = ROOT / profile["navigation_file"]
    navigation = None
    if value["assignment"].get("navigation_file_sha256"):
        if sha(navigation_file) != value["assignment"]["navigation_file_sha256"]:
            raise ValueError("Station navigation file changed")
        navigation = json.loads(navigation_file.read_bytes())
    prepared = prepare(value, release["model"], expected, navigation)

    def calculate(item):
        method, job = item
        result = run_isolated(image, json.dumps(job, allow_nan=False).encode())
        if result["exit_code"] or result["controller_exit_code"] or not result["policy_verified"]:
            raise RuntimeError("Isolated forecast worker failed")
        return method, json.loads(result["stdout"])

    # Independent one-request containers, each with the original isolation policy.
    # Bound aggregate worker memory to 4 * 256 MiB and preserve output order.
    jobs = prepared["jobs"]
    with ThreadPoolExecutor(max_workers=min(4, len(jobs) or 1)) as pool:
        outputs = dict(pool.map(calculate, jobs.items()))
    record = make_record(value, prepared, outputs, expected, image)
    for name, data in (("input.json", value), ("prepared.json", prepared), ("record.json", record)):
        (output / name).write_text(
            json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--credential-file", type=Path, required=True, help="Dedicated energy-input token only"
    )
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        choices=range(121),
        help="Wait at most this long for an admissible input; save unavailable on timeout",
    )
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if not output.is_relative_to(ROOT / "evidence/p2") or output.exists():
        parser.error("Use a new private evidence/p2 directory")
    for path in (args.env_file, args.credential_file):
        if not path.resolve().is_relative_to(ROOT / ".runtime"):
            parser.error("Use canonical ignored runtime configuration")
    if not args.project.startswith("evdt-") or not 1024 <= args.port <= 65535:
        parser.error("Use an EV-DT Compose project and local application port")
    release = verify_release()
    boundary = json.loads((ROOT / "docs/verification/energy-boundary.json").read_bytes())
    for path, expected in boundary["source_sha256"].items():
        if sha(ROOT / path) != expected:
            raise ValueError("Qualified worker source changed: " + path)
    manifest = json.loads((ROOT / "docs/models/energy-held-out-release.json").read_bytes())
    if (
        not manifest["offline_held_out_criteria_passed"]
        or manifest["validation_release_sha256"] != release["manifest_sha256"]
    ):
        raise ValueError("Held-out release is not compatible")
    for path, expected in manifest["evaluation_artifact_sha256"].items():
        if sha(ROOT / manifest["evaluation_path"] / path) != expected:
            raise ValueError("Held-out evidence changed")
    output.mkdir(parents=True)
    image = build_image(output)
    credential = args.credential_file.read_text().strip()
    from urllib.parse import urlencode

    print("Waiting for an admissible monitoring input...", flush=True)
    deadline = time.monotonic() + args.wait_seconds
    while True:
        with urlopen(
            Request(
                f"http://127.0.0.1:{args.port}/v1/energy-input?"
                + urlencode({"asset_id": args.asset}),
                headers={"Authorization": "Bearer " + credential},
            ),
            timeout=5,
        ) as response:
            value = json.load(response)
        if not value["reason_codes"] or time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    if value["asset_id"] != args.asset or not value["assignment"]:
        raise ValueError("Asset identity/forecast assignment is not ready")
    record = compute(value, release, output, image)
    prefix = [sys.executable, str(ROOT / "scripts/linux.py"), "--"] if os.name == "nt" else []

    def linux_path(p):
        p = p.resolve().as_posix()
        return "/mnt/" + p[0].lower() + p[2:] if os.name == "nt" else p

    result = subprocess.run(
        [
            *prefix,
            "docker",
            "compose",
            "--project-directory",
            ".",
            "--env-file",
            linux_path(args.env_file),
            "-f",
            "ops/compose/compose.monitoring.yaml",
            "-f",
            "ops/compose/compose.energy.yaml",
            "-p",
            args.project,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "forecast-writer",
        ],
        input=json.dumps(record, allow_nan=False).encode(),
        capture_output=True,
        cwd=ROOT,
        timeout=60,
    )
    (output / "save.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Forecast persistence failed; inspect private save log")
    print(json.dumps({"saved": True, "status": record["status"], "record_id": record["record_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
