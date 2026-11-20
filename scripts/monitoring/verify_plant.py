"""Run two complete accelerated missions and verify actual 20-signal plant behavior."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract
from evdt.monitoring.source import SumoVehicleSource


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=Path("/scenario"))
    parser.add_argument("--osm", type=Path, default=Path("/input/roads.osm.xml"))
    parser.add_argument("--asset", default="bus-001")
    parser.add_argument("--catalog-version", default="1.2.0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new private output directory")
    args.output.mkdir(parents=True)
    report = {
        "passed": False,
        "scope": "Assumed plant consistency/repeatability, not real-bus accuracy",
        "runs": [],
    }
    try:
        step_s = json.loads((args.scenario / "mission.json").read_text())["step_s"]
        catalog = monitoring_contract().readers[args.catalog_version].catalog
        for index in (1, 2):
            source = SumoVehicleSource(
                args.scenario,
                args.osm,
                args.output / str(index),
                args.asset,
                5000,
                paced=False,
                catalog_version=args.catalog_version,
            )
            frames = list(source.frames())
            rows = source.step_trace
            require(source.summary["mission_finished"], "Full plant mission did not finish")
            require(
                source.summary["insertion"]["device_step_j"] == 0,
                "Insertion was not an explicit initialization step",
            )
            require(
                len(frames) > 800 and len(rows) > 4000, "Insufficient complete mission coverage"
            )
            require(
                set(source.summary["sensor_period_ticks"]) == set(catalog["signals"]),
                "Configured catalog coverage changed",
            )
            for frame in frames:
                tick = round(frame.sim_time_s / step_s)
                expected = {
                    name
                    for name, period in source.summary["sensor_period_ticks"].items()
                    if tick % period == 0
                }
                require(
                    {s["signal_id"] for s in frame.samples} == expected, "Missing initial signal"
                )
            stopped = [r["values"] for r in rows if r["values"]["motion.speed"] < 1e-9]
            regen = [r for r in rows if r["values"]["battery.pack.power"] < 0]
            require(
                any(abs(r["battery.pack.power"] - r["auxiliary.power"]) < 1e-5 for r in stopped),
                "Stopped auxiliary load not drawn from SUMO battery",
            )
            require(bool(regen), "Regenerative intervals not exercised")
            for row in rows:
                v = row["values"]
                require(
                    0 <= v["motion.heading"] <= 2 * math.pi
                    and abs(v["motion.heading"] - row["sumo_heading_deg"] * math.pi / 180) < 1e-12,
                    "SUMO degree heading was not converted to catalog radians",
                )
                require(
                    abs(
                        v["battery.pack.current"] * v["battery.pack.voltage"]
                        - v["battery.pack.power"]
                    )
                    < 1e-6,
                    "Power/voltage/current mismatch",
                )
                require(
                    abs(
                        v["battery.pack.soc"] * source.summary["energy_ledger"]["capacity_j"]
                        - v["battery.pack.energy"]
                    )
                    < 1e-5,
                    "SOC/energy mismatch",
                )
            if args.catalog_version == "1.3.0":
                boundary = json.loads((args.scenario / "sensor-extension.json").read_text())
                ambient = json.loads((args.scenario / "bus-parameters.json").read_text())[
                    "parameters"
                ]["ambient_temperature"]["value"]
                target = ambient + boundary["ambient_offset_k"]
                for row in rows:
                    expected_temp = target + (boundary["initial_k"] - target) * math.exp(
                        -(row["sim_time_s"] - step_s) / boundary["response_time_s"]
                    )
                    require(
                        abs(row["values"]["coolant.inlet.temperature"] - expected_temp) < 1e-9,
                        "Coolant analytic response mismatch",
                    )
            digest = hashlib.sha256(
                json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            report["runs"].append(
                {
                    "sample_events": len(frames),
                    "scalar_samples": sum(len(f.samples) for f in frames),
                    "trace_steps": len(rows),
                    "trace_sha256": digest,
                    "manifest_sha256": source.manifest_sha256,
                    "stopped_steps": len(stopped),
                    "regenerative_steps": len(regen),
                    "heading_conversion_checked_steps": len(rows),
                    **source.summary,
                }
            )
        require(
            report["runs"][0]["trace_sha256"] == report["runs"][1]["trace_sha256"],
            "Repeated plant trajectories differ",
        )
        require(
            report["runs"][0]["manifest_sha256"] == report["runs"][1]["manifest_sha256"],
            "Identical configured runs have different reproducibility manifests",
        )
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        (args.output / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")
        print("PLANT_RESULT=" + json.dumps(report), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
