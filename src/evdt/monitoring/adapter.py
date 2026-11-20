"""Sample live simulated motion into the durable outbox; transport runs separately."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from evdt.monitoring.pacing import TIME_SCALES
from evdt.monitoring.registration_client import RegistrationClient
from evdt.monitoring.source import SumoVehicleSource
from evdt.monitoring.source_pause import SourcePause
from evdt.monitoring.trace import emit, packet, stamp
from evdt.monitoring.transport import open_spool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maximum-samples", type=int, default=30)
    parser.add_argument("--accelerated", action="store_true")
    parser.add_argument("--time-scale", type=float, choices=TIME_SCALES, default=1)
    parser.add_argument("--pause-at-s", type=float)
    parser.add_argument("--pause-seconds", type=float, default=0)
    parser.add_argument("--synchronized-start-dir", type=Path)
    parser.add_argument("--route-aware-energy", action="store_true")
    parser.add_argument("--pause-control", type=Path)
    args = parser.parse_args()
    if not 1 <= args.maximum_samples <= 100000:
        parser.error("Choose a bounded positive sample count")
    if args.time_scale != 1 and (args.accelerated or args.synchronized_start_dir):
        parser.error("Time scaling requires an independent paced source")
    if args.pause_control and (
        args.accelerated or args.synchronized_start_dir or args.pause_at_s is not None
    ):
        parser.error("Interactive pause requires an independent paced source")
    work = Path(os.environ.get("SOURCE_OUTPUT", "/source-output"))
    work.mkdir(parents=True, exist_ok=True)
    spool = open_spool()
    # This initial driver cannot resume a plant timeline from a previous producer.
    # Restart delivery freely; restart the plant only in a fresh explicitly named run.
    session = spool.start_session(require_fresh=True)
    identity = {**spool.identity.__dict__, "session_id": session, "source_epoch": 1}
    registration = RegistrationClient(identity, Path(os.environ["PRODUCER_TOKEN_FILE"]))
    start_clock = None
    if args.synchronized_start_dir:
        if args.accelerated:
            parser.error("Synchronized wall-time workloads cannot run accelerated")
        from evdt.workload.pacing import await_start

        def start_clock():
            return await_start(args.synchronized_start_dir, identity)

    source = SumoVehicleSource(
        Path(os.environ.get("SCENARIO_DIR", "/scenario")),
        Path(os.environ.get("OSM_FILE", "/input/roads.osm.xml")),
        work,
        spool.identity.asset_id,
        args.maximum_samples,
        not args.accelerated,
        identity,
        registration.register,
        registration.clock,
        args.pause_at_s,
        args.pause_seconds,
        spool.catalog_version,
        start_clock,
        (lambda raw: registration.post("/v2/energy/route-contexts", raw))
        if args.route_aware_energy
        else None,
        time_scale_factor=args.time_scale,
        pause_control=SourcePause(args.pause_control, registration.clock)
        if args.pause_control
        else None,
        on_held_charge=(lambda raw: registration.post("/v2/energy/held-charge-observations", raw))
        if args.route_aware_energy
        else None,
    )
    if (source.scenario / "prepared-journey.json").is_file():
        import signal

        def stop(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
    report = {
        "passed": False,
        "scope": "assumed coherent 20-signal plant, not calibrated vehicle physics",
        "mode": "simulation",
        "paced": not args.accelerated,
        "time_scale": args.time_scale if not args.accelerated else None,
        "session_id": session,
        "events": [],
    }
    frames = source.frames()
    try:
        for frame in frames:
            ready = stamp()
            event = spool.enqueue(
                frame.samples,
                frame.sim_time_s,
                annotations={
                    "manifest_sha256": source.manifest_sha256,
                    "model_version": "synthetic-bus-plant/1.0.0",
                    **frame.annotations,
                },
                manifest_sha256=source.manifest_sha256,
                observed_at=datetime.fromtimestamp(ready["utc_s"], timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            )
            queued = stamp()
            correlation = packet(event.raw)
            emit(
                "sample_ready",
                correlation,
                at=ready,
                stream_seq=json.loads(event.raw)["stream_seq"],
            )
            emit("enqueue_committed", correlation, at=queued, wire_sha256=event.wire_sha256)
            report["events"].append(
                {
                    "event_id": event.event_id,
                    "wire_sha256": hashlib.sha256(event.raw).hexdigest(),
                    "body": json.loads(event.raw),
                }
            )
        report["passed"] = not source.summary.get("moving_charge", {}).get("interrupted") and (
            len(report["events"]) == args.maximum_samples
            or bool(
                (source.scenario / "prepared-journey.json").is_file()
                and source.summary.get("mission_finished")
                and report["events"]
            )
        )
    finally:
        frames.close()
        report["plant_summary"] = source.summary
        report["manifest_sha256"] = source.manifest_sha256
        (work / "source-report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["passed"]:
        raise RuntimeError("Source did not emit the expected bounded sample count")


if __name__ == "__main__":
    main()
