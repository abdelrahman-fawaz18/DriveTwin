"""Select original, input-bound signal definitions from an asset's trusted policy."""

import copy
import hashlib
import json
from functools import lru_cache

from fastapi import HTTPException

from evdt.config import ROOT
from evdt.contract_profile import Contract


def schema_dependencies(profile, version):
    names = set(profile["schemas"].values()) | {
        profile["portable_schema"],
        profile.get("catalog_schemas", {}).get(version, profile["catalog_schema"]),
    }
    return tuple(
        (name, hashlib.sha256((ROOT / "contracts" / name).read_bytes()).hexdigest())
        for name in sorted(names)
    )


@lru_cache(maxsize=8)
def validated_catalog(profile_raw, catalog_raw, version, dependencies):
    profile, catalog = json.loads(profile_raw), json.loads(catalog_raw)
    if catalog.get("catalog_version") != version:
        raise HTTPException(409, "Registered catalog version changed")
    try:
        checked = Contract(catalog, profile=profile).catalog
    except ValueError as exc:
        raise HTTPException(409, "Registered catalog artifact invalid") from exc
    if schema_dependencies(profile, version) != dependencies:
        raise HTTPException(409, "Catalog validation dependencies changed")
    return checked


def for_policy(policy, asset):
    owners = [s for s in policy["sources"] if s["asset_id"] == asset]
    versions = {s["expected"]["catalog_version"] for s in owners}
    if not owners:
        raise HTTPException(404, "Asset is not registered in this run")
    if len(versions) != 1:
        raise HTTPException(409, "An asset requires one explicit catalog version")
    version = next(iter(versions))
    profile_raw = (ROOT / "contracts/monitoring.profile.json").read_bytes()
    profile = json.loads(profile_raw)
    if version not in profile["catalogs"]:
        raise HTTPException(409, "Registered catalog artifact unavailable")
    raw = (ROOT / profile["catalogs"][version]).read_bytes()
    if any(
        s["expected"]["input_sha256"]["catalog"] != hashlib.sha256(raw).hexdigest() for s in owners
    ):
        raise HTTPException(409, "Registered catalog fingerprint changed")
    catalog = copy.deepcopy(
        validated_catalog(profile_raw, raw, version, schema_dependencies(profile, version))
    )
    owned = {name for s in owners for name in s["expected"]["signals"]}
    return {
        **catalog,
        "assets": {asset: catalog["assets"][asset]},
        "signals": {k: v for k, v in catalog["signals"].items() if k in owned},
    }


def for_asset(conn, run, asset):
    row = conn.execute("SELECT policy FROM m_run_definitions WHERE run_id=%s", (run,)).fetchone()
    if not row:
        raise HTTPException(404, "Run definition unavailable")
    return for_policy(row["policy"], asset)
