"""Independently reconcile streamed packet bytes against actual stepped plant values."""

import base64
import hashlib
import json
import math


def require(value, message):
    if not value:
        raise ValueError(message)


def audit(directory, scenario):
    summary = json.loads((directory / "source-summary.json").read_bytes())
    require(summary["passed"] and summary["paced"] is False, "Source prefix is incomplete")
    manifest_raw = (directory / "plant/run-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    fingerprint = hashlib.sha256(manifest_raw).hexdigest()
    require(fingerprint == summary["manifest_sha256"], "Source manifest bytes changed")
    require(manifest["identity"] == summary["identity"], "Source/manifest identity changed")
    mission = json.loads((scenario / "mission.json").read_bytes())
    step = mission["step_s"]
    trace = json.loads((directory / "plant/plant-steps.json").read_bytes())
    expected_steps = round(summary["last_sim_time_s"] / step) - 1
    require(len(trace) == expected_steps, "Incomplete physical step trace")
    sampled = {}
    for index, row in enumerate(trace):
        t = row["sim_time_s"]
        require(math.isfinite(t) and abs(t - (index + 2) * step) <= 1e-8, "Physical step gap")
        if abs(t - round(t)) <= 1e-8:
            sampled[round(t)] = row["values"]
    require(len(sampled) == summary["expected_events"], "Incomplete sampled physical trace")
    digest = hashlib.sha256()
    events, scalars, maximum = 0, 0, 0
    distance = -1.0
    with (directory / "originals.jsonl").open("rb") as stream:
        while line := stream.readline(100000):
            require(
                len(line) < 100000 and line.endswith(b"\n"), "Unbounded or truncated packet record"
            )
            record = json.loads(line)
            raw = base64.b64decode(record["raw_base64"], validate=True)
            require(
                hashlib.sha256(raw).hexdigest() == record["wire_sha256"],
                "Original packet bytes changed",
            )
            body = json.loads(raw)
            require(
                all(body[k] == v for k, v in summary["identity"].items()), "Packet identity changed"
            )
            require(
                body["event_id"] == record["event_id"] and body["manifest_sha256"] == fingerprint,
                "Packet/recipe binding changed",
            )
            require(
                body["stream_seq"] == events and abs(body["sim_time_s"] - (events + 1)) <= 1e-8,
                "Packet sequence/cadence gap",
            )
            values = sampled[events + 1]
            samples = body["samples"]
            require(
                len(samples) == len(values) and {s["signal_id"] for s in samples} == set(values),
                "Packet signal coverage changed",
            )
            for sample in samples:
                original = (
                    sample["value"]
                    if sample.get("quality", "good") == "good"
                    else sample.get("raw_value")
                )
                require(
                    original == values[sample["signal_id"]],
                    "Packet value differs from the actual physical step",
                )
            require(
                values["motion.distance"] >= distance, "Mission distance reset inside the prefix"
            )
            distance = values["motion.distance"]
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
            events += 1
            scalars += len(samples)
            maximum = max(maximum, len(raw))
    require(events == summary["events"] == summary["expected_events"], "Packet count mismatch")
    require(
        scalars == summary["scalar_rows"] == events * summary["signals_per_event"],
        "Scalar count mismatch",
    )
    require(
        digest.hexdigest() == summary["original_packets_sha256"],
        "Original stream fingerprint changed",
    )
    require(maximum == summary["maximum_wire_bytes"], "Packet size summary mismatch")
    return {
        "passed": True,
        "events": events,
        "scalar_rows": scalars,
        "physical_steps": len(trace),
        "all_packet_values_match_physical_steps": True,
        "original_bytes_and_cadence_checked": True,
        "distance_never_reset": True,
    }
