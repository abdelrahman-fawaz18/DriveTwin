"""Single owned run, explicit stop requests, and restart-safe status reporting."""

import hmac
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from history import History, recording_saved, valid_entry, valid_result
from recover_run import recovered_session

from catalog import OPERATOR_MODE, ROOT, public, select


def read(path, default=None):
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeError, RecursionError):
        return default


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_current(directory, root):
    """A missing bookmark is idle; an unreadable or invalid one needs review."""
    problem = (
        "DriveTwin cannot read the previous trip's saved status. Starting another trip "
        "is blocked until its cleanup is checked. See saved-session metadata repair "
        "in the launch guide."
    )
    path = directory / "current.json"
    found = False
    try:
        for entry in (path, *path.parents):
            if entry == root:
                break
            if entry.is_symlink() or entry.is_junction():
                return None, problem
        if path.stat().st_size > 8192:
            return None, problem
        found = True
        with path.open("rb") as stream:
            payload = stream.read(8193)
        if len(payload) > 8192:
            return None, problem
        value = json.loads(payload)
        if (
            not isinstance(value, dict)
            or not valid_entry(value)
            or type(value.get("pid")) is not int
            or not 0 < value["pid"] <= 0xFFFFFFFF
            or type(value.get("stop_requested")) is not bool
            or value.get("output") != "evidence/energy-v2/live-journey-" + value["id"]
            or value.get("log") != ".runtime/launch-app/" + value["id"] + ".log"
        ):
            return None, problem
        for name in (value["output"], value["log"]):
            target = root / name
            if target.resolve() != target:
                return None, problem
        return value, None
    except FileNotFoundError:
        return None, problem if found else None
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return None, problem


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of exit.
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def copy_reader_key(key):
    """Keep the reader credential out of command arguments and subprocess logs."""
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["clip.exe"],
            input=key,
            text=True,
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Sessions:
    def __init__(self, trips, *, root=ROOT, spawn=None, open_browser=None, copy_key=None):
        self.root = root
        self.directory = root / ".runtime/launch-app"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.trips = trips
        self.lock = threading.RLock()
        self.process = None
        self.recovery_process = None
        self.spawn = spawn or subprocess.Popen
        self.open_browser = open_browser or webbrowser.open
        self.copy_key = copy_key or copy_reader_key
        self.current, self.session_metadata_error = read_current(self.directory, root)
        self.history = History(root)
        self.closing = False

    def output(self):
        path = (self.root / self.current["output"]).resolve()
        if path.parent != self.root / "evidence/energy-v2" or not path.name.startswith(
            "live-journey-"
        ):
            raise ValueError("Invalid saved run location")
        return path

    def active(self):
        if not self.current:
            return False
        if self.process is not None:
            return self.process.poll() is None
        folder = self.output()
        result = read(folder / "operator-result.json")
        if recovered_session(folder) or (
            valid_result(result)
            and result.get("session", {}).get("cleanup_verified") is True
            and result["status"]
            in ("arrived", "preview_complete", "stopped_incomplete", "failed_or_incomplete")
        ):
            # A completed owner's PID may later belong to an unrelated process.
            return False
        return alive(self.current["pid"])

    def start(
        self,
        trip_id,
        services,
        speed=1,
        *,
        charge_override=None,
        charging_operator=False,
        charging_startup_delay_working_s=0,
    ):
        with self.lock:
            if self.session_metadata_error:
                raise ValueError(self.session_metadata_error)
            if self.closing or self.active() or self.recovering():
                raise ValueError("A run is already active or the launcher is closing")
            if self.current and self.status()["run"]["state"] == "cleanup_unverified":
                raise ValueError("The previous run needs cleanup review before another start")
            profile = select(
                self.trips,
                trip_id,
                services,
                speed,
                charge_override=charge_override,
                charging_operator=charging_operator,
                charging_startup_delay_working_s=charging_startup_delay_working_s,
            )
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
            profile_path = self.directory / (stamp + ".json")
            write(profile_path, profile)
            output = self.root / "evidence/energy-v2" / ("live-journey-" + stamp)
            trip_control_key = urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
            # A fixed executable and allowlisted arguments; browser input never becomes shell code.
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            command = [
                str(executable),
                "scripts/geographic/live.py",
                "--profile",
                profile_path.relative_to(self.root).as_posix(),
                "--output",
                output.relative_to(self.root).as_posix(),
                "--no-browser",
            ]
            log_path = self.directory / (stamp + ".log")
            with log_path.open("xb") as log:
                self.process = self.spawn(
                    command,
                    cwd=self.root,
                    stdout=log,
                    stderr=log,
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                    )
                    if os.name == "nt"
                    else 0,
                )
            self.current = {
                "id": stamp,
                "trip_id": trip_id,
                "title": profile["title"],
                "services": ["monitoring", *services],
                "time_scale": profile.get("time_scale", 1),
                "charging_operator": profile.get("moving_charge_mode") == OPERATOR_MODE,
                **{
                    name: profile[name]
                    for name in (
                        "starting_charge_percent",
                        "initial_charge_percent",
                        "charging_startup_delay_working_s",
                    )
                    if name in profile
                },
                "pid": self.process.pid,
                "output": output.relative_to(self.root).as_posix(),
                "log": log_path.relative_to(self.root).as_posix(),
                "created": time.time(),
                "stop_requested": False,
                "trip_control_key": trip_control_key,
            }
            write(self.directory / "current.json", self.current)
            self.history.remember(self.current)
            return self.status()

    def recovering(self):
        return self.recovery_process is not None and self.recovery_process.poll() is None

    def recover(self, identity):
        with self.lock:
            if (
                self.closing
                or self.active()
                or self.recovering()
                or not self.current
                or self.current["id"] != identity
                or self.status()["run"]["state"] != "cleanup_unverified"
            ):
                raise ValueError("Select the interrupted trip that still needs cleanup.")
            folder = self.history.folder(identity)
            if folder != self.output():
                raise ValueError("The interrupted trip no longer matches its saved results.")
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            with (folder / "cleanup-recovery.log").open("ab") as log:
                self.recovery_process = self.spawn(
                    [str(executable), "scripts/launcher/recover_run.py", identity],
                    cwd=self.root,
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            return self.status()

    def stop(self, identity=None):
        with self.lock:
            if identity is not None and (not self.current or self.current["id"] != identity):
                raise ValueError("This stop confirmation belongs to a different trip")
            if not self.current or not self.active():
                return self.status()
            self.current["stop_requested"] = True
            write(self.directory / "current.json", self.current)
            # An early request is retried by tick() once the worker creates its output folder.
            self.tick()
            return self.status()

    def tick(self):
        with self.lock:
            if self.current and self.current["stop_requested"] and self.active():
                folder = self.output()
                if folder.exists():
                    (folder / "stop").touch()

    def pause_state(self, folder, active, state):
        request = read(folder / "control/requests/pause.json", {})
        confirmation = read(folder / "control/status/pause.json", {})
        revision = request.get("revision", 0)
        pending = revision != confirmation.get("revision", 0)
        return {
            "available": (
                active
                and state == "running"
                and (folder / "startup-check.json").is_file()
                and (folder / "control/requests").is_dir()
                and not (folder / "driving-ended").exists()
            ),
            "revision": revision,
            "pending": pending,
            "requested_paused": request.get("paused", False),
            "paused": confirmation.get("paused", False),
            "working_s": confirmation.get("sim_time_s"),
            "confirmation_delayed": pending and time.time() - request.get("created", 0) > 10,
        }

    def set_paused(self, identity, revision, paused):
        with self.lock:
            run = self.status()["run"]
            if not run or run["id"] != identity or not run["pause"]["available"]:
                raise ValueError("Pause and resume are available during the active drive")
            state = run["pause"]
            if state["revision"] != revision or state["pending"]:
                raise ValueError("The source state changed. Wait for its confirmation.")
            if paused != state["paused"]:
                write(
                    self.output() / "control/requests/pause.json",
                    {"revision": revision + 1, "paused": paused, "created": time.time()},
                )
            return self.status()

    def status(self):
        with self.lock:
            value = {
                "trips": public(self.trips),
                "run": None,
                "closing": self.closing,
                "session_metadata_error": self.session_metadata_error,
            }
            if not self.current:
                return value
            folder = self.output()
            result_path = folder / "operator-result.json"
            raw_result = read(result_path)
            malformed_result = result_path.exists() and not valid_result(raw_result)
            result = raw_result if valid_result(raw_result) else None
            stage = read(
                folder / "stage.json",
                {"stage": "starting", "message": "Validating the saved preset and source."},
            )
            if not isinstance(stage, dict) or not all(
                isinstance(stage.get(key), str) for key in ("stage", "message")
            ):
                stage = {"stage": "starting", "message": "The session status is unavailable."}
            active = self.active()
            state, message = stage["stage"], stage["message"]
            session = (result or {}).get("session", {})
            recovery = recovered_session(folder) if not active else {}
            saved = recording_saved(folder, session, recovery)
            cleanup = None
            error = None
            if active and self.current["stop_requested"]:
                state, message = (
                    "stopping",
                    "Stopping the bus, saving its recording, and cleaning up. Allow up to three minutes.",
                )
            elif not active:
                if recovery:
                    cleanup = True
                    state = "failed_or_incomplete"
                    message = (
                        "Interrupted trip recording saved. Cleanup verified. You can start a new trip."
                        if saved
                        else "Interrupted trip data retained. Cleanup verified. You can start a new trip."
                    )
                elif malformed_result:
                    state, cleanup = "cleanup_unverified", False
                    message = "Saved session metadata needs review before another trip can start."
                    error = "The result cannot verify cleanup or a saved recording."
                elif result:
                    cleanup = session.get("cleanup_verified")
                    state = result["status"]
                    error = result.get("error") or session.get("error")
                    if state == "stopped_incomplete":
                        error = None
                    if (session or result.get("worker_started")) and not cleanup:
                        state = "cleanup_unverified"
                    elif session.get("finalization_errors") or result.get("sleep_release_error"):
                        state = "failed_or_incomplete"
                        error = str(
                            session.get("finalization_errors") or result["sleep_release_error"]
                        )
                    elif failure := (result.get("runtime_host_guard") or {}).get("failure"):
                        state = "failed_or_incomplete"
                        error = failure["reason"]
                    elif state == "arrived" and not saved:
                        state = "failed_or_incomplete"
                        error = "The saved recording is missing or empty."
                    message = {
                        "arrived": "Trip complete. Recording saved and services stopped.",
                        "stopped_incomplete": "Stopped early. Partial recording retained.",
                        "blocked_by_host_preflight": "Startup blocked by the host checks. No bus started.",
                        "cleanup_unverified": "Cleanup was not verified. Review this run before starting another.",
                    }.get(
                        state, "The run did not complete. Review its result before starting again."
                    )
                    if state == "failed_or_incomplete" and (
                        session.get("status") == "arrived"
                        or read(folder / "arrived.json", {}).get("status") == "arrived"
                    ):
                        message = (
                            "Trip complete. Recording saved. Review the session warning."
                            if saved
                            else "Trip complete. The saved recording could not be verified."
                        )
                else:
                    state = "cleanup_unverified" if folder.exists() else "failed_before_start"
                    message = "The runner exited before writing its result. See the launcher log."
                    log = (self.root / self.current["log"]).resolve()
                    if log.parent == self.directory.resolve() and log.is_file():
                        with log.open("rb") as handle:
                            handle.seek(max(0, log.stat().st_size - 2048))
                            lines = (
                                handle.read().decode("utf-8", errors="replace").strip().splitlines()
                            )
                        error = lines[-1] if lines else None
                if self.recovering():
                    state = "recovering_cleanup"
                    message = (
                        "Recovering the recording and releasing the interrupted trip's services."
                    )
                    error = None
                elif state == "cleanup_unverified":
                    failure = (result or {}).get("runtime_host_guard") or {}
                    reason = (failure.get("failure") or {}).get("reason")
                    if reason == "Host-memory sample coverage gap":
                        error = "Windows stopped reporting memory samples during this trip."
                    if self.recovery_process is not None and self.recovery_process.returncode:
                        message = "Recovery could not verify cleanup. Review cleanup-recovery.log and retry."
            ready = (
                read(folder / "ready.json", {})
                if active and state not in ("stopping", "checking", "building")
                else {}
            )
            if not isinstance(ready, dict):
                ready = {}
            charging_operator = self.current.get("charging_operator", False)
            charging_operator_ready = self.operator_ready(folder, active, state, ready)
            # Each run offers one dashboard sign-in role. Fetch the approval key on demand.
            access_keys = []
            if charging_operator_ready:
                access_keys.append("monitoring_url")
            elif not charging_operator:
                access_keys.extend(("monitoring_url", "monitoring_token"))
            access = {key: ready[key] for key in access_keys if key in ready}
            if (
                ready.get("run_id")
                and access.get("monitoring_url") == "http://127.0.0.1:18765"
                and self.current.get("trip_control_key")
            ):
                access["dashboard_url"] = (
                    access["monitoring_url"] + "/#trip-control=" + self.current["trip_control_key"]
                )
            value["run"] = {
                key: self.current[key]
                for key in ("id", "trip_id", "title", "services", "created", "output", "log")
            }
            value["run"].update(
                time_scale=self.current.get("time_scale", 1),
                charging_operator=charging_operator,
                charging_operator_ready=charging_operator_ready,
                charging_startup_delay_working_s=self.current.get(
                    "charging_startup_delay_working_s", 0
                ),
                starting_charge_percent=self.current.get("starting_charge_percent"),
                initial_charge_percent=self.current.get("initial_charge_percent"),
                state=state,
                message=message,
                active=active,
                cleanup_verified=cleanup,
                cleanup_recovered=bool(recovery),
                error=error,
                access=access,
                pause=self.pause_state(folder, active, state),
                recording_saved=saved,
            )
            return value

    def operator_ready(self, folder, active, state, ready):
        return bool(
            active
            and state == "running"
            and self.current.get("charging_operator")
            and (folder / "startup-check.json").is_file()
            and not (folder / "driving-ended").exists()
            and ready.get("run_id")
            and ready.get("charging_operator_token")
        )

    def charging_operator_key(self, identity):
        with self.lock:
            run = self.status()["run"]
            if (
                not run
                or run["id"] != identity
                or not run["charging_operator_ready"]
                or self.closing
            ):
                raise ValueError("Operator connection is available during its active drive")
            ready = read(self.output() / "ready.json", {})
            key = ready.get("charging_operator_token")
            if not isinstance(key, str) or not key:
                raise ValueError("The charging operator credential is unavailable")
            return {"charging_operator_token": key}

    def dashboard_control(self, token, request=None):
        with self.lock:
            key = (self.current or {}).get("trip_control_key")
            if not key or not hmac.compare_digest(token.encode(), key.encode()):
                return None
            run = self.status()["run"]
            if not run["active"]:
                return None
            ready = read(self.output() / "ready.json", {})
            if not ready.get("run_id"):
                return None
            if request is not None:
                run = self.set_paused(run["id"], request["revision"], request["paused"])["run"]
            return {"run_id": ready["run_id"], "asset_id": "bus-001", "pause": run["pause"]}

    def dashboard_replay(self, token):
        """Exchange the active run's dashboard credential for read-only replay access."""
        with self.lock:
            if self.closing or not self.active() or "replay" not in self.current["services"]:
                return None
            run = self.status()["run"]
            if run["state"] not in ("running", "history"):
                return None
            ready = read(self.output() / "ready.json", {})
            key = ready.get(
                "charging_operator_token" if run["charging_operator"] else "monitoring_token"
            )
            replay = ready.get("replay_token")
            if (
                not isinstance(key, str)
                or not key
                or not hmac.compare_digest(token.encode(), key.encode())
                or not isinstance(replay, str)
                or not replay
                or ready.get("replay_url") != "http://127.0.0.1:18766"
            ):
                return None
            return {"url": ready["replay_url"], "access": replay}

    def history_page(self, **options):
        with self.lock:
            return self.history.page(current=self.status()["run"], **options)

    def open_history_folder(self, identity):
        with self.lock:
            return self.history.open_folder(identity)

    def open_dashboard(self, identity):
        """Open only the current run's fixed monitoring endpoint, once per run."""
        with self.lock:
            run = self.status()["run"]
            if not run or identity != run["id"] or not run["active"] or self.closing:
                raise ValueError("The selected session is no longer active")
            if run["charging_operator"] and not run["charging_operator_ready"]:
                raise ValueError("The dashboard key with charging approval is not available yet.")
            access = run["access"]
            key = (
                self.charging_operator_key(identity)["charging_operator_token"]
                if run["charging_operator_ready"]
                else access.get("monitoring_token")
            )
            url = access.get("monitoring_url")
            if url != "http://127.0.0.1:18765" or not isinstance(key, str) or not key:
                raise ValueError("The monitoring dashboard is not ready")
            if self.current.get("dashboard_opened"):
                return {"opened": True, "already_opened": True, "key_copied": False}
            copied = self.copy_key(key)
            try:
                opened = self.open_browser(access.get("dashboard_url", url))
            except (OSError, webbrowser.Error):
                opened = False
            if not opened:
                raise ValueError("The browser could not open. Use Copy key & open dashboard below.")
            self.current["dashboard_opened"] = True
            write(self.directory / "current.json", self.current)
            return {"opened": True, "already_opened": False, "key_copied": copied}
