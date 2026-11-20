"""Shared envelope plus catalog validation; no signal names embedded in ingestion."""

import hashlib
import json
import math

from jsonschema import Draft202012Validator, FormatChecker

from evdt.config import ROOT

MAX_PAYLOAD = 65_536


class Rejected(ValueError):
    pass


class Contract:
    def __init__(self, catalog=None):
        self.catalog = catalog or json.loads((ROOT / "catalog/p0-signals.json").read_text())
        schema = json.loads((ROOT / "contracts/telemetry/1.0.0.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def validate(self, raw: bytes, topic: str, expected_run: str):
        if len(raw) > MAX_PAYLOAD:
            raise Rejected("payload_too_large")
        try:

            def no_duplicates(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate JSON key")
                    result[key] = value
                return result

            obj = json.loads(
                raw,
                object_pairs_hook=no_duplicates,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise Rejected("invalid_json") from exc
        if not self.validator.is_valid(obj):
            raise Rejected("envelope_schema")
        if not math.isfinite(obj["sim_time_s"]):
            raise Rejected("out_of_range")
        if obj["catalog_version"] != self.catalog["catalog_version"]:
            raise Rejected("catalog_version")
        if obj["run_id"] != expected_run:
            raise Rejected("run_mismatch")
        expected_topic = f"evdt/qualification/{expected_run}/{obj['asset_id']}/observations"
        if topic != expected_topic:
            raise Rejected("topic_identity")
        asset = self.catalog["assets"].get(obj["asset_id"])
        if not asset or obj["source_id"] not in asset["sources"]:
            raise Rejected("unregistered_source")
        seen = set()
        for sample in obj["samples"]:
            key = sample["signal_id"]
            spec = self.catalog["signals"].get(key)
            if spec is None:
                raise Rejected("unregistered_signal")
            if key in seen:
                raise Rejected("duplicate_signal")
            seen.add(key)
            if sample["unit"] != spec["unit"]:
                raise Rejected("wrong_unit")
            value = sample["value"]
            if not math.isfinite(value) or not spec["minimum"] <= value <= spec["maximum"]:
                raise Rejected("out_of_range")
        canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return obj, hashlib.sha256(canonical.encode()).hexdigest()
