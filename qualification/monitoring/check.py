"""Cross-language operational-contract expectations, separate from P0 fixtures."""

import copy
import hashlib
import json
import subprocess
import tempfile
import uuid
from pathlib import Path

from evdt.config import ROOT
from evdt.contracts import Rejected
from evdt.monitoring.contracts import monitoring_contract, observation_topic


def main():
    for name, expected in json.loads(
        (ROOT / "qualification/monitoring/immutable.json").read_text()
    ).items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Historical monitoring contract changed: " + name)
    run = str(uuid.UUID(int=123))
    body = {
        "schema_version": "2.0.0",
        "catalog_version": "1.0.0",
        "event_id": str(uuid.UUID(int=1)),
        "asset_id": "bus-001",
        "source_id": "sumo-adapter",
        "source_epoch": 1,
        "session_id": str(uuid.UUID(int=2)),
        "stream_seq": 0,
        "run_id": run,
        "mode": "simulation",
        "observed_at": "2026-11-03T00:00:00Z",
        "clock_domain": "simulation",
        "sim_time_s": 1,
        "samples": [
            {
                "signal_id": "location.latitude",
                "unit": "deg",
                "value": 45.51,
                "quality": "good",
                "provenance": "simulated-observation",
            }
        ],
    }
    topic = observation_topic(run, "bus-001", "sumo-adapter")
    cases = []

    def add(name, value, reason=None, *, catalog=None, topic_override=None):
        cases.append(
            {
                "id": name,
                "body": value,
                "topic": topic_override or topic,
                "run": run,
                "catalog": catalog,
                "expected": {"accepted": reason is None, "reason": reason},
            }
        )

    add("valid-motion", body)
    for key, value, reason in [
        ("mode", "replay", "envelope_schema"),
        ("mode", "qualification", "envelope_schema"),
        ("source_epoch", True, "envelope_schema"),
        ("source_epoch", 0, "envelope_schema"),
        ("source_epoch", 1.5, "envelope_schema"),
        ("catalog_version", "0.1.0", "envelope_schema"),
        ("schema_version", "1.1.0", "envelope_schema"),
        ("source_id", "unknown", "topic_identity"),
        ("run_id", str(uuid.UUID(int=999)), "run_mismatch"),
    ]:
        changed = copy.deepcopy(body)
        changed[key] = value
        add(key + str(value), changed, reason)
    changed = copy.deepcopy(body)
    del changed["source_epoch"]
    add("epoch-required", changed, "envelope_schema")
    add(
        "missing-source-topic",
        body,
        "topic_identity",
        topic_override=f"evdt/simulation/{run}/bus-001/observations",
    )
    add(
        "wrong-namespace",
        body,
        "topic_identity",
        topic_override=topic.replace("/simulation/", "/replay/"),
    )
    for field, value, reason in [
        ("value", True, "envelope_schema"),
        ("unit", "rad", "wrong_unit"),
        ("value", 91, "out_of_range"),
        ("signal_id", "coolant.inlet.temperature", "unregistered_signal"),
    ]:
        changed = copy.deepcopy(body)
        changed["samples"][0][field] = value
        add(field + str(value), changed, reason)
    changed = copy.deepcopy(body)
    changed["samples"].append(copy.deepcopy(changed["samples"][0]))
    add("duplicate-signal", changed, "duplicate_signal")
    catalog = copy.deepcopy(monitoring_contract().readers["1.0.0"].catalog)
    catalog["signals"]["coolant.inlet.temperature"] = {"unit": "K", "minimum": 200, "maximum": 500}
    changed = copy.deepcopy(body)
    changed["samples"][0].update(signal_id="coolant.inlet.temperature", unit="K", value=300)
    add("catalog-only-extension", changed, catalog=catalog)
    current = copy.deepcopy(body)
    current.update(
        schema_version="2.1.0", catalog_version="1.1.0", annotations={"manifest_sha256": "a" * 64}
    )
    add("current-catalog", current)
    current["samples"] = [
        {
            "signal_id": key,
            "value": (spec["minimum"] + spec["maximum"]) / 2,
            "unit": spec["unit"],
            "quality": "good",
            "provenance": "simulated-observation",
        }
        for key, spec in monitoring_contract().catalog["signals"].items()
    ]
    add("current-all-20", copy.deepcopy(current))
    v3 = copy.deepcopy(body)
    v3.update(schema_version="3.0.0", catalog_version="1.2.0", manifest_sha256="a" * 64)
    add("registered-good", copy.deepcopy(v3))
    bad_range = copy.deepcopy(v3)
    bad_range["samples"][0]["value"] = 91
    add("registered-good-out-of-range", bad_range, "out_of_range")
    bad_signal = copy.deepcopy(v3)
    bad_signal["samples"][0]["signal_id"] = "unregistered.sensor"
    add("registered-unregistered-signal", bad_signal, "unregistered_signal")
    bad_run = copy.deepcopy(v3)
    bad_run["run_id"] = str(uuid.UUID(int=999))
    add("registered-cross-run", bad_run, "run_mismatch")
    for name, sample, reason in [
        (
            "invalid",
            {"quality": "invalid", "value": None, "raw_value": 120, "reason": "out_of_range"},
            None,
        ),
        ("nonfinite", {"quality": "invalid", "value": None, "reason": "nonfinite"}, None),
        ("missing", {"quality": "missing", "value": None, "reason": "dropout"}, None),
        ("good-null", {"quality": "good", "value": None}, "envelope_schema"),
        (
            "invalid-number",
            {"quality": "invalid", "value": 45, "reason": "sensor_fault"},
            "envelope_schema",
        ),
        ("missing-no-reason", {"quality": "missing", "value": None}, "envelope_schema"),
        (
            "missing-raw",
            {"quality": "missing", "value": None, "reason": "dropout", "raw_value": 3},
            "envelope_schema",
        ),
        (
            "invalid-unit",
            {"quality": "invalid", "value": None, "reason": "sensor_fault", "unit": "rad"},
            "wrong_unit",
        ),
        ("good-with-reason", {"value": 45, "reason": "sensor_fault"}, "envelope_schema"),
        ("unsupported-quality", {"quality": "estimated", "value": 45}, "envelope_schema"),
    ]:
        changed = copy.deepcopy(v3)
        changed["samples"][0].update(sample)
        add("registered-" + name, changed, reason)
    missing_manifest = copy.deepcopy(v3)
    del missing_manifest["manifest_sha256"]
    add("required-manifest", missing_manifest, "envelope_schema")
    bad_manifest = copy.deepcopy(v3)
    bad_manifest["manifest_sha256"] = "a" * 63
    add("malformed-manifest", bad_manifest, "envelope_schema")
    for schema, cat in [("2.0.0", "1.1.0"), ("2.1.0", "1.0.0"), ("2.1.0", "9.0.0")]:
        changed = copy.deepcopy(body)
        changed.update(schema_version=schema, catalog_version=cat)
        add("schema-catalog-pair-" + schema + "-" + cat, changed, "envelope_schema")
    for index in range(20):
        changed = copy.deepcopy(body)
        changed["samples"][0]["value"] = -89 + index * 8
        add(
            "latitude-" + str(index),
            changed,
            None if changed["samples"][0]["value"] <= 90 else "out_of_range",
        )
    extension_catalog = monitoring_contract().readers["1.3.0"].catalog
    extension_topic = observation_topic(run, "bus-002", "sumo-adapter")
    extension = copy.deepcopy(v3)
    extension.update(asset_id="bus-002", schema_version="3.1.0", catalog_version="1.3.0")
    extension["samples"][0].update(signal_id="coolant.inlet.temperature", unit="K", value=300)
    add(
        "extension-registered-good",
        extension,
        catalog=extension_catalog,
        topic_override=extension_topic,
    )
    for key, value, reason in [
        ("value", 450, "out_of_range"),
        ("unit", "degC", "wrong_unit"),
        ("signal_id", "unknown.signal", "unregistered_signal"),
    ]:
        changed = copy.deepcopy(extension)
        changed["samples"][0][key] = value
        add(
            "extension-" + key,
            changed,
            reason,
            catalog=extension_catalog,
            topic_override=extension_topic,
        )
    for quality in ("missing", "invalid"):
        changed = copy.deepcopy(extension)
        changed["samples"][0].update(
            value=None,
            quality=quality,
            reason="dropout" if quality == "missing" else "sensor_fault",
        )
        if quality == "invalid":
            changed["samples"][0]["raw_value"] = 300
        add(
            "extension-" + quality,
            changed,
            catalog=extension_catalog,
            topic_override=extension_topic,
        )
    changed = copy.deepcopy(extension)
    changed["schema_version"] = "3.0.0"
    add(
        "extension-wrong-pair",
        changed,
        "envelope_schema",
        catalog=extension_catalog,
        topic_override=extension_topic,
    )
    changed = copy.deepcopy(extension)
    changed["samples"].append(copy.deepcopy(changed["samples"][0]))
    add(
        "extension-duplicate",
        changed,
        "duplicate_signal",
        catalog=extension_catalog,
        topic_override=extension_topic,
    )
    fleet_catalog = json.loads((ROOT / "catalog/monitoring-fleet-1.4.0.json").read_bytes())
    fleet = copy.deepcopy(body)
    fleet.update(schema_version="3.2.0", catalog_version="1.4.0", manifest_sha256="0" * 64)
    for asset in fleet_catalog["assets"]:
        changed = copy.deepcopy(fleet)
        changed["asset_id"] = asset
        add(
            "fleet-" + asset,
            changed,
            catalog=fleet_catalog,
            topic_override=observation_topic(run, asset, "sumo-adapter"),
        )
    changed = copy.deepcopy(fleet)
    changed["schema_version"] = "3.0.0"
    add("fleet-wrong-pair", changed, "envelope_schema", catalog=fleet_catalog)
    changed = copy.deepcopy(fleet)
    changed["asset_id"] = "bus-011"
    add(
        "fleet-unregistered-asset",
        changed,
        "unregistered_source",
        catalog=fleet_catalog,
        topic_override=observation_topic(run, "bus-011", "sumo-adapter"),
    )
    changed = copy.deepcopy(fleet)
    changed["samples"][0]["unit"] = "rad"
    add("fleet-wrong-unit", changed, "wrong_unit", catalog=fleet_catalog)
    with tempfile.TemporaryDirectory(prefix="evdt-monitoring-contract-") as folder:
        path = Path(folder)
        (path / "cases.json").write_text(json.dumps(cases))
        subprocess.run(
            [
                "node",
                str(ROOT / "qualification/monitoring/run_typescript.cjs"),
                str(path / "cases.json"),
                str(path / "ts.json"),
            ],
            check=True,
        )
        ts = json.loads((path / "ts.json").read_text())
        assert len(ts) == len(cases)
        for case, actual in zip(cases, ts, strict=True):
            py = {"accepted": True, "reason": None}
            try:
                monitoring_contract(case["catalog"]).validate(
                    json.dumps(case["body"]).encode(), case["topic"], case["run"]
                )
            except Rejected as exc:
                py = {"accepted": False, "reason": str(exc)}
            if py != case["expected"] or actual != case["expected"]:
                raise RuntimeError(f"Operational contract mismatch: {case['id']}")
    print("MONITORING_PARITY=" + json.dumps({"passed": True, "cases": len(cases)}))


if __name__ == "__main__":
    main()
