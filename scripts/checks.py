"""Offline checks inside the immutable-input development image."""

import base64
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def parity(folder):
    cases_path, ts_path, py_path = [folder / name for name in ("cases.json", "ts.json", "py.json")]
    run(sys.executable, "qualification/contracts/build_cases.py", "--output", str(cases_path))
    run("node", "qualification/contracts/run_typescript.cjs", str(cases_path), str(ts_path))
    run(
        sys.executable,
        "qualification/contracts/run_python.py",
        "--cases",
        str(cases_path),
        "--output",
        str(py_path),
        "--typescript-results",
        str(ts_path),
    )
    cases, py, ts = [json.loads(path.read_text()) for path in (cases_path, py_path, ts_path)]
    if not cases or not (len(cases) == len(py) == len(ts)):
        raise RuntimeError("Missing contract results")
    adapted = 0
    for case, python, typescript in zip(cases, py, ts, strict=True):
        expected = {"id": case["id"], **case["expected"]}
        for actual in (python, typescript):
            if {key: actual[key] for key in expected} != expected:
                raise RuntimeError(f"Contract expectation mismatch: {case['id']}")
        if "legacy_expected" in case and python["legacy_accepted"] != case["legacy_expected"]:
            raise RuntimeError("Original-reader expectation mismatch")
        if case.get("downgrade"):
            expected_body = json.loads(base64.b64decode(case["raw_base64"]))
            expected_body.pop("annotations", None)
            expected_body["schema_version"] = "1.0.0"
            if (
                python["downgrade"] != expected_body
                or typescript["downgrade"] != expected_body
                or not python["legacy_typescript_adapter_accepted"]
            ):
                raise RuntimeError("Adapted content or original-reader consumption differs")
            adapted += 1
    return {
        "cases": len(cases),
        "valid": sum(c["expected"]["accepted"] for c in cases),
        "rejected": sum(not c["expected"]["accepted"] for c in cases),
        "adaptation_cases": adapted,
        "mismatches": 0,
    }


def main():
    frozen = json.loads((ROOT / "qualification/contracts/immutable.json").read_text())
    for name, expected in frozen.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Immutable fixture changed: {name}")
    scope = [
        "src",
        "tests/unit",
        "tests/foundation",
        "tests/monitoring",
        "qualification/monitoring/check.py",
        "scripts/benchmark_spec.py",
        "scripts/fetch_map.py",
        "scripts/collect_inventory.py",
        "scripts/platform/montreal_probe.py",
        "scripts/platform/qualify_montreal.py",
        "scripts/monitoring",
        "scripts/known_route_energy",
        "scripts/route_aware_energy",
        "scripts/geographic",
        "scripts/check_host_clock.py",
        "scripts/checks.py",
        "scripts/dev.py",
        "scripts/configure_local.py",
        "scripts/privacy_guard.py",
        "scripts/secrets.py",
        "qualification/contracts/build_cases.py",
        "qualification/contracts/run_python.py",
    ]
    run("ruff", "check", "--no-cache", *scope)
    run("ruff", "format", "--check", "--no-cache", *scope)
    run(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/unit",
        "tests/foundation",
        "tests/monitoring",
    )
    run(
        "node",
        "packages/contracts-ts/node_modules/typescript/bin/tsc",
        "--noEmit",
        "-p",
        "packages/contracts-ts/tsconfig.json",
    )
    with tempfile.TemporaryDirectory(prefix="evdt-checks-") as directory:
        results = parity(Path(directory))
    run(
        "node",
        "--experimental-strip-types",
        "--test",
        *[str(p) for p in sorted(ROOT.glob("apps/dashboard/tests/*.test.ts"))],
    )
    run(sys.executable, "qualification/monitoring/check.py")
    run(
        "mypy",
        "--cache-dir=/tmp/mypy-cache",
        "src/evdt/monitoring/spool.py",
        "src/evdt/monitoring/spool_capacity.py",
        "src/evdt/monitoring/critical_fault.py",
        "src/evdt/monitoring/vehicle_storage.py",
        "src/evdt/monitoring/receipt_window.py",
        "src/evdt/monitoring/plant.py",
        "src/evdt/monitoring/sensors.py",
        "src/evdt/monitoring/run_policy.py",
        "src/evdt/energy/model.py",
        "src/evdt/energy/observations.py",
        "src/evdt/energy/forecasts.py",
        "src/evdt/energy/jobs.py",
        "src/evdt/energy/worker.py",
        "src/evdt/energy/partitions.py",
        "src/evdt/energy/calibration.py",
        "src/evdt/energy/route_aware",
    )
    versions = {
        "python": sys.version.split()[0],
        "node": subprocess.check_output(["node", "--version"], text=True).strip(),
        **{name: importlib.metadata.version(name) for name in ("pytest", "ruff", "jsonschema")},
        "typescript": json.loads(
            (ROOT / "packages/contracts-ts/node_modules/typescript/package.json").read_text()
        )["version"],
    }
    print(
        "EV-DT_CHECK_RESULT="
        + json.dumps({"passed": True, "contracts": results, "versions": versions})
    )


if __name__ == "__main__":
    main()
