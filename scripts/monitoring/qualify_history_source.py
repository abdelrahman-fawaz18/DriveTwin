"""Qualify one actual accelerated history prefix in a bounded, owned simulator."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def fingerprints():
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("contracts/**/*.json"),
        *ROOT.glob("catalog/*.json"),
        *ROOT.glob("simulation/montreal-history/*.json"),
        ROOT / "benchmarks/history.json",
        ROOT / "benchmarks/workloads.json",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / ".dockerignore",
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "containers/sumo-debian.sources",
        ROOT / "environments/dataflow-images.json",
        ROOT / "environments/engineering-images.json",
        Path(__file__),
        ROOT / "scripts/monitoring/history_source_audit.py",
        ROOT / "data/public/montreal-core-v1/roads.osm.xml",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--asset", default="bus-001", choices=[f"bus-{i:03}" for i in range(1, 11)])
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if os.name == "nt":
        parser.error("Run through scripts/linux.py")
    if not output.is_relative_to(ROOT / "evidence/p1") or output.exists():
        parser.error("Use a new directory under evidence/p1")
    if not 1 <= args.samples <= 7200:
        parser.error("Choose 1..7200 accelerated simulation samples")
    output.mkdir(parents=True)
    before = fingerprints()
    token = uuid.uuid4().hex
    name = "evdt-e3-source-" + token[:12]
    image = "evdt-history-simulator:" + token
    record = {
        "passed": False,
        "scope": "One accelerated actual source prefix; not complete W2 storage, live transport, Q05 or Q10",
        "samples": args.samples,
        "source_sha256": before,
        "commands": [],
    }
    created = False
    log = (output / "commands.log").open("w", encoding="utf-8")

    def run(argv, timeout=180, check=True):
        try:
            result = subprocess.run(
                argv,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            record["commands"].append({"argv": argv, "incomplete": "timeout"})
            raise
        record["commands"].append({"argv": argv, "exit_code": result.returncode})
        log.write(json.dumps(argv) + "\n" + result.stdout + result.stderr + "\n")
        log.flush()
        if check and result.returncode:
            raise RuntimeError("Command failed; inspect private commands.log")
        return result.stdout.strip()

    try:
        reserve = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())["limits"][
            "host_disk_reserve_bytes"
        ]
        record["host_disk_free_bytes"] = shutil.disk_usage(ROOT).free
        if record["host_disk_free_bytes"] < reserve + 4 * 1024**3:
            raise RuntimeError(
                "History source preflight lacks disk reserve plus 4 GiB temporary headroom"
            )
        images = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())
        images.update(json.loads((ROOT / "environments/engineering-images.json").read_bytes()))
        build_args = []
        for key in ("python", "uv", "node"):
            build_args += ["--build-arg", key.upper() + "_IMAGE=" + images[key]["digest"]]
        print("Building the locked simulator for an accelerated history prefix", flush=True)
        run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--target",
                "simulator",
                "-f",
                "containers/monitoring.Dockerfile",
                *build_args,
                "-t",
                image,
                ".",
            ],
            timeout=900,
        )
        record["image_id"] = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
        run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--label",
                "com.evdt.qualifier=history-source",
                "--label",
                "com.evdt.run=" + token,
                "--network",
                "none",
                "--memory",
                "512m",
                "--cpus",
                "2",
                "--pids-limit",
                "64",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp:size=64m,mode=1777",
                "-v",
                str(ROOT / "simulation/montreal-history") + ":/scenario:ro",
                "-v",
                str(ROOT / "data/public/montreal-core-v1/roads.osm.xml")
                + ":/input/roads.osm.xml:ro",
                "-v",
                str(output) + ":/history-output",
                image,
                "python",
                "-m",
                "evdt.workload.history_source",
                "--run-id",
                str(uuid.UUID(token)),
                "--asset-id",
                args.asset,
                "--maximum-samples",
                str(args.samples),
                "--output",
                "/history-output/" + args.asset,
            ]
        )
        created = True
        print(
            "Generating actual simulation readings; no wall-time workload or network delivery",
            flush=True,
        )
        run(["docker", "start", "-a", name], timeout=1200)
        report = json.loads((output / args.asset / "source-summary.json").read_bytes())
        record["source"] = report
        if not report["passed"]:
            raise RuntimeError("Actual accelerated history source did not complete")
        from history_source_audit import audit

        record["audit"] = audit(output / args.asset, ROOT / "simulation/montreal-history")
        record["passed"] = True
    except BaseException as exc:
        record.update(passed=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        if created:
            try:
                labels = json.loads(
                    run(["docker", "inspect", name, "--format", "{{json .Config.Labels}} "])
                )
                if (
                    labels.get("com.evdt.qualifier") != "history-source"
                    or labels.get("com.evdt.run") != token
                ):
                    raise RuntimeError("Refusing cleanup of a container without exact ownership")
                state = json.loads(run(["docker", "inspect", name, "--format", "{{json .State}} "]))
                record["container_state"] = state
                if state["Running"]:
                    record["passed"] = False
                    run(["docker", "stop", "-t", "10", name], timeout=30)
                run(["docker", "rm", name])
                record["cleanup_containers"] = run(
                    ["docker", "ps", "-aq", "--filter", "label=com.evdt.run=" + token]
                )
                if record["cleanup_containers"] or state.get("OOMKilled") or state["ExitCode"] != 0:
                    raise RuntimeError("Incomplete owned cleanup or source memory limit exceeded")
            except Exception as exc:
                record.update(passed=False, cleanup_error=str(exc))
        if fingerprints() != before:
            record.update(
                passed=False, error="Source changed during qualification; preserve and rerun"
            )
        log.close()
        (output / "qualification.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(
            "History source passed: "
            + str(record["passed"])
            + ". Evidence: "
            + output.relative_to(ROOT).as_posix(),
            flush=True,
        )
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
