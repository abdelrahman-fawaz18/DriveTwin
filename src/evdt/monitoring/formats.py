"""Explicit version registry; adding a supported format is reviewed metadata."""

import json
from typing import Any, cast

from evdt.config import ROOT
from evdt.contract_profile import Rejected, decode, validator


def profile() -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((ROOT / "contracts/monitoring.profile.json").read_text())
    )


def operational_format(catalog_version: str) -> dict[str, Any]:
    entry = profile()["operational_formats"].get(catalog_version)
    if entry is None:
        raise ValueError("Unsupported operational catalog")
    return cast(dict[str, Any], entry)


def supported_pair(schema_version: str, catalog_version: str) -> bool:
    entry = profile()["operational_formats"].get(catalog_version)
    return bool(entry is not None and entry["schema_version"] == schema_version)


def checked_manifest(raw: bytes) -> dict[str, Any]:
    obj = decode(raw)
    version = obj.get("manifest_version") if isinstance(obj, dict) else None
    schema = profile()["manifest_schemas"].get(version) if isinstance(version, str) else None
    if not schema or not validator(schema).is_valid(obj):
        raise Rejected("metadata_schema")
    return cast(dict[str, Any], obj)
