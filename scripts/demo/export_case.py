"""Verify a matched pair and export an allowlisted semantic browser case.

This is a new public data contract, never an original-packet replay. The source
audit and original identities remain beside the private pair, outside the case.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = "branched-trip-demo/1.1.0"
FORK = 30
LIMIT = 16 * 1024 * 1024


def read(path):
    return json.loads(path.read_bytes())


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def captures(directory):
    rows = [
        json.loads(line)
        for line in (directory / "demo-capture.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    prefix = directory / "demo-prefix-context.jsonl"
    if prefix.exists():
        rows.extend(json.loads(line) for line in prefix.read_text(encoding="utf-8").splitlines())
    return rows


def physical_prefix(report):
    return [
        {
            "t": e["body"]["sim_time_s"],
            "samples": sorted(e["body"]["samples"], key=lambda s: s["signal_id"]),
        }
        for e in report["events"]
        if e["body"]["sim_time_s"] <= FORK
    ]


def domain_context(row):
    context = row["body"].get("context")
    if not context:
        return None
    # Runtime identity, host clocks, and manifest identity are excluded explicitly.
    return {
        key: context[key]
        for key in (
            "working_s",
            "plan",
            "external_index",
            "lane_id",
            "lane_offset_m",
            "charging_state",
            "current_stop_id",
            "remaining_dwell_working_s",
        )
        if key in context
    }


def source_receipt(directory):
    # launch.json is a single-use admission token consumed by the Linux worker.
    # The completed Windows supervisor retains the guarded source fingerprint.
    receipt = read(directory / "operator-result.json").get("source")
    if (
        not isinstance(receipt, dict)
        or type(receipt.get("files")) is not int
        or receipt["files"] <= 0
        or not isinstance(receipt.get("sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", receipt["sha256"]) is None
    ):
        raise ValueError("Missing or invalid durable source inventory receipt")
    return receipt


def verify_pair(protocol):
    if (
        protocol.get("version") != "browser-demo-capture/1.0.0"
        or type(protocol.get("fork_working_s")) is not int
        or not 1 <= protocol["fork_working_s"] <= FORK
    ):
        raise ValueError("Unsupported recording protocol")
    profile = protocol["profile"]
    if profile.get("time_scale") != 1 or profile.get("starting_charge_percent") != 11.6:
        raise ValueError("Unreviewed demonstration inputs")
    runs = {}
    for branch in ("continue", "charge"):
        entry = protocol["outcomes"][branch]
        directory = (ROOT / entry["directory"]).resolve(strict=True)
        if not directory.is_relative_to(ROOT / "evidence/energy-v2"):
            raise ValueError("Recording must remain in private project evidence")
        result = read(directory / "operator-result.json")
        session = result.get("session", {})
        if (
            entry["exit_code"]
            or entry["actor_errors"]
            or not result.get("passed")
            or not (
                session.get("cleanup_verified")
                and session.get("backup_saved")
                and session.get("status") == "arrived"
            )
        ):
            raise ValueError(branch + ": recording, backup, or cleanup not accepted")
        if read(directory / "profile.json") != profile:
            raise ValueError("The pair used different input profiles")
        report = read(directory / "source-report.json")
        if not report.get("passed") or not report["plant_summary"]["mission_finished"]:
            raise ValueError("The actual SUMO mission did not complete")
        ledger = report["plant_summary"].get("energy_ledger", {})
        if not ledger or ledger["maximum_residual_j"] > ledger["tolerance_j"]:
            raise ValueError("The plant energy balance did not reconcile")
        runs[branch] = (directory, report, captures(directory))
    a, b = runs["continue"], runs["charge"]
    manifest_fields = (
        "sumo_version",
        "traci_protocol",
        "packages",
        "model_version",
        "step_s",
        "initial_energy_j",
        "capacity_j",
        "sampling",
        "catalog_version",
    )
    manifests = [read(run[0] / "run-manifest.json") for run in (a, b)]
    manifests = [{k: manifest[k] for k in manifest_fields} for manifest in manifests]
    if manifests[0] != manifests[1]:
        raise ValueError("Simulator, plant, battery, or sampling configuration changed")
    prefix = physical_prefix(a[1])
    if len(prefix) != FORK or prefix != physical_prefix(b[1]) or prefix[-1]["t"] != FORK:
        raise ValueError("Physical observation prefix differs; do not splice or relax tolerances")
    steps = [
        [s for s in read(run[0] / "plant-steps.json") if s["sim_time_s"] <= FORK] for run in (a, b)
    ]
    if not steps[0] or steps[0] != steps[1]:
        raise ValueError("Full-resolution physical prefix differs")
    # Compare the checkpoint's route occurrence, stop plan, and battery context.
    contexts = []
    for _, _, rows in (a, b):
        matching = [
            domain_context(r)
            for r in rows
            if r["kind"] == "context"
            and r["http"] == 200
            and (domain_context(r) or {}).get("working_s") == FORK
        ]
        if not matching:
            raise ValueError("The source route context at the checkpoint was not captured")
        contexts.append(matching[-1])
        planned = {s["stop_id"] for s in matching[-1]["plan"]["remaining_stops"]}
        served = {
            c.get("current_stop_id")
            for r in rows
            if r["kind"] == "context" and r["http"] == 200 and (c := domain_context(r))
        }
        if not planned or not planned.issubset(served):
            raise ValueError("Not all planned passenger stops were observed as served")
        offers = [
            r["body"]["estimate"]
            for r in rows
            if r["kind"] == "advisory"
            and r["http"] == 200
            and r["available_working_s"] <= FORK
            and r["body"].get("estimate")
        ]
        if not any(o["bay_id"] == protocol["selected_bay"] for o in offers):
            raise ValueError("The reviewed bay was not recommended before the checkpoint")
    if contexts[0] != contexts[1]:
        raise ValueError("The route/stop/context state differs at the checkpoint")
    moving = b[1]["plant_summary"]["moving_charge"]
    events = moving.get("events", [])
    by_state = {e["event"]: e for e in events}
    required = ("stop_inserted", "charging_observed", "target_observed", "motion_resumed")
    if any(state not in by_state for state in required):
        raise ValueError("Incomplete command-to-measured-effect chain")
    if by_state["stop_inserted"].get("bay_id") != protocol["selected_bay"]:
        raise ValueError("A different station was applied")
    times = [by_state[s]["working_s"] for s in required]
    if times != sorted(times) or times[0] < FORK or moving.get("delivered_j", 0) <= 0:
        raise ValueError("Charging happened before the decision or without measured transfer")
    approvals = [r for r in b[2] if r["kind"] == "approval" and r["http"] == 200]
    if len(approvals) != 1 or approvals[0]["available_working_s"] < FORK:
        raise ValueError("Expected one successful scoped approval after the checkpoint")
    measured = [
        r["body"]["observed_effect"]["delivered_j"]
        for r in b[2]
        if r["kind"] == "advisory" and r["http"] == 200 and r["body"].get("observed_effect")
    ]
    if not measured or abs(max(measured) - moving["delivered_j"]) > 0.01:
        raise ValueError("The independently observed charging total does not reconcile")
    if any(r["kind"] == "approval" for r in a[2]):
        raise ValueError("The continue outcome received a charging command")
    if a[1]["plant_summary"].get("moving_charge", {}).get("delivered_j", 0):
        raise ValueError("The continue outcome received external energy")
    source = source_receipt(a[0])
    if source != source_receipt(b[0]):
        raise ValueError("Source inventory changed between recordings")
    return runs, {
        "observations": digest(prefix),
        "plant_steps": digest(steps[0]),
        "checkpoint_domain": digest(contexts[0]),
        "simulator_configuration": manifests[0],
        "source": source,
        "capture_fork_working_s": protocol["fork_working_s"],
        "decision_working_s": FORK,
        "command_effect_working_s": times,
    }


def export_case(protocol_path, output):
    protocol = read(protocol_path)
    runs, checks = verify_pair(protocol)
    if output.exists():
        raise ValueError("Use a new output directory; accepted candidates are immutable")
    first = runs["continue"][1]["events"][0]["body"]
    signals = [
        {
            "id": s["signal_id"],
            "unit": s["unit"],
            "freshness": 5,
            "provenance": "Recorded SUMO observation",
        }
        for s in sorted(first["samples"], key=lambda s: s["signal_id"])
    ]
    ids = [s["id"] for s in signals]
    if len(ids) != 20:
        raise ValueError("The case must retain all twenty signals")
    audit = {
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "checks": checks,
        "runs": {},
        "forecast_identity_map": {},
    }
    maps = [r for r in runs["continue"][2] if r["kind"] == "map" and r["http"] == 200]
    if not maps:
        raise ValueError("No recorded map context")
    context = maps[0]["body"]
    features = []
    station_ids = {
        f["properties"]["id"]: f"bay-{i + 1}"
        for i, f in enumerate(
            sorted(
                (f for f in context["features"] if f["properties"].get("layer") == "stations"),
                key=lambda f: f["properties"]["id"],
            )
        )
    }
    audit["station_identity_map"] = station_ids
    allowed_properties = ("id", "layer", "name", "power_limit_W", "road_access", "duration_s")
    for f in context["features"]:
        p = {k: f["properties"][k] for k in allowed_properties if k in f["properties"]}
        if p.get("layer") == "stations":
            original = p["id"]
            p["id"] = station_ids[original]
            match = re.search(r"bay-(\d+)$", original)
            p["name"] = (
                "Off-route charging bay " if original.startswith("detour-") else "Charging bay "
            ) + (match[1] if match else p["id"].split("-")[-1])
        features.append({"type": "Feature", "geometry": f["geometry"], "properties": p})
    map_data = {
        "type": "FeatureCollection",
        "context_version": context["context_version"],
        "run_id": "browser-demo",
        "asset_id": "bus-001",
        "mission_id": "montreal-charge-v2",
        "bbox": context["bbox"],
        "attribution": "OpenStreetMap contributors · ODbL-1.0",
        "input_sha256": {},
        "features": features,
    }
    timelines = {}
    offer = None
    for branch, (directory, report, rows) in runs.items():
        frames = []
        for event in report["events"]:
            body = event["body"]
            samples = {s["signal_id"]: s for s in body["samples"]}
            values = [samples[s]["value"] for s in ids]
            qualities = [samples[s]["quality"] for s in ids]
            frame = [body["sim_time_s"], values]
            if any(q != "good" for q in qualities):
                frame.append(qualities)
            frames.append(frame)
        forecasts, progress, transfers, events, seen, previous = [], [], [], [], set(), None
        comparisons, seen_comparisons = [], set()
        for row in rows:
            if row["http"] != 200:
                continue
            t, body = row["available_working_s"], row["body"]
            if row["kind"] == "snapshot" and body.get("trip_progress"):
                p = body["trip_progress"]
                state = {
                    "remainingM": p.get("remaining_m"),
                    "atStop": p.get("at_passenger_stop", False),
                }
                if state != previous:
                    progress.append({"t": t, **state})
                    previous = state
            if row["kind"] == "forecasts":
                for item in body.get("comparisons", []):
                    p = item["payload"]
                    key = (item["fence"], p["from_working_s"], p["until_working_s"])
                    if key in seen_comparisons:
                        continue
                    seen_comparisons.add(key)
                    if p["until_working_s"] > t:
                        raise ValueError("Comparison presented before its observed interval")
                    comparisons.append(
                        {
                            "available": t,
                            "from": p["from_working_s"],
                            "until": p["until_working_s"],
                            "predicted": p["predicted_net_j"],
                            "observed": p["observed_net_j"],
                            "error": p["signed_error_j"],
                        }
                    )
                for item in reversed(body.get("items", [])):
                    fence, payload = item["fence"], item["payload"]
                    if fence in seen:
                        continue
                    seen.add(fence)
                    summary = payload.get("summary")
                    public_id = f"forecast-{branch}-{len(forecasts) + 1}"
                    f = {
                        "id": public_id,
                        "available": t,
                        "cutoff": payload["cutoff_working_s"],
                        "model": payload["model_version"],
                        "inputId": "input-" + public_id,
                        "routeId": "route-" + payload["route_sha256"][:12],
                        "trigger": item.get("trigger", "recorded"),
                        "status": payload["status"],
                        "reason": payload.get("reason"),
                        "summary": None,
                    }
                    if summary:
                        ledger = summary["ledger"]
                        f["summary"] = {
                            "arrival": summary["arrival_soc"],
                            "minimum": summary["minimum_soc"],
                            "duration": summary["duration_working_s"],
                            "distance": summary["remaining_distance_m"],
                            "energyKwh": ledger["net_battery_j"] / 3_600_000,
                            "drawKwh": ledger["battery_draw_j"] / 3_600_000,
                            "recoveryKwh": ledger["battery_recovery_j"] / 3_600_000,
                            "auxKwh": ledger["auxiliary_required_j"] / 3_600_000,
                            "capacityKwh": ledger["capacity_j"] / 3_600_000,
                            "assumptions": ledger["assumptions"],
                            "ledger": {
                                key: ledger[key]
                                for key in (
                                    "initial_energy_j",
                                    "capacity_j",
                                    "net_battery_j",
                                    "battery_draw_j",
                                    "battery_recovery_j",
                                    "auxiliary_required_j",
                                    "propulsion_required_j",
                                    "regeneration_used_j",
                                    "unserved_energy_j",
                                    "rejected_regeneration_j",
                                    "reserve_breached",
                                    "motion_feasible",
                                    "assumptions",
                                )
                            },
                        }
                    if t < f["cutoff"]:
                        raise ValueError("Forecast presented before its input cutoff")
                    forecasts.append(f)
                    audit["forecast_identity_map"][public_id] = {
                        "directory": str(directory.relative_to(ROOT)),
                        "fence": fence,
                        "input_sha256": payload["input_sha256"],
                    }
            if row["kind"] == "advisory" and t <= FORK and body.get("estimate"):
                offer = offer or body
            if row["kind"] == "advisory" and body.get("observed_effect"):
                effect = body["observed_effect"]
                transfer = {
                    "t": max(t, effect["observed_working_s"]),
                    "state": effect["state"],
                    "deliveredKwh": effect["delivered_j"] / 3_600_000,
                }
                if (
                    not transfers
                    or transfer["state"] != transfers[-1]["state"]
                    or transfer["deliveredKwh"] != transfers[-1]["deliveredKwh"]
                ):
                    transfers.append(transfer)
        moving = report["plant_summary"].get("moving_charge", {})
        titles = {
            "stop_inserted": "Charging stop applied",
            "charging_observed": "Charging measured",
            "target_observed": "Charge target reached",
            "motion_resumed": "Driving resumed",
        }
        if branch == "charge":
            grant = next(r for r in rows if r["kind"] == "approval" and r["http"] == 200)
            events.append(
                {
                    "t": grant["available_working_s"],
                    "kind": "approval",
                    "title": "Charging approval accepted",
                }
            )
            for e in moving["events"]:
                if e["event"] in titles:
                    events.append(
                        {"t": e["working_s"], "kind": e["event"], "title": titles[e["event"]]}
                    )
        soc_index = ids.index("battery.pack.soc")
        crossing = next(
            (f[0] for f in frames if f[1][soc_index] is not None and f[1][soc_index] < 0.1), None
        )
        if crossing is not None:
            events.append(
                {"t": crossing, "kind": "reserve", "title": "Measured charge below 10% reserve"}
            )
        end = report["plant_summary"]["completed_sim_time_s"]
        events.append({"t": end, "kind": "arrived", "title": "Trip complete"})
        events.sort(key=lambda e: e["t"])
        minimum = min(s["values"]["battery.pack.soc"] for s in read(directory / "plant-steps.json"))
        summary = {
            "duration": end,
            "arrivalSoc": frames[-1][1][soc_index],
            "minimumSoc": minimum,
            "deliveredKwh": moving.get("delivered_j", 0) / 3_600_000,
            "reserveCrossing": crossing,
            "completed": True,
        }
        timelines[branch] = {
            "frames": frames,
            "forecasts": forecasts,
            "progress": progress,
            "transfers": transfers,
            "events": events,
            "summary": summary,
            "comparisons": comparisons,
        }
        audit["runs"][branch] = {
            "directory": directory.relative_to(ROOT).as_posix(),
            "source_report_sha256": hashlib.sha256(
                (directory / "source-report.json").read_bytes()
            ).hexdigest(),
        }
    if not offer:
        raise ValueError("No recommendation was available by the checkpoint")
    if (
        timelines["charge"]["summary"]["minimumSoc"] < 0.1
        or timelines["continue"]["summary"]["minimumSoc"] >= 0.1
    ):
        raise ValueError("The measured outcomes do not establish the intended reserve contrast")
    # No retrospective future forecast is smuggled into the common prefix.
    common = {
        "frames": [f for f in timelines["continue"]["frames"] if f[0] <= FORK],
        "forecasts": [f for f in timelines["continue"]["forecasts"] if f["available"] <= FORK],
        "progress": [p for p in timelines["continue"]["progress"] if p["t"] <= FORK],
        "events": [],
        "transfers": [],
        "comparisons": [c for c in timelines["continue"]["comparisons"] if c["available"] <= FORK],
    }
    for branch in timelines:
        timelines[branch]["comparisons"] = [
            c for c in timelines[branch]["comparisons"] if c["available"] > FORK
        ]
        timelines[branch]["frames"] = [f for f in timelines[branch]["frames"] if f[0] > FORK]
        timelines[branch]["forecasts"] = [
            f for f in timelines[branch]["forecasts"] if f["available"] > FORK
        ]
        timelines[branch]["progress"] = [p for p in timelines[branch]["progress"] if p["t"] > FORK]
    estimate = offer["estimate"]
    # Fields are named from the declared estimate, never from arbitrary nested JSON.
    station_id = station_ids[estimate["bay_id"]]
    station = next(
        (
            f
            for f in features
            if f["properties"].get("layer") == "stations"
            and f["properties"].get("id") == station_id
        ),
        None,
    )
    if station is None:
        raise ValueError("Recommended station is absent from the recorded map")
    target = estimate.get("target_soc") or estimate.get("departure_soc")
    if target is None:
        raise ValueError("No explicit departure target was captured")
    decision = {
        "at": FORK,
        "stationId": station_id,
        "stationName": station["properties"]["name"],
        "targetSoc": target,
        "chargingSeconds": estimate.get("charging_working_s", estimate.get("charge_working_s")),
        "minimumSocWithStop": estimate["minimum_soc"],
        "arrivalSocWithStop": estimate["destination_soc"],
        "arrivalWorkingS": estimate["arrival_working_s"],
        "waitSeconds": estimate["wait_working_s"],
        "energyKwh": estimate["estimated_grid_energy_j"] / 3_600_000,
        "choices": ["charge", "continue"],
    }
    payloads = {
        "catalog": signals,
        "map": map_data,
        "common": common,
        "charge": timelines["charge"],
        "continue": timelines["continue"],
    }
    output.mkdir(parents=True)
    files = {}
    total = 0
    for key, value in payloads.items():
        raw = canonical(value) + b"\n"
        if len(raw) > 2_000_000:
            raise ValueError(
                "Split the reviewed data; individual source assets must stay under 2 MB"
            )
        name = key + ".json"
        (output / name).write_bytes(raw)
        files[key] = {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        total += len(raw)
    frames = len(common["frames"]) + sum(len(t["frames"]) for t in timelines.values())
    if frames > 10_000 or total > LIMIT:
        raise ValueError("Public case resource bounds exceeded")
    manifest = {
        "version": VERSION,
        "id": "montreal-charge-v2",
        "title": protocol["profile"]["title"],
        "region": "Montréal",
        "basis": "Recorded " + checks["simulator_configuration"]["sumo_version"] + " simulation",
        "initialSoc": 0.116,
        "reserveSoc": 0.1,
        "decision": decision,
        "files": files,
        "attribution": [
            "OpenStreetMap contributors · ODbL-1.0",
            "Elevation: Natural Resources Canada",
            "SUMO reference readings; simulated charging bays and availability",
        ],
        "evidence": {
            "kind": "Matched demonstration recordings; not a reserved accuracy evaluation",
            "prefixSha256": checks["observations"],
            "sourceRevision": checks["source"]["sha256"],
        },
    }
    (output / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    # A new export review must not overwrite the retained earlier checkpoint.
    audit_path = protocol_path.parent / (
        output.parent.name + "-" + output.name + "-export-audit.json"
    )
    if audit_path.exists() and audit_path.read_bytes() != canonical(audit) + b"\n":
        raise ValueError("Export audit already exists with different contents")
    audit_path.write_bytes(canonical(audit) + b"\n")
    print(
        f"Verified semantic case: {frames} frames; {total:,} bytes. Private audit saved beside protocol."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = args.protocol.resolve(strict=True)
    output = args.output.resolve()
    if not protocol.is_relative_to(ROOT / "evidence/energy-v2") or not output.is_relative_to(ROOT):
        parser.error("Use private evidence inputs and a new project-local candidate directory")
    export_case(protocol, output)


if __name__ == "__main__":
    main()
