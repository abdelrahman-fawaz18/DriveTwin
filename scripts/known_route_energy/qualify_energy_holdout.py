"""Run the frozen held-out study once, with independent restricted-worker outputs."""

import argparse
import json
import uuid
from pathlib import Path

from energy_holdout import ROOT, claim_once, claim_path, sha, verify_release, write
from energy_runtime import (
    PACKAGED,
    build_image,
    docker,
    linux_checkout,
    remove_owned,
    run_isolated,
)


def runtime_args(name, owner, image, output, command):
    linux = linux_checkout()
    relative = output.relative_to(ROOT).as_posix()
    return [
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
        "type=bind,src=" + linux + ",dst=/project,readonly",
        "--mount",
        "type=bind,src=" + linux + "/" + relative + ",dst=/project/" + relative,
        "--workdir",
        "/project",
        "--env",
        "PYTHONPATH=/project/src",
        "--entrypoint",
        "python",
        image,
        *command,
    ]


def build_collector(output):
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
    for key in ("python", "uv", "node"):
        argv.extend(["--build-arg", key.upper() + "_IMAGE=" + pins[key]["digest"]])
    argv.extend(["-t", "evdt-energy-heldout-collector:p2", "."])
    result = docker(*argv, timeout=600, check=False)
    (output / "collector-build.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Held-out collector build failed; inspect private log")
    return json.loads(docker("image", "inspect", "evdt-energy-heldout-collector:p2").stdout)[0][
        "Id"
    ]


def run_stage(image, name, owner, output, command, label):
    try:
        docker(*runtime_args(name, owner, image, output, command))
        result = docker("start", "--attach", name, timeout=600, check=False)
        (output / (label + ".log")).write_bytes(result.stdout + result.stderr)
        state = json.loads(docker("inspect", name).stdout)[0]["State"]
        if result.returncode or state["ExitCode"] or state["OOMKilled"] or state["Running"]:
            raise RuntimeError("Held-out stage failed: " + label + "; inspect its private log")
    finally:
        remove_owned(name, owner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    release = verify_release()
    marker = claim_path(ROOT, release["manifest_sha256"])
    if args.inspect:
        if not marker.exists():
            print("No held-out attempt has been reserved for this frozen release.")
            return 0
        value = json.loads(marker.read_bytes())
        report = ROOT / value["evidence_root"] / "qualification.json"
        print(json.dumps(json.loads(report.read_bytes()) if report.exists() else value, indent=2))
        return 0
    if args.evidence_dir is None:
        parser.error("Choose a new private evidence directory or use --inspect")
    output = args.evidence_dir.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "evidence/p2"):
        parser.error("Choose a new evidence/p2 directory")
    if marker.exists():
        parser.error(
            "This frozen release already has a held-out attempt; use --inspect, preserve data and do not regenerate or retune"
        )
    print(
        "Verifying exact validation-job/envelope parity before opening held-out outcomes...",
        flush=True,
    )
    output.mkdir(parents=True)
    owner = uuid.uuid4().hex
    name = "evdt-energy-heldout-" + owner
    relative = output.relative_to(ROOT).as_posix()
    checks = json.loads(docker("image", "inspect", "evdt-development-checks:local").stdout)[0]["Id"]
    run_stage(
        checks,
        name,
        owner,
        output,
        [
            "scripts/known_route_energy/evaluate_energy_holdout.py",
            "parity",
            "--evidence-dir",
            relative,
        ],
        "validation-parity",
    )
    marker = claim_once(ROOT, release["manifest_sha256"], output)
    volumes = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
    containers = set(docker("ps", "-aq").stdout.decode().splitlines())
    networks = set(docker("network", "ls", "-q").stdout.decode().splitlines())
    paths = [
        Path(__file__),
        ROOT / "scripts/known_route_energy/energy_holdout.py",
        ROOT / "scripts/known_route_energy/collect_energy_holdout.py",
        ROOT / "scripts/known_route_energy/evaluate_energy_holdout.py",
        ROOT / "scripts/known_route_energy/collect_energy_missions.py",
        ROOT / "scripts/known_route_energy/energy_runtime.py",
        ROOT / "containers/monitoring.Dockerfile",
        ROOT / "docs/models/energy-validation-release.json",
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        *sorted((ROOT / "src").rglob("*.py")),
        *sorted((ROOT / "contracts").rglob("*.json")),
        *sorted((ROOT / "catalog").glob("*.json")),
        *sorted((ROOT / "simulation/montreal").glob("*.json")),
        ROOT / "data/public/montreal-core-v1/roads.osm.xml",
    ]
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    report = {
        "passed": False,
        "scope": "Once-only frozen synthetic known-route held-out evaluation; no product/physical/dispatch promotion",
        "release_sha256": release["manifest_sha256"],
        "evaluation_completed": False,
        "model_promotion": "pending_P2_E1",
        "worker_cases": [],
    }
    try:
        print("Collecting six reserved complete missions, one bus at a time...", flush=True)
        collector = build_collector(output)
        run_stage(
            collector,
            name,
            owner,
            output,
            [
                "scripts/known_route_energy/collect_energy_holdout.py",
                "--output",
                relative + "/collection",
                "--osm",
                "data/public/montreal-core-v1/roads.osm.xml",
                "--release-sha",
                release["manifest_sha256"],
            ],
            "collection",
        )
        dataset = json.loads((output / "collection/dataset.json").read_bytes())
        if not dataset["passed"]:
            raise RuntimeError("Held-out collection did not pass")
        report["collection_image_id"] = collector
        print("Preparing causal jobs and frozen scenario envelopes...", flush=True)
        run_stage(
            checks,
            name,
            owner,
            output,
            [
                "scripts/known_route_energy/evaluate_energy_holdout.py",
                "prepare",
                "--evidence-dir",
                relative,
            ],
            "preparation",
        )
        prepared = json.loads((output / "prepared/cases.json").read_bytes())
        prior = json.loads((ROOT / "docs/verification/energy-boundary.json").read_bytes())
        for p, expected in prior["source_sha256"].items():
            if sha(ROOT / p) != expected:
                raise ValueError("Qualified worker source changed: " + p)
        image = build_image(output)
        package = [p.relative_to(ROOT).as_posix() for p in PACKAGED]
        probe = (
            "import hashlib,importlib.util,json,pathlib; base=pathlib.Path('/usr/local/lib/python3.12/site-packages'); paths="
            + repr(package)
            + "; assert importlib.util.find_spec('evdt.monitoring') is None; assert importlib.util.find_spec('evdt.energy.calibration') is None; print(json.dumps({p:hashlib.sha256((base/p.removeprefix('src/')).read_bytes()).hexdigest() for p in paths}))"
        )
        result = run_isolated(image, b"", command=["-I", "-B", "-c", probe])
        if result["exit_code"] or json.loads(result["stdout"]) != {
            p.relative_to(ROOT).as_posix(): sha(p) for p in PACKAGED
        }:
            raise ValueError("Held-out worker packaged code mismatch")
        (output / "worker-results").mkdir()
        for index, case in enumerate(prepared["cases"], 1):
            for method in ("planned", "baseline"):
                job = output / "prepared" / case["jobs"][method]["filename"]
                if sha(job) != case["jobs"][method]["file_sha256"]:
                    raise ValueError("Prepared job changed")
                result = run_isolated(image, job.read_bytes())
                receipt = {
                    "exit_code": result["exit_code"],
                    "controller_exit_code": result["controller_exit_code"],
                    "policy_verified": result["policy_verified"],
                    "result": json.loads(result["stdout"]),
                }
                write(
                    output / "worker-results" / (case["mission_id"] + "-" + method + ".json"),
                    receipt,
                )
                report["worker_cases"].append(
                    {
                        "mission_id": case["mission_id"],
                        "method": method,
                        "exit_code": result["exit_code"],
                        "policy_verified": True,
                    }
                )
            print(f"Sealed worker predictions {index}/6 held-out missions", flush=True)
        write(
            output / "worker-batch.json",
            {
                "completed": True,
                "cases": len(report["worker_cases"]),
                "prepared_cases_sha256": sha(output / "prepared/cases.json"),
                "worker_image_id": image,
                "files_sha256": {p.name: sha(p) for p in (output / "worker-results").iterdir()},
            },
        )
        print("Scoring the sealed batch once against unchanged criteria...", flush=True)
        run_stage(
            checks,
            name,
            owner,
            output,
            [
                "scripts/known_route_energy/evaluate_energy_holdout.py",
                "score",
                "--evidence-dir",
                relative,
            ],
            "scoring",
        )
        evaluation = json.loads((output / "evaluation.json").read_bytes())
        verify_release()
        report.update(
            passed=evaluation["held_out_criteria_met"],
            evaluation_completed=True,
            held_out_criteria_met=evaluation["held_out_criteria_met"],
            summary=evaluation["summary"],
            model_targets_protocol_unchanged=True,
            evaluation_sha256=sha(output / "evaluation.json"),
            worker_image_id=image,
        )
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    finally:
        try:
            remove_owned(name, owner)
            after_v = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
            after_c = set(docker("ps", "-aq").stdout.decode().splitlines())
            after_n = set(docker("network", "ls", "-q").stdout.decode().splitlines())
            report["cleanup"] = {
                "passed": volumes == after_v and containers == after_c and networks == after_n,
                "preexisting_volumes_preserved": len(volumes & after_v),
                "new_volumes": len(after_v - volumes),
                "new_containers": len(after_c - containers),
                "networks_preserved": networks == after_n,
            }
            report["passed"] = report["passed"] and report["cleanup"]["passed"]
        except Exception as exc:
            report.update(passed=False, cleanup_error=str(exc))
    report["source_sha256"] = before
    if any(sha(ROOT / p) != h for p, h in before.items()):
        report.update(passed=False, error="Held-out runner sources changed during this attempt")
    write(output / "qualification.json", report)
    write(
        marker,
        {
            "version": "held-out-attempt/1.0.0",
            "release_sha256": release["manifest_sha256"],
            "evidence_root": output.relative_to(ROOT).as_posix(),
            "state": "scored" if report["evaluation_completed"] else "incomplete",
            "passed": report["passed"],
        },
    )
    print(
        f"Held-out evaluation completed: {report['evaluation_completed']}; criteria passed: {report['passed']}; report: {relative}",
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
