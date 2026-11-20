"""Build the locked simulator and collect only the reserved development missions."""

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from energy_runtime import ROOT, docker, linux_checkout, remove_owned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "evidence/p2"):
        parser.error("Choose a new evidence/p2 directory")
    output.mkdir(parents=True)
    (output / "collection").mkdir()
    # Freeze the actual collection code/configuration; later estimator additions do not regenerate data.
    paths = [
        Path(__file__),
        ROOT / "scripts/known_route_energy/collect_energy_missions.py",
        ROOT / "scripts/known_route_energy/energy_runtime.py",
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        ROOT / "qualification/energy/mission-design.json",
        ROOT / "qualification/energy/development-protocol.json",
        ROOT / "data/public/montreal-core-v1/roads.osm.xml",
        *sorted((ROOT / "src").rglob("*.py")),
        *sorted((ROOT / "contracts").rglob("*.json")),
        *sorted((ROOT / "catalog").glob("*.json")),
        *sorted((ROOT / "simulation/montreal").glob("*.json")),
    ]
    before = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }
    owner = uuid.uuid4().hex
    name = "evdt-energy-collection-" + owner
    baseline = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
    report = {
        "passed": False,
        "scope": "Sequential complete training/validation SUMO missions only; not capacity/soak proof",
        "held_out_collected": False,
    }
    try:
        pins = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())
        pins.update(json.loads((ROOT / "environments/engineering-images.json").read_bytes()))
        argv = [
            "build",
            "--platform",
            "linux/amd64",
            "--target",
            "simulator",
            "-f",
            "containers/monitoring.Dockerfile",
        ]
        for k in ("python", "uv", "node"):
            argv.extend(["--build-arg", k.upper() + "_IMAGE=" + pins[k]["digest"]])
        argv.extend(["-t", "evdt-energy-collector:p2", "."])
        print("Building the locked sequential mission collector...", flush=True)
        built = docker(*argv, timeout=600, check=False)
        (output / "build.log").write_bytes(built.stdout + built.stderr)
        if built.returncode:
            raise RuntimeError("Collector image build failed; inspect build.log")
        image = json.loads(docker("image", "inspect", "evdt-energy-collector:p2").stdout)[0]["Id"]
        root_linux = linux_checkout()
        target = root_linux + "/" + output.relative_to(ROOT).as_posix() + "/collection"
        docker(
            "create",
            "--name",
            name,
            "--label",
            "evdt.energy.owner=" + owner,
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "2",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:size=64m,mode=1777",
            "--mount",
            "type=bind,src=" + root_linux + ",dst=/project,readonly",
            "--mount",
            "type=bind,src=" + target + ",dst=/output",
            "--env",
            "PYTHONPATH=/project/src",
            "--entrypoint",
            "python",
            image,
            "/project/scripts/known_route_energy/collect_energy_missions.py",
            "--output",
            "/output",
            "--osm",
            "/project/data/public/montreal-core-v1/roads.osm.xml",
        )
        print(
            "Collecting 18 development missions, one bus at a time; held-out trips excluded.",
            flush=True,
        )
        result = docker("start", "--attach", name, timeout=900, check=False)
        (output / "collection.log").write_bytes(result.stdout + result.stderr)
        info = json.loads(docker("inspect", name).stdout)[0]
        report.update(
            image_id=image,
            container_exit=info["State"]["ExitCode"],
            oom_killed=info["State"]["OOMKilled"],
        )
        data = json.loads((output / "collection/dataset.json").read_bytes())
        report["passed"] = (
            result.returncode == 0
            and data["passed"]
            and not info["State"]["OOMKilled"]
            and info["State"]["ExitCode"] == 0
        )
        report["dataset"] = (
            {
                k: data[k]
                for k in ("training_missions", "validation_missions", "repeatability_passed")
            }
            if data["passed"]
            else {"error": data.get("error")}
        )
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    finally:
        try:
            remove_owned(name, owner)
            after = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
            report["cleanup"] = {
                "passed": after == baseline,
                "preexisting_volumes_preserved": len(after & baseline),
                "new_or_missing_volumes": len(after ^ baseline),
            }
            report["passed"] = report["passed"] and report["cleanup"]["passed"]
        except Exception as exc:
            report.update(passed=False, cleanup_error=str(exc))
    report["source_sha256"] = before
    if any(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h for p, h in before.items()):
        report.update(passed=False, error="Collection sources changed during the attempt")
    (output / "qualification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Development missions passed: {report['passed']}; report: {output.relative_to(ROOT)}",
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
