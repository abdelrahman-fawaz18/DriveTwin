"""Run the actual Python receiver and compiled TypeScript on shared golden vectors."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if not evidence.is_relative_to(ROOT / "evidence/p0") or evidence.exists():
        parser.error("Use a new evidence directory under evidence/p0")
    runtime = ROOT / ".runtime/p0-contracts" / uuid.uuid4().hex
    runtime.mkdir(parents=True)
    record = {
        "client_date": "2026-11-03",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "passed": False,
        "commands": [],
        "scope": "P0-04 scalar qualification profile",
    }
    log = (runtime / "commands.log").open("w", encoding="utf-8")

    def run(argv, *, cwd=ROOT, timeout=180):
        started = time.monotonic()
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        record["commands"].append(
            {
                "argv": [str(x) for x in argv],
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        log.write(json.dumps([str(x) for x in argv]) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        if result.returncode:
            raise RuntimeError(
                f"Command failed: {argv!r}: {result.stdout[-1500:]} {result.stderr[-1500:]}"
            )
        return result.stdout

    try:
        node = shutil.which("node.exe") if os.name == "nt" else shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required")
        record["node"] = run([node, "--version"]).strip()
        if not record["node"].startswith("v24."):
            raise RuntimeError("Qualify Node.js 24 first")
        package = ROOT / "packages/contracts-ts"
        npm = Path(node).parent / "node_modules/npm/bin/npm-cli.js"
        run(
            [
                node,
                str(npm),
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--cache",
                str(ROOT / ".runtime/npm-cache"),
            ],
            cwd=package,
        )
        run(
            [
                node,
                str(package / "node_modules/typescript/bin/tsc"),
                "-p",
                str(package / "tsconfig.json"),
            ]
        )
        run(
            [
                sys.executable,
                str(ROOT / "qualification/contracts/build_cases.py"),
                "--output",
                str(runtime / "cases.json"),
            ]
        )
        # Use the known Python/dependency image, but explicitly import mounted CURRENT source.
        baseline = json.loads(
            (ROOT / "evidence/p0/2026-11-03-dataflow-qualification-final.json").read_text()
        )
        image = "evdt-p0-checks:" + baseline["run_id"].replace("-", "")
        linux_relative = str(runtime.relative_to(ROOT)).replace("\\", "/")
        linux_checkout = run([sys.executable, str(ROOT / "scripts/linux.py"), "--", "pwd"]).strip()
        python_command = [
            sys.executable,
            str(ROOT / "scripts/linux.py"),
            "--timeout",
            "300",
            "--",
            "docker",
            "run",
            "--rm",
            "--user",
            "1000:1000",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:size=64m",
            "-e",
            "PYTHONPATH=/work/src",
            "-v",
            f"{linux_checkout}:/work",
            "-w",
            "/work",
            image,
        ]
        record["python_context"] = json.loads(
            run(
                [
                    *python_command,
                    "python",
                    "-c",
                    "import evdt.contracts,sys,json,importlib.metadata as m; print(json.dumps({'python':sys.version,'receiver':evdt.contracts.__file__,'jsonschema':m.version('jsonschema')}))",
                ]
            )
        )
        if record["python_context"]["receiver"] != "/work/src/evdt/contracts.py":
            raise RuntimeError("Wrong receiver implementation imported")
        run(
            [
                node,
                str(ROOT / "qualification/contracts/run_typescript.cjs"),
                str(runtime / "cases.json"),
                str(runtime / "typescript.json"),
            ],
            timeout=300,
        )
        run(
            [
                *python_command,
                "python",
                "qualification/contracts/run_python.py",
                "--cases",
                linux_relative + "/cases.json",
                "--output",
                linux_relative + "/python.json",
                "--typescript-results",
                linux_relative + "/typescript.json",
            ],
            timeout=300,
        )
        cases = json.loads((runtime / "cases.json").read_text())
        python_results = json.loads((runtime / "python.json").read_text())
        ts_results = json.loads((runtime / "typescript.json").read_text())
        if len(cases) != len(python_results) or len(cases) != len(ts_results):
            raise RuntimeError("Result count mismatch")
        discrepancies = []
        compatibility = []
        for case, py, ts in zip(cases, python_results, ts_results, strict=True):
            expected = {"id": case["id"], **case["expected"]}
            for language, outcome in (("python", py), ("typescript", ts)):
                actual = {key: outcome[key] for key in expected}
                if actual != expected:
                    discrepancies.append(
                        {
                            "id": case["id"],
                            "language": language,
                            "expected": expected,
                            "actual": actual,
                        }
                    )
            if "legacy_expected" in case and py["legacy_accepted"] != case["legacy_expected"]:
                discrepancies.append({"id": case["id"], "reason": "legacy expectation mismatch"})
            if case.get("downgrade"):
                if py["downgrade"] != ts["downgrade"]:
                    discrepancies.append({"id": case["id"], "reason": "downgrade differs"})
                compatibility.append(
                    {
                        "id": case["id"],
                        "legacy_direct": py["legacy_accepted"],
                        "legacy_after_python_adapter": True,
                        "legacy_after_typescript_adapter": py["legacy_typescript_adapter_accepted"],
                        "adapters_agree": py["downgrade"] == ts["downgrade"],
                    }
                )
        record.update(
            case_count=len(cases),
            discrepancies=discrepancies,
            compatibility=compatibility,
            accepted_cases=sum(c["expected"]["accepted"] for c in cases),
            rejected_cases=sum(not c["expected"]["accepted"] for c in cases),
        )
        record["packages"] = {
            name: json.loads((package / "node_modules" / name / "package.json").read_text())[
                "version"
            ]
            for name in ("ajv", "ajv-formats", "jsonc-parser", "typescript")
        }
        record["compiled_sha256"] = hashlib.sha256(
            (package / ".build/index.js").read_bytes()
        ).hexdigest()
        if discrepancies:
            raise RuntimeError(f"{len(discrepancies)} mismatches against explicit expected results")
        record["passed"] = True
    except BaseException as exc:
        record["error"] = repr(exc)
        print(record["error"], flush=True)
    finally:
        log.close()
        evidence.mkdir()
        for name in ("cases.json", "python.json", "typescript.json", "commands.log"):
            if (runtime / name).exists():
                shutil.copyfile(runtime / name, evidence / name)
        paths = [
            *ROOT.glob("contracts/**/*.json"),
            *ROOT.glob("catalog/*.json"),
            *ROOT.glob("packages/contracts-ts/src/*.ts"),
            *ROOT.glob("packages/contracts-ts/*.json"),
            *ROOT.glob("qualification/contracts/*.py"),
            *ROOT.glob("qualification/contracts/*.cjs"),
            ROOT / "src/evdt/contracts.py",
            ROOT / "src/evdt/contract_profile.py",
            Path(__file__).resolve(),
        ]
        record["source_sha256"] = {
            str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)
        }
        (evidence / "qualification.json").write_text(json.dumps(record, indent=2) + "\n")
        print("Evidence:", evidence.relative_to(ROOT), "passed:", record["passed"], flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
