"""Build locked queue code and qualify only owned physical storage from Windows."""

import argparse
import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SOURCE = {
    "scripts/monitoring/qualify_disk_boundaries.py",
    "src/evdt/workload/disk_queue.py",
    "benchmarks/storage.json",
    "benchmarks/storage-queue.json",
}


def fingerprints():
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("contracts/**/*.json"),
        *ROOT.glob("catalog/*.json"),
        *ROOT.glob("apps/dashboard/src/**/*"),
        *ROOT.glob("apps/dashboard/*.json"),
        ROOT / "apps/dashboard/index.html",
        ROOT / "apps/dashboard/replay.html",
        ROOT / "apps/dashboard/vite.config.mjs",
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "environments/dataflow-images.json",
        ROOT / "environments/engineering-images.json",
        ROOT / "benchmarks/storage.json",
        ROOT / "benchmarks/storage-queue.json",
        ROOT / "benchmarks/workloads.json",
        ROOT / "scripts/monitoring/qualify_disk_boundaries.py",
        ROOT / "scripts/monitoring/disk_boundary_probe.py",
        ROOT / "scripts/monitoring/locked_runtime.py",
        ROOT / "scripts/monitoring/storage_lease.py",
        Path(__file__),
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file()
    }


def accepted(report):
    return (
        report.get("passed") is True
        and report.get("queue_storage_integration_passed") is True
        and report.get("critical_fault_full_disk_passed") is True
        and report.get("queue_packaged_sources_match") is True
        and not report.get("error")
        and not report.get("cleanup_error")
        and report.get("cleanup")
        == {"owned_containers": [], "owned_volumes": [], "owned_loop_associations": []}
        and isinstance(report.get("source_sha256"), dict)
        and bool(report["source_sha256"])
        and REQUIRED_SOURCE <= set(report["source_sha256"])
        and all(
            not Path(name).is_absolute()
            and ".." not in Path(name).parts
            and (ROOT / name).resolve().is_relative_to(ROOT)
            and (ROOT / name).is_file()
            and hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
            for name, expected in report["source_sha256"].items()
        )
    )


def main():
    from locked_runtime import build_runtime

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if os.name != "nt" or not output.is_relative_to(ROOT / "evidence/p1") or output.exists():
        parser.error("Use Windows and a new private directory under evidence/p1")
    settings = tomllib.loads((ROOT / "environments/dev.toml").read_text())["linux"]
    output.mkdir(parents=True)
    before = fingerprints()
    record = {
        "passed": False,
        "scope": "Bounded production queue component; accelerated synthetic storage inputs",
        "source_sha256": before,
        "phase_1_complete": False,
        "commands": [],
    }
    try:
        print("Building the locked queue application image", flush=True)
        image = build_runtime(output, "evdt-queue-storage:p1")
        record["commands"].append({"stage": "build", "exit_code": 0})
        if fingerprints() != before:
            raise RuntimeError(
                "Application build failed or source changed; no disk fixture started"
            )
        record["image_id"] = image
        linux_root = (
            settings["windows_mount_root"].rstrip("/")
            + "/"
            + ROOT.drive[0].lower()
            + "/"
            + "/".join(ROOT.parts[1:])
        )
        relative = (output.relative_to(ROOT) / "services").as_posix()
        print("Checking the actual queue on new owned bounded filesystems", flush=True)
        argv = [
            "wsl.exe",
            "--distribution",
            settings["distribution"],
            "--user",
            "root",
            "--cd",
            linux_root,
            "--exec",
            "python3",
            "scripts/monitoring/qualify_disk_boundaries.py",
            "--critical-fault",
            "--queue-image",
            image,
            "--evidence-dir",
            relative,
        ]
        with (output / "worker.log").open("w", encoding="utf-8") as log:
            child = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=log)
        record["commands"].append({"stage": "owned_root_fixture", "exit_code": child.returncode})
        report = json.loads((output / "services/qualification.json").read_bytes())
        record["services_passed"] = accepted(report)
        record["passed"] = (
            child.returncode == 0 and record["services_passed"] and fingerprints() == before
        )
        if not record["passed"]:
            raise RuntimeError("Queue storage fixture or exact source/cleanup checks failed")
    except Exception as exc:
        record.update(passed=False, error=str(exc))
    finally:
        if fingerprints() != before:
            record.update(passed=False, error="Source changed during queue storage qualification")
        (output / "qualification.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(
            json.dumps(
                {
                    "passed": record["passed"],
                    "evidence": output.relative_to(ROOT).as_posix(),
                    "error": record.get("error"),
                }
            )
        )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
