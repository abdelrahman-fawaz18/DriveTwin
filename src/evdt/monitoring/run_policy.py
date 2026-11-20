"""Pure source ownership, immutable recipe validation and freshness decisions."""

import hashlib
import math
import re
import uuid
from datetime import datetime
from typing import Any, cast

from evdt.contract_profile import PROFILE as PORTABLE_PROFILE
from evdt.contract_profile import Rejected, decode, validator
from evdt.monitoring.formats import checked_manifest, supported_pair

RUN_POLICY_MAX_BYTES = 1024 * 1024


def encoded(value: Any) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def identity_key(obj: dict[str, Any]) -> tuple[str, str, str]:
    return str(uuid.UUID(obj["run_id"])), obj["asset_id"], obj["source_id"]


def checked(raw: bytes, schema: str) -> dict[str, Any]:
    obj = decode(raw)
    if not validator(schema).is_valid(obj):
        raise Rejected("metadata_schema")
    return cast(dict[str, Any], obj)


def validate_policy(raw: bytes) -> dict[str, Any]:
    # Trusted startup configuration is not a telemetry/registration wire message.
    obj = decode(raw, {**PORTABLE_PROFILE, "max_payload_bytes": RUN_POLICY_MAX_BYTES})
    if (
        not isinstance(obj, dict)
        or obj.get("policy_version") != "run-policy/1.0.0"
        or obj.get("mode") != "simulation"
    ):
        raise ValueError("Unsupported run policy")
    uuid.UUID(obj["run_id"])
    owners: set[tuple[str, str]] = set()
    identities: set[tuple[str, str]] = set()
    if not isinstance(obj.get("sources"), list) or not obj["sources"]:
        raise ValueError("Run needs source permissions")
    for source in obj["sources"]:
        if set(source) != {"asset_id", "source_id", "expected"} or any(
            not isinstance(source[k], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", source[k])
            for k in ("asset_id", "source_id")
        ):
            raise ValueError("Invalid source permission")
        key = source["asset_id"], source["source_id"]
        if key in identities:
            raise ValueError("Duplicate source identity")
        identities.add(key)
        expected = source["expected"]
        if set(expected) != {
            "schema_version",
            "catalog_version",
            "model_version",
            "input_sha256",
            "step_s",
            "signals",
            "heartbeat_deadline_s",
            "paced",
            "initial_energy_j",
            "capacity_j",
            "packages",
            "traci_protocol",
            "sumo_version",
        }:
            raise ValueError("Policy expected fields must be explicit")
        if (
            not isinstance(expected["paced"], bool)
            or not expected["signals"]
            or not expected["input_sha256"]
        ):
            raise ValueError("Missing bounded source configuration")
        if any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
            for v in expected["input_sha256"].values()
        ):
            raise ValueError("Invalid input fingerprint")
        for name in ("heartbeat_deadline_s", "initial_energy_j", "capacity_j", "step_s"):
            value = expected[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Invalid policy number")
        if expected["initial_energy_j"] > expected["capacity_j"]:
            raise ValueError("Initial energy exceeds capacity")
        if not supported_pair(expected["schema_version"], expected["catalog_version"]):
            raise ValueError("Unsupported operational policy")
        for name, spec in expected["signals"].items():
            owner = source["asset_id"], name
            if owner in owners:
                raise ValueError("Competing signal owners")
            owners.add(owner)
            period, freshness, step = spec["period_s"], spec["freshness_s"], expected["step_s"]
            if any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or v <= 0
                for v in (period, freshness, step)
            ):
                raise ValueError("Invalid timing policy")
            if freshness < period or not math.isclose(
                period / step, round(period / step), abs_tol=1e-9, rel_tol=0
            ):
                raise ValueError("Incoherent cadence policy")
    return obj


def source_spec(policy: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    if str(uuid.UUID(obj["run_id"])) != str(uuid.UUID(policy["run_id"])):
        raise Rejected("run_mismatch")
    for source in policy["sources"]:
        if (source["asset_id"], source["source_id"]) == (obj["asset_id"], obj["source_id"]):
            return cast(dict[str, Any], source)
    raise Rejected("source_not_authorized")


def validate_manifest(raw: bytes, policy: dict[str, Any]) -> dict[str, Any]:
    manifest = checked_manifest(raw)
    obj = manifest["identity"]
    expected = source_spec(policy, obj)["expected"]
    if manifest["asset_id"] != obj["asset_id"]:
        raise Rejected("manifest_identity")
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise Rejected("manifest_policy_mismatch")
    if manifest["source_epoch_start_sim_s"] != manifest["minimum_sim_time_s"]:
        raise Rejected("manifest_time_floor")
    sampling = manifest["sampling"]
    if set(sampling["period_overrides_s"]) - set(manifest["signals"]):
        raise Rejected("manifest_sampling_mismatch")
    for name, spec in manifest["signals"].items():
        period = sampling["period_overrides_s"].get(name, sampling["default_period_s"])
        if not math.isclose(period, spec["period_s"], abs_tol=1e-9, rel_tol=0):
            raise Rejected("manifest_sampling_mismatch")
    return cast(dict[str, Any], manifest)


def presentation(
    sample: dict[str, Any] | None,
    sample_sim_s: float | None,
    observed_at: datetime | None,
    clock: dict[str, Any] | None,
    timing: dict[str, float],
    now: datetime,
    heartbeat_s: float,
) -> dict[str, Any]:
    """Quality does not turn into freshness, and stale samples do not become fresh on resume."""
    live = clock is not None and (now - clock["observed_at"]).total_seconds() <= heartbeat_s
    source_state = "unknown" if clock is None else clock["state"]
    if clock and clock["state"] != "finished" and not live:
        source_state = "disconnected"
    age = max(0.0, (now - observed_at).total_seconds()) if observed_at is not None else None
    sim_age = (
        max(0.0, clock["sim_time_s"] - sample_sim_s) if clock and sample_sim_s is not None else None
    )
    quality = sample["quality"] if sample else "unknown"
    freshness = "unknown"
    if sample:
        sim_ok = sim_age is not None and sim_age <= timing["freshness_s"]
        pause_at = clock.get("pause_started_at") if clock else None
        eligible_at_pause = (
            observed_at is not None
            and pause_at is not None
            and (pause_at - observed_at).total_seconds() <= timing["freshness_s"]
        )
        if source_state == "paused" and live and sim_ok and eligible_at_pause:
            freshness = "paused"
        elif (
            source_state in ("running", "finished")
            and sim_ok
            and age is not None
            and age <= timing["freshness_s"]
            and (live or source_state == "finished")
        ):
            freshness = "fresh"
        else:
            freshness = "stale"
    status = quality if quality in ("invalid", "missing") else freshness
    return {
        "quality": quality,
        "freshness": freshness,
        "source_state": source_state,
        "age_s": age,
        "simulation_age_s": sim_age,
        "status": status,
    }
