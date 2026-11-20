"""Versioned envelope/catalog checks under an explicit portable JSON profile."""

import copy
import hashlib
import json
import math

from jsonschema import Draft202012Validator, FormatChecker

from evdt.config import ROOT

PROFILE = json.loads((ROOT / "contracts/profile.json").read_text())
MAX_PAYLOAD = PROFILE["max_payload_bytes"]


class Rejected(ValueError):
    pass


def portable(value, depth=0, profile=PROFILE):
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise Rejected("invalid_json") from exc
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        safe = (
            abs(value) <= profile["max_safe_integer"]
            if isinstance(value, int)
            else (
                math.isfinite(value)
                and (not value.is_integer() or abs(value) <= profile["max_safe_integer"])
            )
        )
        if not safe:
            raise Rejected("portable_json")
    elif isinstance(value, (dict, list)):
        if depth + 1 > profile["max_depth"]:
            raise Rejected("invalid_json")
        if isinstance(value, dict):
            for key, item in value.items():
                portable(key, depth + 1, profile)
                portable(item, depth + 1, profile)
        else:
            for item in value:
                portable(item, depth + 1, profile)


def decode(raw, profile=PROFILE):
    if len(raw) > profile["max_payload_bytes"]:
        raise Rejected("payload_too_large")

    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        obj = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=no_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Rejected("invalid_json") from exc
    portable(obj, profile=profile)
    return obj


def validator(path):
    schema = json.loads((ROOT / "contracts" / path).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


class Contract:
    def __init__(self, catalog=None, *, profile=None):
        self.profile = copy.deepcopy(PROFILE if profile is None else profile)
        self.catalog = (
            json.loads((ROOT / "catalog/reference-signals.json").read_text())
            if catalog is None
            else copy.deepcopy(catalog)
        )
        try:
            portable(self.catalog, profile=self.profile)
            catalog_schema = self.profile.get("catalog_schemas", {}).get(
                self.catalog.get("catalog_version"), self.profile["catalog_schema"]
            )
            if not validator(catalog_schema).is_valid(self.catalog):
                raise ValueError("catalog schema")
            if any(spec["minimum"] > spec["maximum"] for spec in self.catalog["signals"].values()):
                raise ValueError("reversed bounds")
        except (Rejected, ValueError) as exc:
            raise ValueError("Invalid catalog configuration") from exc
        self.validators = {
            version: validator(path) for version, path in self.profile["schemas"].items()
        }
        self.portable_validator = validator(self.profile["portable_schema"])

    def validate(self, raw: bytes, topic: str, expected_run: str):
        obj = decode(raw, self.profile)
        version = obj.get("schema_version") if isinstance(obj, dict) else None
        if not isinstance(version, str) or version not in self.validators:
            raise Rejected("envelope_schema")
        if not self.validators[version].is_valid(obj) or not self.portable_validator.is_valid(obj):
            raise Rejected("envelope_schema")
        if obj["catalog_version"] != self.catalog["catalog_version"]:
            raise Rejected("catalog_version")
        if obj["run_id"] != expected_run:
            raise Rejected("run_mismatch")
        namespace = self.profile.get("topic_namespace", "qualification")
        source = f"/{obj['source_id']}" if self.profile.get("source_in_topic", False) else ""
        expected_topic = f"evdt/{namespace}/{expected_run}/{obj['asset_id']}{source}/observations"
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
            if sample["quality"] != "good" and self.profile.get("sample_quality_states"):
                continue
            if not spec["minimum"] <= sample["value"] <= spec["maximum"]:
                raise Rejected("out_of_range")
        canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return obj, hashlib.sha256(canonical.encode()).hexdigest()

    def for_legacy(self, raw: bytes, topic: str, expected_run: str):
        """Explicit pre-publication encoding, never an automatic history rewrite."""
        obj, _ = self.validate(raw, topic, expected_run)
        result = copy.deepcopy(obj)
        result.pop("annotations", None)
        result["schema_version"] = "1.0.0"
        encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
        self.validate(encoded, topic, expected_run)
        return encoded
