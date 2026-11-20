"""Offline analytic energy checks; no plant connection or held-out accuracy claim."""

import argparse
import hashlib
import json
import math
import platform
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.energy.model import (  # noqa: E402
    MODEL_VERSION,
    DrivingInterval,
    EnergyParameters,
    estimate_energy,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if not output.is_relative_to(ROOT / "evidence/p2") or output.exists():
        parser.error("Choose a new directory under evidence/p2")
    output.mkdir(parents=True)
    inputs = ROOT / "qualification/energy/core-cases.json"
    paths = [
        ROOT / "src/evdt/energy/model.py",
        ROOT / "src/evdt/energy/__init__.py",
        Path(__file__),
        inputs,
    ]

    def fingerprints():
        return {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }

    before = fingerprints()
    record = {
        "passed": False,
        "checkpoint": "P2-A1 numerical foundation",
        "scope": "Analytical reference profiles; not calibrated mission accuracy or runtime isolation",
        "phase_2_complete": False,
        "model_version": MODEL_VERSION,
        "python": platform.python_version(),
        "cases": [],
    }
    try:
        data = json.loads(inputs.read_bytes())
        if data["version"] != "energy-core-analytic-cases/1.0.0":
            raise ValueError("Unknown analytic case format")
        identifiers = [case["id"] for case in data["cases"]]
        if len(identifiers) != len(set(identifiers)) or not all(
            isinstance(name, str) and name for name in identifiers
        ):
            raise ValueError("Analytic case identifiers must be unique nonempty strings")
        for case in data["cases"]:
            parameters = EnergyParameters(
                **{**data["base_parameters"], **case["parameter_overrides"]}
            )
            plan = tuple(DrivingInterval(**p) for p in case["plan"])
            result = asdict(
                estimate_energy(parameters, plan, case["initial_soc"], step_s=case["step_s"])
            )
            for key, expected in case["expected"].items():
                actual = result[key]
                valid = (
                    actual is expected
                    if isinstance(expected, bool)
                    else isinstance(actual, (int, float))
                    and not isinstance(actual, bool)
                    and math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8)
                )
                if not valid:
                    raise ArithmeticError("Analytic expectation failed: " + case["id"] + "." + key)
            record["cases"].append({"id": case["id"], "passed": True, "estimate": result})
        record["passed"] = len(record["cases"]) == len(data["cases"]) > 0
    except Exception as exc:
        record["error"] = str(exc)
    record["source_sha256"] = before
    if fingerprints() != before:
        record.update(passed=False, error="Source or case inputs changed during qualification")
    (output / "qualification.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        f"Energy core passed: {record['passed']}; cases: {len(record['cases'])}; report: {output}"
    )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
