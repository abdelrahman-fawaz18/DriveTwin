"""Shared reader authentication, using each process's own mounted credential file."""

import hmac
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import Header, HTTPException


def dashboard_operator_asset(authorization, scope_variable):
    asset = os.environ.get(scope_variable)
    key_path = os.environ.get("EVDT_DASHBOARD_OPERATOR_FILE")
    if asset and key_path and authorization and len(authorization) <= 512:
        key = Path(key_path).read_text().strip()
        if key and hmac.compare_digest(authorization.encode(), ("Bearer " + key).encode()):
            return asset
    return None


def authenticate(authorization, *, energy_only=False):
    readers = json.loads(Path(os.environ["READERS_FILE"]).read_text())["readers"]
    supplied = (authorization or "").encode("utf-8")
    for entry in readers:
        purpose = entry.get("purpose", "monitoring")
        if purpose not in ("monitoring", "energy_input") or (
            purpose == "energy_input" and not energy_only
        ):
            continue
        if hmac.compare_digest(supplied, ("Bearer " + entry["token"]).encode("utf-8")):
            return entry["assets"]
    if not energy_only:
        for scope in ("EVDT_OPERATOR_ASSET", "EVDT_MOVING_CHARGE_OPERATOR_ASSET"):
            if asset := dashboard_operator_asset(authorization, scope):
                return [asset]
    raise HTTPException(401, "Reader credential required", headers={"WWW-Authenticate": "Bearer"})


def reader(authorization: Annotated[str | None, Header()] = None):
    return authenticate(authorization)


def energy_reader(authorization: Annotated[str | None, Header()] = None):
    return authenticate(authorization, energy_only=True)
