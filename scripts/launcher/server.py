"""Loopback-only launch API. No shell, arbitrary paths, or monitoring-token control."""

import hmac
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from catalog import ROOT, preview_map, starting_charge_percent

API_VERSION = 16
REPLAY_PATH = "/api/dashboard-replay"
DASHBOARD_ORIGIN = "http://127.0.0.1:18765"
TRIP_CONTROL_PATH = "/api/dashboard-trip-control"

ASSETS = {
    "/": ("apps/launcher/index.html", "text/html; charset=utf-8"),
    "/app.js": ("apps/launcher/app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("apps/launcher/style.css", "text/css; charset=utf-8"),
    "/loading.css": ("apps/dashboard/src/ui/loading.css", "text/css; charset=utf-8"),
    "/trip-map.js": ("apps/launcher/trip-map.js", "text/javascript; charset=utf-8"),
    "/route-layers.js": (
        "apps/dashboard/src/features/map/routeLayers.js",
        "text/javascript; charset=utf-8",
    ),
    "/leaflet.js": (
        "apps/dashboard/node_modules/leaflet/dist/leaflet.js",
        "text/javascript; charset=utf-8",
    ),
    "/leaflet.css": (
        "apps/dashboard/node_modules/leaflet/dist/leaflet.css",
        "text/css; charset=utf-8",
    ),
    "/leaflet-license": (
        "apps/dashboard/node_modules/leaflet/LICENSE",
        "text/plain; charset=utf-8",
    ),
    "/font.woff2": ("apps/dashboard/src/ui/assets/InterVariable.woff2", "font/woff2"),
    "/font-license": (
        "apps/dashboard/src/ui/assets/Inter-LICENSE.txt",
        "text/plain; charset=utf-8",
    ),
}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, manager, token, port=18760):
        self.manager, self.token = manager, token
        self.map_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def status(self, value=None):
        return {
            **(self.manager.status() if value is None else value),
            "launcher_api_version": API_VERSION,
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "DriveTwin"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass  # No bearer credentials or request URLs in application logs.

    def respond(self, status, body, kind="application/json", *, cors=False):
        raw = (
            b""
            if status == 204
            else json.dumps(body, ensure_ascii=False).encode()
            if kind == "application/json"
            else body
        )
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if cors:
            self.send_header("Access-Control-Allow-Origin", DASHBOARD_ORIGIN)
            self.send_header("Vary", "Origin")
            if status == 204:
                self.send_header("Access-Control-Allow-Methods", "GET, POST")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        try:
            self.end_headers()
            self.wfile.write(raw)
        except ConnectionError:
            self.close_connection = True

    def admission(self, mutation=False):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            self.respond(403, {"error": "Invalid local host"})
            return False
        origin = self.headers.get("Origin")
        if (mutation and origin != self.server.origin) or (origin and origin != self.server.origin):
            self.respond(403, {"error": "Only the local launch page can make this request"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.respond(403, {"error": "Cross-site requests are not accepted"})
            return False
        expected = "Bearer " + self.server.token
        if not hmac.compare_digest(
            self.headers.get("Authorization", "").encode(), expected.encode()
        ):
            self.respond(401, {"error": "Open DriveTwin from its launcher to reconnect"})
            return False
        return True

    def do_GET(self):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            self.respond(403, {"error": "Invalid local host"})
            return
        if self.path == TRIP_CONTROL_PATH:
            self.dashboard_control()
        elif self.path == REPLAY_PATH:
            if not self.dashboard_origin():
                self.respond(403, {"error": "Use the local monitoring dashboard"})
                return
            scheme, _, token = self.headers.get("Authorization", "").partition(" ")
            access = (
                self.server.manager.dashboard_replay(token)
                if scheme == "Bearer" and 1 <= len(token) <= 128
                else None
            )
            if access is None:
                self.respond(
                    403, {"error": "Replay is unavailable for this dashboard session"}, cors=True
                )
            else:
                self.respond(200, access, cors=True)
        elif self.path in ASSETS:
            path, kind = ASSETS[self.path]
            try:
                raw = (ROOT / path).read_bytes()
            except FileNotFoundError:
                self.respond(404, {"error": "Local asset is unavailable"})
            else:
                self.respond(200, raw, kind)
        elif self.path == "/api/status":
            if self.admission():
                self.respond(200, self.server.status())
        elif urlsplit(self.path).path == "/api/map":
            if self.admission():
                try:
                    query = parse_qs(urlsplit(self.path).query, max_num_fields=1)
                    (identity,) = query["trip"]
                    if set(query) != {"trip"}:
                        raise ValueError
                    trip = next(t for t in self.server.manager.trips if t["id"] == identity)
                    # Keep map preparation separate from session control and bound the queue.
                    if not self.server.map_lock.acquire(timeout=25):
                        self.respond(
                            503,
                            {"error": "The street map is busy. Select Reload map to try again."},
                        )
                        return
                    try:
                        value = preview_map(trip)
                    finally:
                        self.server.map_lock.release()
                    self.respond(200, value)
                except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, StopIteration):
                    self.respond(400, {"error": "Street map unavailable for this trip"})
        elif urlsplit(self.path).path == "/api/history":
            if self.admission():
                try:
                    query = parse_qs(
                        urlsplit(self.path).query, keep_blank_values=True, max_num_fields=4
                    )
                    if set(query) - {"offset", "search", "outcome", "refresh"} or any(
                        len(v) != 1 for v in query.values()
                    ):
                        raise ValueError("Invalid history query")
                    self.respond(
                        200,
                        self.server.manager.history_page(
                            offset=int(query.get("offset", ["0"])[0]),
                            search=query.get("search", [""])[0],
                            outcome=query.get("outcome", ["all"])[0],
                            refresh=query.get("refresh", ["0"])[0] == "1",
                        ),
                    )
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    self.respond(400, {"error": str(exc)})
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == TRIP_CONTROL_PATH:
            self.dashboard_control()
            return
        if not self.admission(mutation=True):
            return
        try:
            if (
                self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Type") != "application/json"
            ):
                raise ValueError("Send bounded JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if not 2 <= length <= 4096:
                raise ValueError("Request body must be 2–4096 bytes")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("A JSON object is required")
            path = urlsplit(self.path)
            if path.query or path.fragment:
                raise ValueError("Unexpected request parameters")
            manager = self.server.manager
            if path.path == "/api/start":
                if (
                    not {"trip_id", "services"} <= set(body)
                    or set(body)
                    - {
                        "trip_id",
                        "services",
                        "time_scale",
                        "starting_charge_percent",
                        "charging_operator",
                        "charging_startup_delay_working_s",
                    }
                    or not isinstance(body["trip_id"], str)
                ):
                    raise ValueError("Select a preset and its services")
                settings = (
                    {"charge_override": starting_charge_percent(body["starting_charge_percent"])}
                    if "starting_charge_percent" in body
                    else {}
                )
                if "charging_operator" in body:
                    if type(body["charging_operator"]) is not bool:
                        raise ValueError("Select a supported charging mode")
                    settings["charging_operator"] = body["charging_operator"]
                if "charging_startup_delay_working_s" in body:
                    settings["charging_startup_delay_working_s"] = body[
                        "charging_startup_delay_working_s"
                    ]
                self.respond(
                    202,
                    self.server.status(
                        manager.start(
                            body["trip_id"], body["services"], body.get("time_scale", 1), **settings
                        )
                    ),
                )
            elif path.path == "/api/stop" and set(body) == {"id"}:
                if not isinstance(body["id"], str) or not body["id"]:
                    raise ValueError("Select the current trip to stop")
                self.respond(202, self.server.status(manager.stop(body["id"])))
            elif path.path == "/api/recover" and set(body) == {"id"}:
                if not isinstance(body["id"], str):
                    raise ValueError("Select the interrupted trip.")
                self.respond(202, self.server.status(manager.recover(body["id"])))
            elif path.path == "/api/pause" and set(body) == {"id", "revision", "paused"}:
                if (
                    not isinstance(body["id"], str)
                    or type(body["revision"]) is not int
                    or body["revision"] < 0
                    or type(body["paused"]) is not bool
                ):
                    raise ValueError("Invalid pause request")
                self.respond(
                    202,
                    self.server.status(
                        manager.set_paused(body["id"], body["revision"], body["paused"])
                    ),
                )
            elif path.path == "/api/open-dashboard" and set(body) == {"id"}:
                self.respond(200, manager.open_dashboard(body["id"]))
            elif path.path == "/api/charging-operator-key" and set(body) == {"id"}:
                if not isinstance(body["id"], str):
                    raise ValueError("Select the active run")
                self.respond(200, manager.charging_operator_key(body["id"]))
            elif path.path == "/api/open-results" and set(body) == {"id"}:
                self.respond(200, manager.open_history_folder(body["id"]))
            elif path.path == "/api/close" and not body:
                with manager.lock:
                    if manager.active() or manager.recovering():
                        raise ValueError(
                            "Stop the active run and wait for cleanup before closing DriveTwin"
                        )
                    manager.closing = True
                self.respond(200, {"closed": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.respond(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.respond(400, {"error": str(exc)})

    def dashboard_origin(self):
        return (
            self.headers.get("Host") == self.server.origin.removeprefix("http://")
            and self.headers.get("Origin") == DASHBOARD_ORIGIN
        )

    def dashboard_control(self):
        if not self.dashboard_origin():
            self.respond(403, {"error": "Use the local monitoring dashboard"})
            return
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme != "Bearer" or not 40 <= len(token) <= 80:
            self.respond(401, {"error": "Trip control access is unavailable"}, cors=True)
            return
        try:
            request = None
            if self.command == "POST":
                if (
                    self.headers.get("Transfer-Encoding")
                    or self.headers.get("Content-Type") != "application/json"
                    or not 2 <= (length := int(self.headers.get("Content-Length", "0"))) <= 256
                ):
                    raise ValueError("Invalid trip control request")
                request = json.loads(self.rfile.read(length))
                if (
                    not isinstance(request, dict)
                    or set(request) != {"revision", "paused"}
                    or type(request["revision"]) is not int
                    or request["revision"] < 0
                    or type(request["paused"]) is not bool
                ):
                    raise ValueError("Invalid trip control request")
            result = self.server.manager.dashboard_control(token, request)
            if result is None:
                self.respond(401, {"error": "Trip control access has ended"}, cors=True)
            else:
                self.respond(200, result, cors=True)
        except (ValueError, OSError):
            self.respond(409, {"error": "Trip state changed. Wait for the next update."}, cors=True)

    def do_OPTIONS(self):
        if self.path in (TRIP_CONTROL_PATH, REPLAY_PATH) and self.dashboard_origin():
            self.respond(204, None, cors=True)
            return
        self.respond(403, {"error": "Cross-origin access is disabled"})
