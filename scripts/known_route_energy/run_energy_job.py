"""Run one prepared private energy job through the qualified isolated worker."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from energy_runtime import ROOT, build_image, fingerprints, run_isolated

sys.path.insert(0, str(ROOT / "src"))
from evdt.energy.jobs import MAX_JOB_BYTES, decode_job  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    path = args.job.resolve()
    output = args.evidence_dir.resolve()
    if not path.is_relative_to(ROOT / ".runtime") and not path.is_relative_to(ROOT / "evidence/p2"):
        parser.error("Job must be under ignored .runtime or evidence/p2")
    if output.exists() or not output.is_relative_to(ROOT / "evidence/p2"):
        parser.error("Choose a new evidence/p2 directory")
    with path.open("rb") as stream:
        raw = stream.read(MAX_JOB_BYTES + 1)
    decode_job(raw)
    output.mkdir(parents=True)
    before = fingerprints()
    report = {
        "passed": False,
        "scope": "One isolated advisory calculation; no model accuracy or live authentication claim",
    }
    try:
        image_id = build_image(output)
        result = run_isolated(image_id, raw)
        report.update(result=result, image_id=image_id, job_sha256=hashlib.sha256(raw).hexdigest())
        report["passed"] = result["exit_code"] == result["controller_exit_code"] == 0
    except Exception as exc:
        report["error"] = str(exc)
    report["source_sha256"] = before
    if before != fingerprints():
        report.update(passed=False, error="Worker source changed during calculation")
    (output / "qualification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Isolated job passed: {report['passed']}; report: {output.relative_to(ROOT)}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
