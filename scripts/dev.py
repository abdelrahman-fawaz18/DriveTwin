"""One Windows/Linux entry point for reproducible engineering checks."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_hashes():
    paths = [
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / ".dockerignore",
        *ROOT.glob("ops/compose/*.yaml"),
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("tests/unit/*.py"),
        *ROOT.glob("tests/foundation/*.py"),
        *ROOT.glob("tests/monitoring/*.py"),
        *ROOT.glob("benchmarks/*.json"),
        *ROOT.glob("simulation/**/*.json"),
        *ROOT.glob("scripts/**/*.py"),
        *ROOT.glob("qualification/contracts/*.*"),
        *ROOT.glob("qualification/monitoring/*.*"),
        *ROOT.glob("qualification/workload/*.json"),
        *ROOT.glob("qualification/energy/**/*.json"),
        *ROOT.glob("qualification/dashboard/*.json"),
        *ROOT.glob("contracts/**/*.json"),
        *ROOT.glob("catalog/*.json"),
        ROOT / "data/public/montreal-core-v1/roads.osm.xml",
        *ROOT.glob("data/public/montreal-backdrop-v1/*"),
        *ROOT.glob("packages/contracts-ts/src/*.ts"),
        *ROOT.glob("packages/contracts-ts/*.json"),
        *ROOT.glob("apps/dashboard/src/**/*"),
        *ROOT.glob("apps/dashboard/tests/*"),
        *ROOT.glob("apps/dashboard/*.json"),
        *ROOT.glob("apps/launcher/*"),
        *ROOT.glob("qualification/browser/*.mjs"),
        *(
            path
            for folder in ("dashboard", "energy", "energy-e1", "tests")
            for path in ROOT.glob(f"qualification/browser/{folder}/*.mjs")
        ),
        ROOT / "DriveTwin.cmd",
        ROOT / "apps/dashboard/index.html",
        ROOT / "apps/dashboard/replay.html",
        ROOT / "apps/dashboard/vite.config.mjs",
        *ROOT.glob("migrations/monitoring/*.sql"),
        ROOT / "containers/checks.Dockerfile",
        ROOT / "containers/energy-worker.Dockerfile",
        ROOT / "containers/energy-route-worker.Dockerfile",
        ROOT / "environments/energy-geospatial-requirements.txt",
        ROOT / "environments/engineering-images.json",
        ROOT / "environments/dataflow-images.json",
        ROOT / "environments/secret-scanner.json",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(paths))
        if p.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check"])
    args = parser.parse_args()
    del args
    folder = ROOT / ".runtime/dev-checks" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    report = {
        "passed": False,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "source/contract/privacy checks; not application or model acceptance",
    }
    log = (folder / "commands.log").open("w", encoding="utf-8")
    before = source_hashes()

    def run(argv, timeout=1200):
        result = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        log.write(result.stdout + result.stderr + "\n")
        log.flush()
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}); inspect ignored run log")
        return result.stdout

    def docker(*argv):
        prefix = (
            [sys.executable, str(ROOT / "scripts/linux.py"), "--timeout", "1200", "--"]
            if os.name == "nt"
            else []
        )
        return run([*prefix, "docker", *argv])

    try:
        print("Checking candidate-file privacy and secrets...", flush=True)
        print(run([sys.executable, "scripts/privacy_guard.py"]).strip(), flush=True)
        print(run([sys.executable, "scripts/secrets.py", "scan"]).strip(), flush=True)
        images = json.loads((ROOT / "environments/dataflow-images.json").read_text())
        images.update(json.loads((ROOT / "environments/engineering-images.json").read_text()))
        tag = "evdt-development-checks:local"
        build_args = []
        for name in ("python", "uv", "node"):
            build_args.extend(["--build-arg", f"{name.upper()}_IMAGE={images[name]['digest']}"])
        print("Building checks from pinned images and dependency locks...", flush=True)
        docker(
            "build",
            "--platform",
            "linux/amd64",
            "-f",
            "containers/checks.Dockerfile",
            *build_args,
            "-t",
            tag,
            ".",
        )
        report["image_id"] = docker("image", "inspect", tag, "--format", "{{.Id}}").strip()
        report["images"] = {name: images[name]["digest"] for name in ("python", "uv", "node")}
        print("Running offline unit, lint, strict TypeScript, and contract checks...", flush=True)
        output = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--memory",
            "768m",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:size=256m,mode=1777",
            tag,
        )
        markers = [line for line in output.splitlines() if line.startswith("EV-DT_CHECK_RESULT=")]
        if len(markers) != 1:
            raise RuntimeError("Missing or ambiguous check result")
        report["checks"] = json.loads(markers[0].split("=", 1)[1])
        report["passed"] = report["checks"]["passed"] is True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        log.close()
        report["source_sha256"] = before
        if source_hashes() != before:
            report["passed"] = False
            report["error"] = "Source changed during checks; preserve this run and rerun."
        (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Checks passed: {report['passed']}. Local report: {folder.relative_to(ROOT)}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
