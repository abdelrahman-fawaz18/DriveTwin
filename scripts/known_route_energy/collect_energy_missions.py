"""Sequential complete SUMO training/validation missions; held-out runs forbidden."""

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.energy.observations import fingerprint  # noqa: E402
from evdt.energy.partitions import MissionArtifact, parse_design, validate_inventory  # noqa: E402
from evdt.monitoring.contracts import monitoring_contract, observation_topic  # noqa: E402
from evdt.monitoring.source import SumoVehicleSource  # noqa: E402


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def prepare_scenario(destination, mission, protocol):
    destination.mkdir()
    for name in (
        "mission.json",
        "bus-parameters.json",
        "plant.json",
        "import-review.json",
        "stations.json",
    ):
        shutil.copyfile(ROOT / "simulation/montreal" / name, destination / name)
    recipe = mission.recipe
    data = json.loads((destination / "mission.json").read_bytes())
    data["seed"] = recipe.realization_seed
    data["stop"]["duration_s"] = recipe.scheduled_stop_s
    write(destination / "mission.json", data)
    bus = json.loads((destination / "bus-parameters.json").read_bytes())
    p = bus["parameters"]
    values = {
        "passenger_load": recipe.loaded_mass_kg - p["curb_mass"]["value"],
        "initial_soc": recipe.initial_soc,
        "usable_battery_energy": recipe.actual_capacity_kwh,
        "baseline_auxiliary_power": recipe.auxiliary_power_w,
        "ambient_temperature": recipe.ambient_temperature_k,
        "scheduled_stop": recipe.scheduled_stop_s,
        "driver_sigma": protocol["driver_sigma_stop_heavy"]
        if recipe.traffic_regime == "stop_heavy"
        else protocol["driver_sigma_urban"],
    }
    if values["passenger_load"] < 0:
        raise ValueError("Loaded mass below declared curb mass")
    for name, value in values.items():
        p[name]["value"] = value
    bus["derived"] = {
        "loaded_mass_kg": recipe.loaded_mass_kg,
        "initial_usable_energy_J": recipe.actual_capacity_kwh * recipe.initial_soc * 3_600_000,
    }
    write(destination / "bus-parameters.json", bus)
    plant = json.loads((destination / "plant.json").read_bytes())
    non_hvac = min(3000, recipe.auxiliary_power_w)
    plant["energy"].update(
        non_hvac_power_w=non_hvac, hvac_power_w=recipe.auxiliary_power_w - non_hvac
    )
    # Imperfections affect observations, not SUMO's battery state. Seed/bias stay harness-only.
    plant["sampling"].update(
        seed=recipe.realization_seed + protocol["sensor_seed_offset"],
        faults={
            "battery.pack.energy": {
                "noise_std": protocol["energy_noise_std_j"],
                "quantum": protocol["energy_quantum_j"],
            },
            "battery.pack.soc": {
                "noise_std": protocol["soc_noise_std"],
                "quantum": protocol["soc_quantum"],
                "bias": protocol["soc_bias_magnitude"] * (1 if recipe.realization_seed % 2 else -1),
            },
        },
    )
    write(destination / "plant.json", plant)


def collect(mission, folder, osm, protocol):
    folder.mkdir()
    scenario = folder / "scenario"
    prepare_scenario(scenario, mission, protocol)
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "evdt-energy/" + mission.mission_id))
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, run_id + "/session"))
    source = SumoVehicleSource(
        scenario,
        osm,
        folder / "plant",
        "bus-001",
        protocol["maximum_samples"],
        paced=False,
        run_identity={
            "run_id": run_id,
            "session_id": session_id,
            "asset_id": "bus-001",
            "source_id": "sumo-adapter",
            "source_epoch": 1,
        },
    )
    contract = monitoring_contract()
    packets = []
    with (folder / "observations.jsonl").open("wb") as stream:
        for sequence, frame in enumerate(source.frames()):
            body = {
                "schema_version": "3.0.0",
                "catalog_version": "1.2.0",
                "mode": "simulation",
                "clock_domain": "simulation",
                "run_id": run_id,
                "session_id": session_id,
                "asset_id": "bus-001",
                "source_id": "sumo-adapter",
                "source_epoch": 1,
                "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, run_id + "/" + str(sequence))),
                "stream_seq": sequence,
                "sim_time_s": frame.sim_time_s,
                "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "manifest_sha256": source.manifest_sha256,
                "samples": frame.samples,
            }
            raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            admitted, digest = contract.validate(
                raw, observation_topic(run_id, "bus-001", "sumo-adapter"), run_id
            )
            if hashlib.sha256(raw).hexdigest() != digest:
                raise RuntimeError("Canonical observation digest mismatch")
            stream.write(raw + b"\n")
            packets.append(admitted)
    if not source.summary["mission_finished"] or len(packets) < 121:
        raise RuntimeError("Complete mission was not collected")
    rows = source.step_trace
    if (
        source.summary["energy_ledger"]["capacity_j"]
        != mission.recipe.actual_capacity_kwh * 3_600_000
    ):
        raise RuntimeError("Actual capacity was not applied")
    # Content hashes deliberately exclude run/event IDs, paths, wall times and recipes.
    causal = fingerprint(
        [
            [r["sim_time_s"], r["values"]["motion.speed"], r["values"]["motion.distance"]]
            for r in rows
        ]
    )
    observed = fingerprint([[p["sim_time_s"], p["samples"]] for p in packets])
    artifact = MissionArtifact(
        mission.mission_id,
        mission.mission_id,
        mission.family_id,
        mission.partition,
        mission.recipe_sha256,
        causal,
        observed,
    )
    files = {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in folder.rglob("*")
        if p.is_file()
    }
    endpoint = next(
        r for r in reversed(rows) if abs(r["sim_time_s"] - packets[-1]["sim_time_s"]) < 1e-8
    )
    summary = {
        "passed": True,
        "mission": asdict(mission),
        "artifact": asdict(artifact),
        "frames": len(packets),
        "physical_steps": len(rows),
        "summary": source.summary,
        "files_sha256": files,
        "final_truth": {
            "sim_time_s": endpoint["sim_time_s"],
            "energy_j": endpoint["values"]["battery.pack.energy"],
            "soc": endpoint["values"]["battery.pack.soc"],
        },
        "truth_rows_file": "plant/plant-steps.json",
    }
    write(folder / "collection.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use a new empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    design_path = ROOT / "qualification/energy/mission-design.json"
    protocol_path = ROOT / "qualification/energy/development-protocol.json"
    design = parse_design(json.loads(design_path.read_bytes()))
    protocol = json.loads(protocol_path.read_bytes())
    if protocol["collection"]["partitions"] != ["train", "validation"]:
        raise ValueError("Held-out collection is not authorized by this command")
    selected = [m for m in design.missions if m.partition in ("train", "validation")]
    report = {
        "passed": False,
        "scope": "Complete sequential synthetic development missions; not live load or real-bus evidence",
        "held_out_collected": False,
        "design_sha256": design.design_sha256,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "missions": [],
    }
    try:
        for i, mission in enumerate(selected, 1):
            summary = collect(
                mission, args.output / mission.mission_id, args.osm, protocol["collection"]
            )
            report["missions"].append(
                {
                    "mission_id": mission.mission_id,
                    "partition": mission.partition,
                    "artifact": summary["artifact"],
                    "collection_sha256": hashlib.sha256(
                        (args.output / mission.mission_id / "collection.json").read_bytes()
                    ).hexdigest(),
                }
            )
            write(
                args.output / "progress.json",
                {"completed": i, "total": len(selected), "latest": mission.mission_id},
            )
            print(f"Collected {i}/{len(selected)}: {mission.mission_id}", flush=True)
        artifacts = tuple(MissionArtifact(**r["artifact"]) for r in report["missions"])
        validate_inventory(design, artifacts, require_complete=False)
        if {a.mission_id for a in artifacts} != {m.mission_id for m in selected}:
            raise RuntimeError("Selected development inventory incomplete")
        if len({a.causal_trace_sha256 for a in artifacts}) != len(artifacts):
            raise RuntimeError("Mission driving realizations were not distinct")
        repeat = collect(
            selected[0], args.output / "repeatability", args.osm, protocol["collection"]
        )
        if (
            repeat["artifact"]["causal_trace_sha256"] != artifacts[0].causal_trace_sha256
            or repeat["artifact"]["observation_content_sha256"]
            != artifacts[0].observation_content_sha256
        ):
            raise RuntimeError("Seeded mission/measurement repeatability failed")
        report.update(
            passed=True,
            repeatability_passed=True,
            training_missions=sum(m.partition == "train" for m in selected),
            validation_missions=sum(m.partition == "validation" for m in selected),
        )
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    write(args.output / "dataset.json", report)
    print(f"Development collection passed: {report['passed']}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
