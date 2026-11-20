"""Credential-free index of local journey recordings and their outcomes."""

import json
import math
import os
import re
import time
from datetime import datetime, timezone

from recover_run import BACKUP, recovered_session, valid_result

from catalog import OPERATOR_MODE, starting_charge_percent, time_scale
from evdt.monitoring.charging_startup import charging_startup_delay

IDENTITY = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
CHARGE_FIELDS = ("starting_charge_percent", "initial_charge_percent")
PROFILE_SETTINGS = (*CHARGE_FIELDS, "charging_startup_delay_working_s")
OPTIONAL_SETTINGS = (*PROFILE_SETTINGS, "charging_operator")


def validate_charge_metadata(value):
    services = value.get("services")
    if charging_startup_delay(value.get("charging_startup_delay_working_s", 0)) and (
        not isinstance(services, list) or "charging" not in services
    ):
        raise ValueError("A saved advisor delay requires Charging Advisor")
    if type(value.get("charging_operator", False)) is not bool:
        raise ValueError("Invalid charging operator mode")
    if "starting_charge_percent" in value:
        starting_charge_percent(value["starting_charge_percent"])
    if "initial_charge_percent" in value:
        initial = value["initial_charge_percent"]
        if type(initial) not in (int, float) or not 0 <= initial <= 100:
            raise ValueError("Invalid effective starting charge")


def recording_saved(folder, session, recovery=None):
    for metadata, name in ((session, "database.dump"), (recovery or {}, BACKUP)):
        if metadata.get("backup_saved") is True:
            try:
                backup = folder / name
                if backup.is_file() and backup.stat().st_size > 0:
                    return True
            except OSError:
                pass
    return False


def valid_entry(value):
    if not isinstance(value, dict):
        return False
    try:
        time_scale(value.get("time_scale", 1))
        validate_charge_metadata(value)
        return (
            all(isinstance(value.get(k), str) for k in ("id", "trip_id", "title"))
            and bool(IDENTITY.fullmatch(value["id"]))
            and isinstance(value.get("services"), list)
            and all(
                service in ("monitoring", "energy", "replay", "charging")
                for service in value["services"]
            )
            and type(value.get("created")) in (int, float)
            and math.isfinite(value["created"])
        )
    except (ValueError, TypeError, OverflowError):
        return False


class History:
    def __init__(self, root, opener=None):
        self.root = root.resolve()
        self.directory = self.root / ".runtime/launch-app/sessions"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.opener = opener or (os.startfile if os.name == "nt" else None)
        self.scanned = False

    def folder(self, identity):
        if not isinstance(identity, str) or not IDENTITY.fullmatch(identity):
            raise ValueError("Select a saved session")
        parent = self.root / "evidence/energy-v2"
        path = (parent / ("live-journey-" + identity)).resolve()
        if path.parent != parent or not path.is_dir():
            raise ValueError("This session's result folder is unavailable")
        return path

    def read(self, path, default=None, limit=8_388_608):
        try:
            if not path.resolve().is_relative_to(self.root) or path.stat().st_size > limit:
                return default
            value = json.loads(path.read_bytes())
            return value if isinstance(value, dict) else default
        except (OSError, ValueError, RecursionError):
            return default

    def remember(self, run):
        if not valid_entry(run):
            raise ValueError("Invalid saved session metadata")
        # Credentials, PIDs, arbitrary caller paths, and error traces are never indexed.
        record = {key: run[key] for key in ("id", "trip_id", "title", "services", "created")}
        record["time_scale"] = run.get("time_scale", 1)
        record.update({name: run[name] for name in OPTIONAL_SETTINGS if name in run})
        path = self.directory / (run["id"] + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def discover(self):
        for folder in (self.root / "evidence/energy-v2").glob("live-journey-*"):
            identity = folder.name.removeprefix("live-journey-")
            if not IDENTITY.fullmatch(identity) or (self.directory / (identity + ".json")).exists():
                continue
            try:
                folder = self.folder(identity)
                created = (
                    datetime.strptime(identity[:16], "%Y%m%dT%H%M%SZ")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
            except ValueError:
                continue
            profile = self.read(folder / "profile.json", {}, limit=1_048_576)
            services = profile.get("services", ["energy", "replay"])
            if (
                not all(isinstance(profile.get(key), str) for key in ("journey_id", "title"))
                or not isinstance(services, list)
                or any(service not in ("energy", "replay", "charging") for service in services)
            ):
                continue
            try:
                time_scale(profile.get("time_scale", 1))
                validate_charge_metadata(profile)
            except ValueError:
                continue
            self.remember(
                {
                    "id": identity,
                    "trip_id": profile["journey_id"],
                    "title": profile["title"],
                    "services": ["monitoring", *services],
                    "created": created,
                    "time_scale": profile.get("time_scale", 1),
                    "charging_operator": profile.get("moving_charge_mode") == OPERATOR_MODE,
                    **{name: profile[name] for name in PROFILE_SETTINGS if name in profile},
                }
            )
        self.scanned = True

    def summary(self, entry, current):
        record = {key: entry[key] for key in ("id", "trip_id", "title", "services", "created")}
        record["time_scale"] = entry.get("time_scale", 1)
        record.update({name: entry[name] for name in OPTIONAL_SETTINGS if name in entry})
        active = bool(current and current["id"] == entry["id"] and current["active"])
        try:
            folder = self.folder(entry["id"])
        except ValueError:
            return {
                **record,
                "state": current["state"] if active else "results_unavailable",
                "folder_available": False,
                "recording_saved": False,
                "active": active,
            }
        result_path = folder / "operator-result.json"
        raw_result = self.read(result_path)
        malformed_result = result_path.exists() and not valid_result(raw_result)
        result = raw_result if valid_result(raw_result) else {}
        session = result.get("session", {})
        if not isinstance(session, dict):
            session = {}
        recovery = recovered_session(folder) if not active else {}
        cleanup = recovery.get("cleanup_verified", session.get("cleanup_verified"))
        state = current["state"] if active else result.get("status", "incomplete")
        if not active and malformed_result and not recovery:
            state = "cleanup_unverified"
            cleanup = False
        elif not active and (session or result.get("worker_started")) and not cleanup:
            state = "cleanup_unverified"
        elif recovery:
            state = "failed_or_incomplete"
        if state != "cleanup_unverified" and (
            session.get("finalization_errors")
            or result.get("sleep_release_error")
            or (result.get("runtime_host_guard") or {}).get("failure")
        ):
            state = "failed_or_incomplete"
        saved = recording_saved(folder, session, recovery)
        if not active and state == "arrived" and not saved:
            state = "failed_or_incomplete"
        return {
            **record,
            "state": state,
            "active": active,
            "folder_available": True,
            "recording_saved": saved,
            "cleanup_verified": cleanup,
            "output": folder.relative_to(self.root).as_posix(),
            "absolute_path": str(folder),
            "finished": None
            if active or not result
            else (folder / "operator-result.json").stat().st_mtime,
        }

    def page(self, *, offset=0, search="", outcome="all", current=None, refresh=False):
        if type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError("Invalid history page")
        if (
            not isinstance(search, str)
            or len(search) > 100
            or outcome not in ("all", "saved", "attention")
        ):
            raise ValueError("Invalid history filter")
        if refresh or not self.scanned:
            self.discover()
        entries = []
        for path in self.directory.glob("*.json"):
            value = self.read(path, {}, limit=8192)
            if not valid_entry(value):
                continue
            if (
                not IDENTITY.fullmatch(str(value["id"]))
                or search.casefold() not in str(value["title"]).casefold()
            ):
                continue
            entries.append(value)
        entries.sort(key=lambda item: item["created"], reverse=True)
        # The unfiltered view needs outcomes for the visible page only. Keep
        # active/recovery/backup checks fresh; do not cache mutable run results.
        if outcome == "all":
            return {
                "items": [self.summary(value, current) for value in entries[offset : offset + 20]],
                "offset": offset,
                "total": len(entries),
                "updated": time.time(),
            }
        filtered = []
        for value in entries:
            item = self.summary(value, current)
            if outcome == "saved" and not item["recording_saved"]:
                continue
            if outcome == "attention" and (
                item["active"]
                or (
                    item["recording_saved"]
                    and item["state"] in ("arrived", "stopped_incomplete", "preview_complete")
                )
            ):
                continue
            filtered.append(item)
        return {
            "items": filtered[offset : offset + 20],
            "offset": offset,
            "total": len(filtered),
            "updated": time.time(),
        }

    def open_folder(self, identity):
        entry = (
            self.read(self.directory / (str(identity) + ".json"), {}, limit=8192)
            if isinstance(identity, str) and IDENTITY.fullmatch(identity)
            else {}
        )
        if entry.get("id") != identity:
            raise ValueError("Select an indexed session")
        folder = self.folder(identity)
        if self.opener is None:
            raise ValueError("Use the displayed folder path on this platform")
        self.opener(str(folder))
        return {"opened": True}
