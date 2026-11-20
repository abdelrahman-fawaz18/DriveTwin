"""Windows controller for the unchanged five-minute outage on real bounded storage."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def fingerprints():
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("contracts/**/*.json"),
        *ROOT.glob("catalog/*.json"),
        *ROOT.glob("scripts/monitoring/*.py"),
        *ROOT.glob("qualification/workload/*.json"),
        *ROOT.glob("migrations/monitoring/*.sql"),
        *ROOT.glob("apps/dashboard/src/**/*"),
        ROOT / "ops/compose/compose.monitoring.yaml",
        ROOT / "ops/compose/compose.extension.yaml",
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "benchmarks/storage.json",
        ROOT / "benchmarks/workloads.json",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file()
    }


def validate_request(request):
    token = uuid.UUID(request["run_id"]).hex
    expected = [
        {
            "asset_id": "bus-001",
            "source_id": "sumo-adapter",
            "catalog_version": "1.2.0",
            "encoded_limit_bytes": 32 * 1024**2,
            "services": ["source", "sender", "probe"],
        }
    ]
    if not (
        request.get("version") == "bounded-fixture-request/1.0.0"
        and request.get("project") == "evdt-e1-" + token[:12]
        and request.get("runtime") == ".runtime/p1-uplink/" + token
        and isinstance(request.get("image_id"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", request["image_id"])
        and request.get("specs") == expected
    ):
        raise RuntimeError("Nominal bounded-outage request differs from its explicit scope")
    return token


def accepted_worker(report):
    required = {
        "src/evdt/monitoring/transport.py",
        "src/evdt/monitoring/vehicle_storage.py",
        "src/evdt/workload/bound_storage_health.py",
        "scripts/monitoring/bounded_fixture.py",
    }
    sources = report.get("source_sha256")
    outage = report.get("outage", {})
    health = outage.get("bounded_operational_health", {})
    stages = {
        "warmup": "connected",
        "sender_stopped_during_outage": "unavailable",
        "reconnected_and_drained": "connected",
        "final": "connected",
    }
    return (
        report.get("passed") is True
        and report.get("bounded_storage", {}).get("merged_mounts_checked") is True
        and outage.get("bounded_vehicle_storage") is True
        and outage.get("passed") is True
        and outage.get("outage_seconds", 0) >= 300
        and outage.get("final", {}).get("passed") is True
        and all(
            health.get(k, {}).get("passed") is True and health[k].get("state") == v
            for k, v in stages.items()
        )
        and report.get("cleanup") == {"containers": "", "volumes": ""}
        and not report.get("error")
        and not report.get("cleanup_error")
        and isinstance(sources, dict)
        and required <= set(sources)
        and all(
            not Path(n).is_absolute()
            and ".." not in Path(n).parts
            and Path(n).as_posix() == n
            and (ROOT / n).resolve().is_relative_to(ROOT)
            and (ROOT / n).is_file()
            and hashlib.sha256((ROOT / n).read_bytes()).hexdigest() == h
            for n, h in sources.items()
        )
    )


def main():
    from current_scope import verify_shared
    from qualify_vehicle_storage import accepted_service
    from storage_lease import StorageLease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if os.name != "nt" or not output.is_relative_to(ROOT / "evidence/p1") or output.exists():
        parser.error("Use Windows and a new private evidence directory under evidence/p1")
    output.mkdir(parents=True)
    settings = tomllib.loads((ROOT / "environments/dev.toml").read_text())["linux"]
    control = ROOT / ".runtime/bounded-storage" / uuid.uuid4().hex
    control.mkdir(parents=True)
    linux_root = (
        settings["windows_mount_root"].rstrip("/")
        + "/"
        + ROOT.drive[0].lower()
        + "/"
        + "/".join(ROOT.parts[1:])
    )
    worker_id = uuid.uuid4().hex
    record = {
        "passed": False,
        "phase_1_complete": False,
        "full_W0_W1_passed": False,
        "scope": "Actual single-asset 300-second outage with nominal bounded storage and API diagnostics",
        "shared_core": verify_shared(),
        "source_sha256": fingerprints(),
    }
    worker = None
    request = None
    leases = []
    log = (output / "worker.log").open("w", encoding="utf-8")

    def linux(arguments, *, check=True, timeout=120):
        result = subprocess.run(
            [sys.executable, "scripts/linux.py", "--", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        with (output / "controller-commands.log").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"argv": arguments, "exit_code": result.returncode})
                + "\n"
                + result.stdout
                + result.stderr
                + "\n"
            )
        if check and result.returncode:
            raise RuntimeError("Bounded outage controller command failed; see private log")
        return result

    try:
        print(
            "Preparing the normal source/broker/API fixture before bounded-volume attachment",
            flush=True,
        )
        worker = subprocess.Popen(
            [
                sys.executable,
                "scripts/linux.py",
                "--run-id",
                worker_id,
                "--timeout",
                "1500",
                "--receipt",
                (output.relative_to(ROOT) / "launcher.json").as_posix(),
                "--",
                "python3",
                "scripts/monitoring/extension_fixture.py",
                "--outage",
                "--bounded-storage-control",
                control.relative_to(ROOT).as_posix(),
                "--evidence-dir",
                (output.relative_to(ROOT) / "services").as_posix(),
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
        )
        deadline = time.monotonic() + 900
        while not (control / "request.json").is_file():
            if worker.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Vehicle worker did not request bounded storage")
            time.sleep(0.25)
        request = json.loads((control / "request.json").read_bytes())
        validate_request(request)
        response = {
            "version": "bounded-fixture-response/1.0.0",
            "run_id": request["run_id"],
            "image_id": request["image_id"],
            "assets": [],
        }
        for spec in request["specs"]:
            identity = {
                "run_id": request["run_id"],
                "asset_id": spec["asset_id"],
                "source_id": spec["source_id"],
            }
            lease = StorageLease(
                settings,
                output / "storage" / spec["asset_id"],
                control / spec["asset_id"],
                identity,
                request["image_id"],
                catalog=spec["catalog_version"],
                encoded_limit=spec["encoded_limit_bytes"],
                seconds=1800,
            )
            leases.append(lease)
            ready = lease.start()
            response["assets"].append(
                {
                    "asset_id": spec["asset_id"],
                    "ready": ready,
                    "binding_path": (lease.runtime / "binding.json").relative_to(ROOT).as_posix(),
                }
            )
        temporary = control / "response.tmp"
        temporary.write_text(json.dumps(response) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(control / "response.json")
        print("Nominal bounded volumes attached; running the actual five-minute outage", flush=True)
        while worker.poll() is None:
            if time.monotonic() >= deadline + 900:
                raise RuntimeError("Bounded outage worker exceeded its owned lifetime")
            time.sleep(0.5)
        services = json.loads((output / "services/qualification.json").read_bytes())
        record["services_passed"] = worker.returncode == 0 and accepted_worker(services)
        record["outage"] = services.get("outage")
        record["source_worker_hashes_match"] = bool(services.get("source_sha256")) and all(
            (ROOT / n).is_file() and hashlib.sha256((ROOT / n).read_bytes()).hexdigest() == h
            for n, h in services.get("source_sha256", {}).items()
        )
        if not record["services_passed"] or not record["source_worker_hashes_match"]:
            raise RuntimeError("Bounded outage or worker source/cleanup verification failed")
    except BaseException as exc:
        record.update(passed=False, error=str(exc) or type(exc).__name__, incomplete=True)
    finally:
        try:
            if worker is not None and worker.poll() is None:
                subprocess.run(
                    [sys.executable, "scripts/linux.py", "--cancel", worker_id],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=True,
                )
                worker.wait(timeout=180)
            if request and not record.get("services_passed"):
                validate_request(request)
                token = uuid.UUID(request["run_id"]).hex
                runtime = ROOT / ".runtime/p1-uplink" / token
                files = [
                    "ops/compose/compose.monitoring.yaml",
                    "ops/compose/compose.extension.yaml",
                ]
                if (runtime / "bounded.compose.json").is_file():
                    files.append((runtime.relative_to(ROOT) / "bounded.compose.json").as_posix())
                prefix = [
                    "docker",
                    "compose",
                    "--project-directory",
                    ".",
                    "--env-file",
                    (runtime.relative_to(ROOT) / "compose.env").as_posix(),
                ]
                for path in files:
                    prefix.extend(["-f", path])
                prefix.extend(["-p", request["project"], "--profile", "*"])
                # Capture available partial results before deleting only owned services.
                monitor = runtime / "outage-control"
                if monitor.is_dir():
                    destination = output / "partial-monitor"
                    destination.mkdir(exist_ok=True)
                    for path in monitor.iterdir():
                        if path.is_file() and not path.is_symlink():
                            (destination / path.name).write_bytes(path.read_bytes())
                name = request["project"] + "-plant"
                inspected = linux(["docker", "container", "inspect", name], check=False)
                if inspected.returncode == 0:
                    value = json.loads(inspected.stdout)[0]
                    if (
                        value.get("Config", {}).get("Labels", {}).get("com.docker.compose.project")
                        != request["project"]
                    ):
                        raise RuntimeError(
                            "Refusing partial capture from a differently owned source"
                        )
                    mounts = {m["Destination"]: m for m in value["Mounts"]}
                    expected = leases[0].ready["volumes"] if leases and leases[0].ready else {}
                    if mounts.get("/spool", {}).get("Name") != expected.get(
                        "telemetry"
                    ) or mounts.get("/critical", {}).get("Name") != expected.get("critical"):
                        raise RuntimeError("Refusing partial capture from changed storage mounts")
                    linux(["docker", "stop", "--time", "5", name])
                    for role, source in (
                        ("source", "/source-output"),
                        ("telemetry", "/spool"),
                        ("critical", "/critical"),
                    ):
                        destination = output / ("partial-" + role)
                        copied = linux(
                            [
                                "docker",
                                "cp",
                                name + ":" + source + "/.",
                                linux_root + "/" + destination.relative_to(ROOT).as_posix(),
                            ],
                            check=False,
                        )
                        record.setdefault("partial_capture_exit_codes", {})[role] = (
                            copied.returncode
                        )
                linux([*prefix, "logs", "--no-color"], check=False)
                linux([*prefix, "down", "--volumes", "--remove-orphans"])
                record["fallback_owned_compose_cleanup_completed"] = True
        except Exception as exc:
            record.update(passed=False, cleanup_error=str(exc))
        record["storage_lifecycles"] = []
        for lease in reversed(leases):
            try:
                result = lease.close()
                record["storage_lifecycles"].append(result is not None and accepted_service(result))
            except Exception as exc:
                record["storage_lifecycles"].append(False)
                record.setdefault("storage_cleanup_errors", []).append(str(exc))
        try:
            record["shared_core_after"] = verify_shared()
            record["passed"] = (
                record.get("services_passed") is True
                and "error" not in record
                and "cleanup_error" not in record
                and "storage_cleanup_errors" not in record
                and bool(record["storage_lifecycles"])
                and all(record["storage_lifecycles"])
                and record["source_sha256"] == fingerprints()
            )
        except Exception as exc:
            record.update(passed=False, cleanup_error=str(exc))
        log.close()
        if record["source_sha256"] != fingerprints():
            record.update(passed=False, error="Source changed during bounded outage qualification")
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
