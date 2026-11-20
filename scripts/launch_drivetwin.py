"""Open the local DriveTwin launch application without starting a bus."""

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from base64 import urlsafe_b64encode
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/launcher"))
from server import API_VERSION, Server  # noqa: E402
from session import Sessions, read, write  # noqa: E402

from catalog import load  # noqa: E402


def reopen(directory, open_browser):
    instance = read(directory / "instance.json", {})
    # Fixed loopback origin: a corrupted local file cannot redirect the launch credential.
    origin = "http://127.0.0.1:18760"
    token = instance.get("token") if isinstance(instance, dict) else None
    if not isinstance(token, str) or not 1 <= len(token) <= 128:
        return False
    try:
        request = Request(
            origin + "/api/status", headers={"Authorization": "Bearer " + token}
        )
        with urlopen(request, timeout=2) as response:
            if response.status != 200:
                return False
            status = json.load(response)
        if not isinstance(status, dict):
            return False
        run = status.get("run")
        if run is not None and (
            not isinstance(run, dict) or type(run.get("active")) is not bool
        ):
            return False
        outdated = status.get("launcher_api_version") != API_VERSION
        if outdated and "run" not in status:
            return False  # Unknown activity is not evidence that closing is safe.
        if outdated and not (run or {}).get("active"):
            # Refresh an idle outdated service through its own guarded shutdown endpoint.
            # A run started concurrently makes /api/close refuse this operation.
            request = Request(
                origin + "/api/close",
                data=b"{}",
                headers={
                    "Authorization": "Bearer " + token,
                    "Origin": origin,
                    "Content-Type": "application/json",
                },
            )
            with urlopen(request, timeout=3) as response:
                closed = json.load(response)
                if not isinstance(closed, dict) or closed.get("closed") is not True:
                    raise RuntimeError("The outdated launcher could not close safely")
            return False
        if open_browser:
            webbrowser.open(origin + "/#" + token)
        return True
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    directory = ROOT / ".runtime/launch-app"
    directory.mkdir(parents=True, exist_ok=True)
    if reopen(directory, not args.no_browser):
        return 0
    manager = Sessions(load())
    token = urlsafe_b64encode(os.urandom(36)).decode()
    for _ in range(25):
        try:
            server = Server(manager, token)
            break
        except OSError:
            # Another opener may be initializing, or the idle old service may be closing.
            if reopen(directory, not args.no_browser):
                return 0
            time.sleep(0.2)
    else:
        raise RuntimeError(
            "Local port 18760 is occupied. See .runtime/launch-app/startup.log; no bus was started."
        ) from None
    write(directory / "instance.json", {"token": token, "pid": os.getpid()})
    stopped = threading.Event()

    def poll():
        while not stopped.wait(0.5):
            manager.tick()

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    if not args.no_browser:
        webbrowser.open(server.origin + "/#" + token)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        manager.stop()
        while manager.active():
            manager.tick()
            time.sleep(0.5)
    finally:
        stopped.set()
        server.server_close()
        instance = read(directory / "instance.json", {})
        if instance.get("pid") == os.getpid():
            (directory / "instance.json").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback

        folder = ROOT / ".runtime/launch-app"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "startup.log").write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt" and Path(sys.executable).name.lower() == "pythonw.exe":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                "DriveTwin could not open. Details are saved in .runtime/launch-app/startup.log inside the project.",
                "DriveTwin",
                0x10,
            )
        raise
