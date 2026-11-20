"""Deterministic test vectors with explicit acceptance/rejection oracles."""

import base64
import copy
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = "12345678-1234-4234-8234-123456789abc"
BASE = {
    "schema_version": "1.0.0",
    "catalog_version": "0.1.0",
    "event_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "asset_id": "bus-001",
    "source_id": "fixture-adapter",
    "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "stream_seq": 1,
    "run_id": RUN,
    "mode": "qualification",
    "observed_at": "2026-01-01T00:00:01Z",
    "clock_domain": "simulation",
    "sim_time_s": 1,
    "samples": [
        {
            "signal_id": "battery.pack.soc",
            "value": 0.71,
            "unit": "1",
            "quality": "good",
            "provenance": "synthetic-fixture",
        }
    ],
}


def build():
    cases = []

    def add(name, obj=None, reason=None, *, raw=None, **extra):
        payload = (
            json.dumps(
                BASE if obj is None else obj, ensure_ascii=True, separators=(",", ":")
            ).encode()
            if raw is None
            else raw
        )
        cases.append(
            {
                "id": name,
                "raw_base64": base64.b64encode(payload).decode(),
                "topic": f"evdt/qualification/{RUN}/bus-001/observations",
                "expected_run": RUN,
                "expected": {"accepted": reason is None, "reason": reason},
                **extra,
            }
        )

    def changed(field, value):
        obj = copy.deepcopy(BASE)
        obj[field] = value
        return obj

    def sample(field, value):
        obj = copy.deepcopy(BASE)
        obj["samples"][0][field] = value
        return obj

    add("valid-previous", legacy_expected=True, downgrade=True)
    current = changed("schema_version", "1.1.0")
    add("valid-current", current, legacy_expected=False, downgrade=True)
    current["annotations"] = {"note": "synthetic Montréal bus \U0001f68c"}
    add("valid-current-annotations", current, legacy_expected=False, downgrade=True)
    add("old-version-rejects-new-field", changed("annotations", {"note": "x"}), "envelope_schema")
    for version in ["0.9.0", "1.2.0", "2.0.0", "1.1", None, 1, True]:
        add("version-" + str(version), changed("schema_version", version), "envelope_schema")
    for field in BASE:
        obj = copy.deepcopy(BASE)
        del obj[field]
        add("missing-" + field, obj, "envelope_schema")
    for field, value in [
        ("stream_seq", True),
        ("stream_seq", "1"),
        ("stream_seq", -1),
        ("stream_seq", 1.5),
        ("sim_time_s", -1),
        ("sim_time_s", "1"),
        ("mode", "live"),
        ("mode", "replay"),
        ("clock_domain", "wall"),
        ("catalog_version", "9.0.0"),
        ("extra", "unknown"),
        ("asset_id", "bus-001\n"),
        ("source_id", "fixture-adapter\n"),
    ]:
        add("field-" + field + "-" + repr(value), changed(field, value), "envelope_schema")
    for field in ("event_id", "session_id", "run_id"):
        for value in ("invalid", "urn:uuid:" + BASE[field], BASE[field] + "\n"):
            add(field + "-" + repr(value), changed(field, value), "envelope_schema")
    for timestamp in [
        "2026-01-01T00:00:01+00:00",
        "2026-01-01T00:00:01",
        "2026-01-01t00:00:01Z",
        "2026-01-01T00:00:01z",
        "2026-02-29T00:00:00Z",
        "2024-02-30T00:00:00Z",
        "0000-01-01T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-01-01T24:00:00Z",
        "2026-01-01T23:59:60Z",
        "2026-01-01T00:00:01.1234567Z",
        "2026-01-01T00:00:01Z\n",
    ]:
        add("timestamp-" + repr(timestamp), changed("observed_at", timestamp), "envelope_schema")
    for timestamp in [
        "2024-02-29T00:00:00Z",
        "2026-01-01T00:00:01.1Z",
        "2026-01-01T00:00:01.123456Z",
    ]:
        add("valid-time-" + timestamp, changed("observed_at", timestamp))
    for field, values, reason in [
        ("value", [True, False, None, "0.71", [], {}], "envelope_schema"),
        ("value", [-0.001, 1.001], "out_of_range"),
        ("unit", ["%", "K", "", 1], "wrong_unit"),
        ("quality", ["invalid", "unknown"], "envelope_schema"),
        ("provenance", ["experiment-truth", "unknown"], "envelope_schema"),
        (
            "signal_id",
            ["new.sensor", "toString", "constructor", "__proto__"],
            "unregistered_signal",
        ),
    ]:
        for value in values:
            why = "envelope_schema" if field == "unit" and value in ("", 1) else reason
            add("sample-" + field + "-" + repr(value), sample(field, value), why)
    for value in [0, 1, 0.5, -0.0]:
        add("soc-boundary-" + repr(value), sample("value", value))
    for value in [0, 9007199254740991]:
        add("sequence-boundary-" + str(value), changed("stream_seq", value))
    for value in [9007199254740992, 9007199254740993, 9223372036854775807]:
        add("unsafe-integer-" + str(value), changed("stream_seq", value), "portable_json")
    add(
        "numeric-overflow",
        raw=json.dumps(BASE).replace('"sim_time_s": 1', '"sim_time_s": 1e400').encode(),
        reason="portable_json",
    )
    add(
        "duplicate-signal",
        changed("samples", [BASE["samples"][0], BASE["samples"][0]]),
        "duplicate_signal",
    )
    add("empty-samples", changed("samples", []), "envelope_schema")
    add("too-many-samples", changed("samples", [BASE["samples"][0]] * 65), "envelope_schema")
    add(
        "wrong-topic",
        topic=f"evdt/qualification/{RUN}/bus-999/observations",
        reason="topic_identity",
    )
    add("wrong-run", expected_run="ffffffff-ffff-4fff-8fff-ffffffffffff", reason="run_mismatch")
    add("unknown-source", changed("source_id", "unknown"), "unregistered_source")
    obj = changed("asset_id", "bus-999")
    add(
        "unknown-asset",
        obj,
        "unregistered_source",
        topic=f"evdt/qualification/{RUN}/bus-999/observations",
    )
    for raw in [
        b"",
        b"{",
        b"null true",
        b'{"x":1,}',
        b'{/*comment*/"x":1}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1,"x":2}',
        b'{"a":{"x":1,"x":2}}',
        b'{"x":1,"\\u0078":2}',
        b'"\\ud800"',
        b'"\\udfff"',
        b'"\x01"',
        b"\xff",
        b"\xc3(",
    ]:
        add("invalid-json-" + raw.hex(), raw=raw, reason="invalid_json")
    for value in [None, True, False, 1, "text", []]:
        add("wrong-root-" + repr(value), value, "envelope_schema", raw=json.dumps(value).encode())
    valid = json.dumps(BASE, separators=(",", ":")).encode()
    add("utf8-bom", raw=b"\xef\xbb\xbf" + valid, reason="invalid_json")
    add("utf16", raw=valid.decode().encode("utf-16"), reason="invalid_json")
    add("payload-exact-limit", raw=valid + b" " * (65536 - len(valid)))
    add("payload-over-limit", raw=valid + b" " * (65537 - len(valid)), reason="payload_too_large")
    add("nesting-at-limit", raw=b"[" * 32 + b"0" + b"]" * 32, reason="envelope_schema")
    add("nesting-over-limit", raw=b"[" * 33 + b"0" + b"]" * 33, reason="invalid_json")
    add("deep-malformed", raw=b"[" * 1500, reason="invalid_json")
    for annotations in [
        {"x": 1},
        {"bad key": "x"},
        {"note": "x" * 257},
        {f"key{i}": "x" for i in range(9)},
    ]:
        obj = copy.deepcopy(current)
        obj["annotations"] = annotations
        add("bad-annotations-" + str(len(cases)), obj, "envelope_schema")
    obj = copy.deepcopy(current)
    obj["annotations"] = {"note": "\U0001f68c" * 256}
    add("unicode-codepoint-length", obj)
    base_catalog = json.loads((ROOT / "catalog/reference-signals.json").read_text())
    extension = copy.deepcopy(base_catalog)
    extension["signals"]["coolant.inlet.temperature"] = {
        "unit": "K",
        "minimum": 200,
        "maximum": 500,
        "data_type": "number",
        "physical_location": "coolant inlet",
    }
    coolant = sample("signal_id", "coolant.inlet.temperature")
    coolant["samples"][0].update(value=300, unit="K")
    add("extension-before-registration", coolant, "unregistered_signal")
    add("extension-after-registration", coolant, catalog=extension)
    add("catalog-snapshot", catalog=base_catalog, mutate_catalog_after=True)
    for mutation in [
        {},
        {**base_catalog, "catalog_version": "unknown"},
        {**base_catalog, "assets": {}},
    ]:
        add("bad-catalog-" + str(len(cases)), reason="configuration_error", catalog=mutation)
    bad = copy.deepcopy(base_catalog)
    bad["signals"]["battery.pack.soc"]["minimum"] = 2
    add("reversed-catalog-range", reason="configuration_error", catalog=bad)
    bad = copy.deepcopy(base_catalog)
    bad["signals"]["battery.pack.soc"]["minimum"] = False
    add("boolean-catalog-range", reason="configuration_error", catalog=bad)
    rng = random.Random(20261103)
    for index in range(100):
        add(f"seeded-valid-soc-{index}", sample("value", rng.random()))
        add(f"seeded-invalid-soc-{index}", sample("value", 1.01 + rng.random()), "out_of_range")
    return cases


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build(), indent=2) + "\n")
