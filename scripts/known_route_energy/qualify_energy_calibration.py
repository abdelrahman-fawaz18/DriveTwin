"""Analyze development missions, verify real worker outputs and seal a release."""

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from energy_runtime import (
    PACKAGED,
    ROOT,
    build_image,
    docker,
    linux_checkout,
    remove_owned,
    run_isolated,
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_frozen(analysis):
    q = json.loads((analysis / "qualification.json").read_bytes())
    if not q["passed"] or q["held_out_generated_or_scored"]:
        raise ValueError("Successful development-only analysis required")
    for name, h in q["source_sha256"].items():
        if sha(ROOT / name) != h:
            raise ValueError("Analysis source/input mismatch: " + name)
    names = {"model.json", "targets.json", "evaluation-protocol.json"}
    if set(q["frozen_artifact_sha256"]) != names:
        raise ValueError("Unexpected frozen artifact inventory")
    for name, h in q["frozen_artifact_sha256"].items():
        if sha(analysis / name) != h:
            raise ValueError("Frozen artifact was modified: " + name)
    model = json.loads((analysis / "model.json").read_bytes())
    targets = json.loads((analysis / "targets.json").read_bytes())
    protocol = json.loads((analysis / "evaluation-protocol.json").read_bytes())
    if (
        not model["training_only"]
        or model["model_qualified"]
        or targets["automatic_dispatch_approved"]
    ):
        raise ValueError("Unsupported model authority")
    if protocol["required_model_sha256"] != sha(analysis / "model.json") or protocol[
        "required_targets_sha256"
    ] != sha(analysis / "targets.json"):
        raise ValueError("Protocol frozen references mismatch")
    if (
        targets["targets"]
        != json.loads((ROOT / "qualification/energy/development-protocol.json").read_bytes())[
            "proposed_D18_targets"
        ]
    ):
        raise ValueError("D-18 targets were changed")
    validation = json.loads((analysis / "validation.json").read_bytes())
    if validation["summary"] != q["validation"] or validation["predictions_sha256"] != sha(
        analysis / "predictions.json"
    ):
        raise ValueError("Validation/prediction artifacts do not match their recorded results")
    if model["parameters"] != q["model_parameters"]:
        raise ValueError("Frozen model parameters do not match the fitted record")
    for prediction in json.loads((analysis / "predictions.json").read_bytes()):
        for method, key in (("planned", "candidate"), ("baseline", "baseline")):
            data = json.loads(
                (analysis / (prediction["mission_id"] + "-" + method + "-job.json")).read_bytes()
            )
            normalized = hashlib.sha256(
                json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            ).hexdigest()
            if normalized != prediction[key]["job_sha256"]:
                raise ValueError("Prepared job does not match recorded prediction")
            if method == "planned" and (
                data["parameters"] != model["parameters"]
                or data["mission"]["plan_version"] != model["template_sha256"]
            ):
                raise ValueError("Prepared plan does not match the frozen model/template")
    return q


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--analysis", type=Path)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    collection = args.collection.resolve()
    analysis = args.analysis.resolve() if args.analysis else output / "analysis"
    if (
        output.exists()
        or not output.is_relative_to(ROOT / "evidence/p2")
        or not collection.is_relative_to(ROOT / "evidence/p2")
        or not analysis.is_relative_to(ROOT / "evidence/p2")
    ):
        parser.error("Use private project evidence/p2 inputs and a new output")
    output.mkdir(parents=True)
    owner = uuid.uuid4().hex
    name = "evdt-energy-fit-" + owner
    baseline_volumes = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
    baseline_containers = set(docker("ps", "-aq").stdout.decode().splitlines())
    report = {
        "passed": False,
        "scope": "Synthetic training/validation and frozen pre-held-out release; no final accuracy qualification",
        "held_out_generated_or_scored": False,
        "model_qualified": False,
        "worker_cases": [],
    }
    paths = [
        Path(__file__),
        ROOT / "qualification/energy/development-protocol.json",
        ROOT / "qualification/energy/uncertainty-selection.json",
        ROOT / "scripts/known_route_energy/analyze_energy_development.py",
        ROOT / "src/evdt/energy/calibration.py",
    ]
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}
    try:
        if args.analysis is None:
            image = json.loads(docker("image", "inspect", "evdt-development-checks:local").stdout)[
                0
            ]["Id"]
            root_linux = linux_checkout()
            out_linux = root_linux + "/" + output.relative_to(ROOT).as_posix()
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
                "--tmpfs",
                "/tmp:size=64m,mode=1777",
                "--mount",
                "type=bind,src=" + root_linux + ",dst=/work,readonly",
                "--mount",
                "type=bind,src=" + out_linux + ",dst=/work/" + output.relative_to(ROOT).as_posix(),
                "--workdir",
                "/work",
                "--env",
                "PYTHONPATH=/work/src",
                image,
                "python",
                "scripts/known_route_energy/analyze_energy_development.py",
                "--collection",
                collection.relative_to(ROOT).as_posix(),
                "--output",
                analysis.relative_to(ROOT).as_posix(),
                "--uncertainty-selection",
                "qualification/energy/uncertainty-selection.json",
            )
            print(
                "Fitting training trips and evaluating validation with the reviewed joint-envelope policy...",
                flush=True,
            )
            result = docker("start", "--attach", name, timeout=600, check=False)
            (output / "analysis.log").write_bytes(result.stdout + result.stderr)
            state = json.loads(docker("inspect", name).stdout)[0]["State"]
            if result.returncode or state["ExitCode"] or state["OOMKilled"]:
                raise RuntimeError("Offline analysis failed; inspect analysis.log")
            remove_owned(name, owner)
        q = check_frozen(analysis)
        artifact_paths = [p for p in analysis.iterdir() if p.is_file()]
        sealed_inputs = {p.name: sha(p) for p in artifact_paths}
        prior = json.loads((ROOT / "docs/verification/energy-boundary.json").read_bytes())
        for source, h in prior["source_sha256"].items():
            if sha(ROOT / source) != h:
                raise ValueError("Qualified worker boundary changed: " + source)
        image = build_image(output)
        package_paths = [p.relative_to(ROOT).as_posix() for p in PACKAGED]
        probe = (
            "import hashlib,importlib.util,json,pathlib; base=pathlib.Path('/usr/local/lib/python3.12/site-packages'); names="
            + repr(package_paths)
            + "; assert importlib.util.find_spec('evdt.monitoring') is None; assert importlib.util.find_spec('evdt.energy.calibration') is None; print(json.dumps({n:hashlib.sha256((base/n.removeprefix('src/')).read_bytes()).hexdigest() for n in names}))"
        )
        packaged = run_isolated(image, b"", command=["-I", "-B", "-c", probe])
        if packaged["exit_code"] or json.loads(packaged["stdout"]) != {
            p.relative_to(ROOT).as_posix(): sha(p) for p in PACKAGED
        }:
            raise ValueError("Rebuilt worker package differs from guarded source")
        report["rebuilt_worker_packaged_bytes_verified"] = True
        predictions = json.loads((analysis / "predictions.json").read_bytes())
        if len(predictions) != 6:
            raise ValueError("Expected six independent validation missions")
        for i, prediction in enumerate(predictions, 1):
            for method, key in (("planned", "candidate"), ("baseline", "baseline")):
                job = analysis / (prediction["mission_id"] + "-" + method + "-job.json")
                result = run_isolated(image, job.read_bytes())
                actual = json.loads(result["stdout"])
                if (
                    result["exit_code"]
                    or result["controller_exit_code"]
                    or actual != prediction[key]
                ):
                    raise RuntimeError("Isolated worker result mismatch")
                report["worker_cases"].append(
                    {
                        "mission_id": prediction["mission_id"],
                        "method": method,
                        "passed": True,
                        "job_sha256": actual["job_sha256"],
                    }
                )
            print(f"Worker outputs verified {i}/6 validation missions", flush=True)
        # Recheck sealed bytes after all independent worker calculations.
        q = check_frozen(analysis)
        if any(sha(analysis / n) != h for n, h in sealed_inputs.items()):
            raise ValueError("Frozen analysis artifacts changed during worker verification")
        validation = q["validation"]
        release = {
            "version": "energy-validation-release/1.0.0",
            "analysis_path": analysis.relative_to(ROOT).as_posix(),
            "frozen_artifact_sha256": q["frozen_artifact_sha256"],
            "validation_targets_met": validation["targets_met"],
            "held_out_release_eligible": validation["targets_met"],
            "held_out_generated_or_scored": False,
            "model_qualified": False,
            "worker_image_id": image,
            "worker_outputs_match": True,
            "D18_scope": "Synthetic known-route advisory energy/charge and empirical scenario-envelope targets only; no physical/thermal/command approval",
            "source_sha256": before,
            "analysis_artifact_sha256": sealed_inputs,
        }
        (output / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
        report.update(
            passed=validation["targets_met"],
            validation=validation,
            release=release,
            all_12_worker_outputs_match=True,
        )
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    finally:
        try:
            remove_owned(name, owner)
            volumes = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
            containers = set(docker("ps", "-aq").stdout.decode().splitlines())
            report["cleanup"] = {
                "passed": volumes == baseline_volumes and containers == baseline_containers,
                "preexisting_volumes_preserved": len(volumes & baseline_volumes),
                "new_volumes": len(volumes - baseline_volumes),
                "new_containers": len(containers - baseline_containers),
            }
            report["passed"] = report["passed"] and report["cleanup"]["passed"]
        except Exception as exc:
            report.update(passed=False, cleanup_error=str(exc))
    report["source_sha256"] = before
    if any(sha(ROOT / p) != h for p, h in before.items()):
        report.update(passed=False, error="Verification sources changed during the attempt")
    (output / "qualification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Calibration release checks passed: {report['passed']}; worker cases: {len(report['worker_cases'])}; report: {output.relative_to(ROOT)}",
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
