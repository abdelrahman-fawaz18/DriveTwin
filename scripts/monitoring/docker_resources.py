"""Project-scoped, read-only Docker metadata over one local HTTP connection."""

import http.client
import json
import re
import socket
from urllib.parse import urlencode

MAXIMUM_RESPONSE_BYTES = 16 * 1024 * 1024
CLIENT_API = (1, 56)


class ContainerDisappeared(ValueError):
    """An owned container from the current inventory was removed before inspection."""


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", value):
        raise ValueError("Invalid Docker API version")
    return tuple(map(int, value.split(".")))


class LocalConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=2)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class DockerReadClient:
    def __init__(self, project, socket_path="/var/run/docker.sock"):
        if not re.fullmatch(r"evdt-e2[bc]-[a-f0-9]{12}", project):
            raise ValueError("Require an exact measurement project")
        self.project = project
        self.connection = LocalConnection(socket_path)
        self.known_ids = set()
        try:
            server = self._get("/version")
            selected = min(CLIENT_API, version(server["ApiVersion"]))
            if version(server["MinAPIVersion"]) > selected:
                raise ValueError("Docker API versions do not overlap")
            self.api_version = ".".join(map(str, selected))
            self.prefix = "/v" + self.api_version
        except BaseException:
            self.close()
            raise

    def _get(self, path, *, inspecting_container=False):
        for attempt in range(2):
            try:
                self.connection.request("GET", path)
                response = self.connection.getresponse()
                try:
                    payload = response.read(MAXIMUM_RESPONSE_BYTES + 1)
                    if response.status == 404 and inspecting_container:
                        raise ContainerDisappeared("Listed Docker container disappeared")
                    if response.status != 200:
                        raise ValueError("Docker metadata HTTP status " + str(response.status))
                    if len(payload) > MAXIMUM_RESPONSE_BYTES:
                        raise ValueError("Docker metadata response exceeds its bound")
                    return json.loads(payload)
                finally:
                    response.close()
            except (OSError, http.client.HTTPException):
                self.connection.close()
                if attempt:
                    raise
        raise RuntimeError("Docker metadata request failed")

    def containers(self):
        labels = ["com.docker.compose.project=" + self.project, "com.docker.compose.oneoff=False"]
        query = urlencode(
            {"all": "1", "filters": json.dumps({"label": labels}, separators=(",", ":"))}
        )
        rows = self._get(self.prefix + "/containers/json?" + query)
        if not isinstance(rows, list):
            raise ValueError("Invalid Docker container list")
        ids = []
        for row in rows:
            labels = row["Labels"]
            identity = row["Id"]
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[a-f0-9]{64}", identity)
                or labels.get("com.docker.compose.project") != self.project
                or labels.get("com.docker.compose.oneoff") != "False"
                or identity in ids
            ):
                raise ValueError("Docker container list ownership differs")
            ids.append(identity)
        self.known_ids = set(ids)
        return ids

    def inspect(self, ids):
        if len(ids) != len(set(ids)) or not set(ids) <= self.known_ids:
            raise ValueError("Refusing unknown or repeated container inspection")
        result = []
        for identity in ids:
            row = self._get(
                self.prefix + "/containers/" + identity + "/json", inspecting_container=True
            )
            labels = row["Config"]["Labels"]
            if row["Id"] != identity or labels.get("com.docker.compose.project") != self.project:
                raise ValueError("Container inspection identity coverage differs")
            state = row["State"]
            if (
                type(state["Running"]) is not bool
                or type(state["OOMKilled"]) is not bool
                or type(state["Pid"]) is not int
                or state["Pid"] < 0
                or type(state["ExitCode"]) is not int
                or type(row["RestartCount"]) is not int
                or row["RestartCount"] < 0
                or not isinstance(state["StartedAt"], str)
                or not isinstance(labels.get("com.docker.compose.service"), str)
            ):
                raise ValueError("Invalid container state metadata")
            result.append(
                {
                    "Id": identity,
                    "State": state,
                    "RestartCount": row["RestartCount"],
                    "service": labels["com.docker.compose.service"],
                }
            )
        return result

    def close(self):
        self.connection.close()
