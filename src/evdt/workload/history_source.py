"""Stream actual accelerated source packets for history seeding, without transport claims."""

import argparse
import base64
import hashlib
import json
import os
import resource
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evdt.monitoring.contracts import monitoring_contract, observation_topic
from evdt.monitoring.formats import operational_format
from evdt.monitoring.source import SumoVehicleSource


def encode_frame(identity, sequence, frame, manifest_sha256, catalog_version, observed_at):
    body = {
        **identity,
        "schema_version": operational_format(catalog_version)["schema_version"],
        "catalog_version": catalog_version,
        "event_id": str(uuid.uuid5(uuid.UUID(identity["session_id"]), str(sequence))),
        "stream_seq": sequence,
        "mode": "simulation",
        "clock_domain": "simulation",
        "sim_time_s": frame.sim_time_s,
        "observed_at": observed_at,
        "manifest_sha256": manifest_sha256,
        "annotations": {
            "history_fixture": "accelerated source / database seeding; not live transport"
        },
        "samples": frame.samples,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def generate(output, scenario, osm, identity, count, catalog_version="1.4.0", registration=None):
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    contract = monitoring_contract()
    signals = set(contract.readers[catalog_version].catalog["signals"])
    source = SumoVehicleSource(
        scenario,
        osm,
        output / "plant",
        identity["asset_id"],
        count,
        paced=False,
        run_identity=identity,
        catalog_version=catalog_version,
        on_manifest=registration.register if registration else None,
        on_clock=(
            lambda state, sim_time: (
                registration.clock(state, sim_time) if state == "finished" else None
            )
        )
        if registration
        else None,
    )
    frames = source.frames()
    report = {
        "passed": False,
        "scope": "Actual accelerated source prefix for history seeding; not live delivery, wall-time workload or calibrated bus physics",
        "identity": identity,
        "paced": False,
        "registration_mode": "registered-before-generation"
        if registration
        else "offline-unregistered",
        "clock_mode": "initial registered zero and actual finished time; no live heartbeat claim"
        if registration
        else "offline; no registry clock",
        "expected_events": count,
        "events": 0,
        "scalar_rows": 0,
        "signals_per_event": len(signals),
        "first_sim_time_s": None,
        "last_sim_time_s": None,
        "maximum_wire_bytes": 0,
    }
    digest = hashlib.sha256()
    previous = None
    try:
        with (output / "originals.jsonl").open("xb") as packets:
            for sequence, frame in enumerate(frames):
                if previous is not None and abs(frame.sim_time_s - previous - 1.0) > 1e-8:
                    raise ValueError("History fixture requires the frozen one-second cadence")
                if (
                    len(frame.samples) != len(signals)
                    or {s["signal_id"] for s in frame.samples} != signals
                ):
                    raise ValueError(
                        "History fixture did not produce the complete registered signal set"
                    )
                raw = encode_frame(
                    identity,
                    sequence,
                    frame,
                    source.manifest_sha256,
                    catalog_version,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                )
                contract.validate(
                    raw,
                    observation_topic(
                        identity["run_id"], identity["asset_id"], identity["source_id"]
                    ),
                    identity["run_id"],
                )
                row = {
                    "event_id": str(uuid.uuid5(uuid.UUID(identity["session_id"]), str(sequence))),
                    "wire_sha256": hashlib.sha256(raw).hexdigest(),
                    "raw_base64": base64.b64encode(raw).decode("ascii"),
                }
                packets.write(
                    json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                )
                digest.update(len(raw).to_bytes(8, "big"))
                digest.update(raw)
                report["events"] += 1
                report["scalar_rows"] += len(frame.samples)
                report["maximum_wire_bytes"] = max(report["maximum_wire_bytes"], len(raw))
                if previous is None:
                    report["first_sim_time_s"] = frame.sim_time_s
                report["last_sim_time_s"] = frame.sim_time_s
                previous = frame.sim_time_s
            packets.flush()
            os.fsync(packets.fileno())
        if report["events"] != count or report["scalar_rows"] != count * len(signals):
            raise ValueError("Source ended before the complete history prefix")
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        try:
            frames.close()
        except Exception as exc:
            report.update(passed=False, close_error=str(exc))
        report.update(
            original_packets_sha256=digest.hexdigest(),
            manifest_sha256=source.manifest_sha256,
            plant_summary=source.summary,
            elapsed_wall_s=time.monotonic() - started,
            source_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            child_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        )
        peak = Path("/sys/fs/cgroup/memory.peak")
        report["container_memory_peak_bytes"] = int(peak.read_text()) if peak.is_file() else None
        (output / "source-summary.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, default=Path("/scenario"))
    parser.add_argument("--osm", type=Path, default=Path("/input/roads.osm.xml"))
    parser.add_argument("--run-id", required=True, type=uuid.UUID)
    parser.add_argument("--asset-id", required=True, choices=[f"bus-{i:03}" for i in range(1, 11)])
    parser.add_argument("--maximum-samples", type=int, required=True)
    parser.add_argument(
        "--register",
        action="store_true",
        help="Register actual recipe before generating this offline history source",
    )
    args = parser.parse_args()
    if not 1 <= args.maximum_samples <= 7200:
        parser.error("Choose a bounded history prefix of 1..7200 samples")
    identity = {
        "run_id": str(args.run_id),
        "asset_id": args.asset_id,
        "source_id": "sumo-adapter",
        "session_id": str(uuid.uuid4()),
        "source_epoch": 1,
    }
    registration = None
    if args.register:
        from evdt.monitoring.registration_client import RegistrationClient

        registration = RegistrationClient(identity, Path(os.environ["PRODUCER_TOKEN_FILE"]))
    report = generate(
        args.output,
        args.scenario,
        args.osm,
        identity,
        args.maximum_samples,
        registration=registration,
    )
    print(
        "HISTORY_SOURCE_RESULT="
        + json.dumps(
            {
                k: report[k]
                for k in [
                    "passed",
                    "events",
                    "scalar_rows",
                    "elapsed_wall_s",
                    "container_memory_peak_bytes",
                ]
            }
        ),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
