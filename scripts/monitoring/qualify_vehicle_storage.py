"""Qualify temporary bounded vehicle storage, guarded reopening and fault persistence."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def accepted_service(report):
    required = {
        "scripts/monitoring/qualify_disk_boundaries.py",
        "scripts/monitoring/disk_boundary_probe.py",
        "src/evdt/monitoring/vehicle_storage.py",
        "src/evdt/monitoring/spool.py",
    }
    sources = report.get("source_sha256", {})
    return (
        all(
            report.get(k) is True
            for k in (
                "passed",
                "temporary_vehicle_storage_mode",
                "temporary_vehicle_storage_lifecycle_passed",
                "mechanism_checks_passed",
                "queue_packaged_sources_match",
            )
        )
        and not report.get("error")
        and not report.get("cleanup_error")
        and report.get("cleanup")
        == {"owned_containers": [], "owned_volumes": [], "owned_loop_associations": []}
        and isinstance(sources, dict)
        and required <= set(sources)
        and all(
            not Path(n).is_absolute()
            and Path(n).as_posix() == n
            and ".." not in Path(n).parts
            and (ROOT / n).resolve().is_relative_to(ROOT)
            and (ROOT / n).is_file()
            and hashlib.sha256((ROOT / n).read_bytes()).hexdigest() == h
            for n, h in sources.items()
        )
        and all(
            report.get("checks", {}).get(k, {}).get("passed") is True
            for k in ("vehicle_initialize", "vehicle_fresh_reopen")
        )
    )


def main():
    from locked_runtime import build_runtime
    from qualify_queue_storage import fingerprints as base_fingerprints
    from storage_lease import StorageLease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if os.name != "nt" or not output.is_relative_to(ROOT / "evidence/p1") or output.exists():
        parser.error("Use Windows and a new private directory under evidence/p1")
    output.mkdir(parents=True)
    settings = tomllib.loads((ROOT / "environments/dev.toml").read_text())["linux"]
    linux_root = (
        settings["windows_mount_root"].rstrip("/")
        + "/"
        + ROOT.drive[0].lower()
        + "/"
        + "/".join(ROOT.parts[1:])
    )
    token = uuid.uuid4().hex
    runtime = ROOT / ".runtime/bounded-storage" / token
    runtime.mkdir(parents=True)
    (runtime / "metrics-token").write_text(
        secrets.token_urlsafe(32) + "\n", encoding="utf-8", newline="\n"
    )
    identity = {"run_id": str(uuid.uuid4()), "asset_id": "bus-003", "source_id": "sumo-adapter"}
    (runtime / "identity.json").write_text(
        json.dumps(identity) + "\n", encoding="utf-8", newline="\n"
    )

    def fingerprints():
        return {
            **base_fingerprints(),
            Path(__file__).relative_to(ROOT).as_posix(): hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
        }

    before = fingerprints()
    record = {
        "passed": False,
        "phase_1_complete": False,
        "scope": "Temporary owned storage initialization/reopen/fault fixture; no normal sender or live workload acceptance",
        "source_sha256": before,
        "checks": {},
    }
    lease = None
    ready = None
    names = []
    log = (output / "commands.log").open("w", encoding="utf-8")

    def run(arguments, check=True, timeout=120):
        argv = [sys.executable, "scripts/linux.py", "--", *arguments]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        log.write(
            json.dumps({"argv": argv, "exit_code": result.returncode})
            + "\n"
            + result.stdout
            + result.stderr
            + "\n"
        )
        log.flush()
        if check and result.returncode:
            raise RuntimeError("Ordinary storage helper failed; see private commands.log")
        return result

    def helper(module_args, binding_file=None, missing=None, script=False, check=True):
        name = ready["owner"] + "-vehicle-" + str(len(names))
        names.append(name)
        arguments = [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            "com.evdt.owner=" + ready["owner"],
            "--label",
            "com.evdt.scope=temporary-vehicle-storage/1.0.0",
            "--network",
            "none",
            "--read-only",
            "--memory",
            "256m",
            "--pids-limit",
            "32",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "10001:10001",
        ]
        for role, destination in (
            ("telemetry", "telemetry" if script else "spool"),
            ("critical", "critical"),
        ):
            if role != missing:
                arguments.extend(
                    [
                        "--mount",
                        "type=volume,src=" + ready["volumes"][role] + ",dst=/" + destination,
                    ]
                )
        if script:
            arguments.extend(
                [
                    "--mount",
                    "type=bind,src="
                    + linux_root
                    + "/scripts/monitoring/disk_boundary_probe.py,dst=/probe.py,readonly",
                ]
            )
        else:
            selected = binding_file or (runtime / "binding.json")
            arguments.extend(
                [
                    "--mount",
                    "type=bind,src="
                    + linux_root
                    + "/"
                    + (runtime / "metrics-token").relative_to(ROOT).as_posix()
                    + ",dst=/metrics-token,readonly",
                    "--env",
                    "METRICS_TOKEN_FILE=/metrics-token",
                ]
            )
            arguments.extend(
                [
                    "--mount",
                    "type=bind,src="
                    + linux_root
                    + "/"
                    + selected.relative_to(ROOT).as_posix()
                    + ",dst=/binding.json,readonly",
                ]
            )
            witness = runtime / "witness.json"
            if witness.exists():
                arguments.extend(
                    [
                        "--mount",
                        "type=bind,src="
                        + linux_root
                        + "/"
                        + witness.relative_to(ROOT).as_posix()
                        + ",dst=/witness.json,readonly",
                    ]
                )
        arguments.extend(["--entrypoint", "python", record["image_id"], *module_args])
        return run(arguments, check=check)

    try:
        print("Building locked runtime for the bounded-storage lifecycle", flush=True)
        record["image_id"] = build_runtime(output, "evdt-vehicle-storage:p1")
        if fingerprints() != before:
            raise RuntimeError("Source changed during build; no storage started")
        lease = StorageLease(settings, output, runtime, identity, record["image_id"])
        ready = lease.start()
        verify = ["-m", "evdt.monitoring.vehicle_storage", "verify", "--binding", "/binding.json"]
        record["checks"]["ordinary_reopen"] = json.loads(helper(verify).stdout)
        for role in ("telemetry", "critical"):
            denied = helper(verify, missing=role, check=False)
            if not denied.returncode or "StorageUnavailable" not in denied.stderr:
                raise RuntimeError("Missing mount was not explicitly refused")
            record["checks"]["missing_" + role + "_refused"] = True
        for change, expected in (
            ("boot", "another Ubuntu boot"),
            ("device", "capacity differs"),
            ("binding", "binding differs"),
        ):
            body = json.loads(json.dumps(ready["binding"]))
            if change == "boot":
                body["boot_id"] = str(uuid.uuid4())
            elif change == "device":
                body["roles"]["telemetry"]["device"] = (
                    max(item["device"] for item in body["roles"].values()) + 1
                )
            else:
                body["owner"] = "evdt-disk-" + "b" * 32
            path = runtime / ("denied-" + change + ".json")
            path.write_text(json.dumps(body) + "\n", encoding="utf-8", newline="\n")
            denied = helper(verify, binding_file=path, check=False)
            if (
                not denied.returncode
                or "StorageUnavailable" not in denied.stderr
                or expected not in denied.stderr
            ):
                raise RuntimeError("Changed storage binding was not explicitly refused")
            record["checks"][change + "_refused"] = True
        probe = ["-m", "evdt.workload.vehicle_storage_probe"]
        first = json.loads(helper([*probe, "enqueue", "--binding", "/binding.json"]).stdout)
        witness = first.pop("witness")
        record["checks"]["original_enqueued"] = first
        (runtime / "witness.json").write_text(
            json.dumps(witness) + "\n", encoding="utf-8", newline="\n"
        )
        arguments = ["--binding", "/binding.json", "--witness", "/witness.json"]
        record["checks"]["original_fresh_reopen"] = json.loads(
            helper([*probe, "verify", *arguments]).stdout
        )
        record["checks"]["telemetry_physically_full"] = json.loads(
            helper(["/probe.py", "fill"], script=True).stdout
        )
        record["checks"]["queue_full_fault"] = json.loads(
            helper([*probe, "full-fault", *arguments]).stdout
        )
        witness["fault_expected"] = True
        (runtime / "witness.json").write_text(
            json.dumps(witness) + "\n", encoding="utf-8", newline="\n"
        )
        record["checks"]["full_fresh_reopen"] = json.loads(
            helper([*probe, "verify", *arguments]).stdout
        )
        record["checks"]["telemetry_recovered"] = json.loads(
            helper(["/probe.py", "clear"], script=True).stdout
        )
        record["checks"]["recovered_fresh_reopen"] = json.loads(
            helper([*probe, "verify", *arguments]).stdout
        )
        record["ordinary_checks_passed"] = True
    except Exception as exc:
        record.update(passed=False, error=str(exc))
    finally:
        try:
            if lease is not None:
                owner = lease.release_owner()
                if owner:
                    for name in names:
                        inspected = run(["docker", "container", "inspect", name], check=False)
                        if inspected.returncode == 0:
                            container = json.loads(inspected.stdout)[0]
                            if (
                                container.get("Config", {}).get("Labels", {}).get("com.evdt.owner")
                                != owner
                            ):
                                raise RuntimeError("Refusing a changed helper container")
                            run(["docker", "rm", "-f", name])
                child_report = lease.close()
                record["services_passed"] = child_report is not None and accepted_service(
                    child_report
                )
            record["passed"] = (
                record.get("ordinary_checks_passed") is True
                and record.get("services_passed") is True
                and "error" not in record
                and fingerprints() == before
            )
        except Exception as exc:
            record.update(passed=False, cleanup_error=str(exc))
        if fingerprints() != before:
            record.update(
                passed=False, error="Source changed during temporary storage qualification"
            )
        log.close()
        (output / "qualification.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(
            json.dumps(
                {
                    "passed": record["passed"],
                    "evidence": output.relative_to(ROOT).as_posix(),
                    "error": record.get("error"),
                    "cleanup_error": record.get("cleanup_error"),
                }
            )
        )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
