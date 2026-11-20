"""Explicit monitoring-family selection and durable receipt encoding."""

import hashlib
import json

from evdt.config import ROOT
from evdt.contract_profile import Contract, Rejected, decode, validator


class MonitoringContract:
    """Explicit catalog dispatch preserves old bodies without permissive fallback."""

    def __init__(self, catalog=None):
        self.profile = json.loads((ROOT / "contracts/monitoring.profile.json").read_text())
        selected = (
            [catalog]
            if catalog is not None
            else [
                json.loads((ROOT / path).read_text()) for path in self.profile["catalogs"].values()
            ]
        )
        self.readers = {
            item["catalog_version"]: Contract(item, profile=self.profile) for item in selected
        }
        current = self.profile["current_catalog_version"]
        self.catalog = self.readers[
            current if current in self.readers else selected[0]["catalog_version"]
        ].catalog

    def validate(self, raw, topic, expected_run):
        obj = decode(raw, self.profile)
        version = obj.get("catalog_version") if isinstance(obj, dict) else None
        default = self.profile["current_catalog_version"]
        fallback = self.readers.get(default, next(iter(self.readers.values())))
        reader = self.readers.get(version, fallback) if isinstance(version, str) else fallback
        return reader.validate(raw, topic, expected_run)


def monitoring_contract(catalog=None):
    return MonitoringContract(catalog)


def observation_topic(run, asset, source):
    return f"evdt/simulation/{run}/{asset}/{source}/observations"


def receipt_topic(run, asset, source):
    return f"evdt/simulation/{run}/{asset}/{source}/receipts"


def read_receipt(raw: bytes) -> dict:
    obj = decode(raw)
    if not validator("receipt.schema.json").is_valid(obj):
        raise Rejected("receipt_schema")
    return obj


def receipt(obj, raw, status, reason=None):
    body = {key: obj[key] for key in ("run_id", "asset_id", "source_id", "event_id")}
    body.update(
        schema_version="receipt/1.0.0",
        wire_sha256=hashlib.sha256(raw).hexdigest(),
        status=status,
        reason=reason,
    )
    encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    read_receipt(encoded)
    return encoded


def receipt_identity(raw, topic, run, catalog):
    """Bind a rejection to a registered sender without trusting invalid sample data."""
    try:
        obj = decode(raw)
        if not isinstance(obj, dict) or obj.get("run_id") != run:
            return None
        asset = catalog["assets"].get(obj.get("asset_id"))
        if not asset or obj.get("source_id") not in asset["sources"]:
            return None
        if topic != observation_topic(run, obj["asset_id"], obj["source_id"]):
            return None
        receipt(obj, raw, "rejected", "validation_failed")
        return obj
    except (ValueError, TypeError, KeyError):
        return None
